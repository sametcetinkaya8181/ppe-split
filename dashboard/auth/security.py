"""Erişim politikası: güvenlik profili, Host/HTTPS denetimi, oturum doğrulama, rol + kamera kapsamı, CSRF/Origin,
kaba kuvvet sınırı, güvenlik başlıkları ve hız sınırlı denetim kaydı.

Profiller (SECURITY_PROFILE):
  local (varsayılan) : yalnız localhost Host'ları; HTTP; çerez Secure DEĞİL. LAN Host'uyla gelen istek 400 alır,
                       böylece bu profil yanlışlıkla LAN'da kullanılamaz.
  lan                : yalnız ALLOWED_HOSTS; tarayıcı uçları yalnız TRUSTED_PROXIES'ten gelen X-Forwarded-Proto=https
                       ile (TLS ters vekil); çerez __Host- + Secure; HSTS; INGEST_TOKEN zorunlu.
Rol sırası: viewer < supervisor < admin. Kamera kapsamı (camera_scope) None = tüm kameralar."""
import hashlib
import hmac
import ipaddress
import os
import re
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool
from starlette.requests import HTTPConnection

from db.session import get_session

from . import store

PROFILE = os.getenv("SECURITY_PROFILE", "local").strip().lower()
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")
SESSION_IDLE = float(os.getenv("SESSION_IDLE_MINUTES", 30)) * 60
SESSION_ABSOLUTE = float(os.getenv("SESSION_MAX_HOURS", 12)) * 3600
LOGIN_MAX_FAILURES = int(os.getenv("LOGIN_MAX_FAILURES", 5))
LOGIN_LOCK_MINUTES = float(os.getenv("LOGIN_LOCK_MINUTES", 15))
IP_MAX_FAILURES = int(os.getenv("LOGIN_IP_MAX_FAILURES", 20))
IP_WINDOW = 15 * 60
TOUCH_EVERY = 60.0                 # last_seen_at en fazla dakikada bir yazılır
CACHE_TTL = 5.0                    # oturum doğrulama önbelleği (iptal tüm süreçte en geç bu kadar gecikir)
DB_GRACE = float(os.getenv("SESSION_DB_GRACE_SEC", 300))   # DB kesintisinde doğrulanmış oturumun geçerli kalma süresi
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")


def _list(name: str) -> tuple:
    return tuple(x.strip() for x in os.getenv(name, "").split(",") if x.strip())


ALLOWED_HOSTS = tuple(h.lower() for h in _list("ALLOWED_HOSTS")) or (LOCAL_HOSTS if PROFILE == "local" else ())
ALLOWED_ORIGINS = _list("ALLOWED_ORIGINS")
TRUSTED_PROXIES = tuple(ipaddress.ip_network(p, strict=False) for p in _list("TRUSTED_PROXIES"))
COOKIE = "__Host-ppe_session" if PROFILE == "lan" else "ppe_session"


def validate_config(ingest_tokens: tuple) -> list:
    """Başlangıçta çağrılır. Güvensiz LAN yapılandırmasında RuntimeError; döner: uyarılar."""
    if PROFILE not in ("local", "lan"):
        raise RuntimeError("SECURITY_PROFILE 'local' veya 'lan' olmalı")
    if PROFILE == "lan":
        problems = []
        if not ALLOWED_HOSTS:
            problems.append("ALLOWED_HOSTS boş")
        if not TRUSTED_PROXIES:
            problems.append("TRUSTED_PROXIES boş (TLS ters vekil adresi)")
        if not ingest_tokens:
            problems.append("INGEST_TOKEN boş")
        if ALLOWED_HOSTS and all(h in LOCAL_HOSTS for h in ALLOWED_HOSTS):
            problems.append("ALLOWED_HOSTS yalnız localhost içeriyor")
        if problems:
            raise RuntimeError("lan profili güvensiz yapılandırılmış: " + "; ".join(problems))
        return []
    extra = [h for h in ALLOWED_HOSTS if h not in LOCAL_HOSTS and h != "testserver"]
    if extra:
        raise RuntimeError("local profilinde ALLOWED_HOSTS yalnız localhost olabilir; LAN için SECURITY_PROFILE=lan")
    return [] if ingest_tokens else ["INGEST_TOKEN boş: /ingest uçları kimlik doğrulamasız (yalnız local profil)."]


# ---------------------------------------------------------------- istek bağlamı
def peer_ip(conn: HTTPConnection) -> str:
    return conn.client.host if conn.client else ""


def _trusted_peer(conn: HTTPConnection) -> bool:
    try:
        ip = ipaddress.ip_address(peer_ip(conn))
    except ValueError:
        return False
    return any(ip in n for n in TRUSTED_PROXIES)


def client_ip(conn: HTTPConnection) -> str:
    """Güvenilir vekil arkasında X-Forwarded-For'un en sağdaki güvenilmeyen adresi; aksi hâlde TCP eşi."""
    if PROFILE == "lan" and _trusted_peer(conn):
        for hop in reversed([h.strip() for h in conn.headers.get("x-forwarded-for", "").split(",") if h.strip()]):
            try:
                ip = ipaddress.ip_address(hop)
            except ValueError:
                break
            if not any(ip in n for n in TRUSTED_PROXIES):
                return str(ip)
    return peer_ip(conn)


def is_https(conn: HTTPConnection) -> bool:
    if PROFILE == "lan":
        return _trusted_peer(conn) and conn.headers.get("x-forwarded-proto", "").lower() == "https"
    return conn.url.scheme in ("https", "wss")


def host_of(header: str) -> str:
    h = (header or "").strip().lower()
    if h.startswith("["):
        return h[1:h.find("]")] if "]" in h else ""
    return h.rsplit(":", 1)[0] if h.count(":") == 1 else h


def host_allowed(header: str) -> bool:
    return host_of(header) in ALLOWED_HOSTS


def own_origin(conn: HTTPConnection) -> str:
    return ("https" if is_https(conn) else "http") + "://" + conn.headers.get("host", "").lower()


def origin_ok(conn: HTTPConnection) -> bool:
    """Tarayıcıdan gelen isteklerde Origin aynı köken (veya ALLOWED_ORIGINS) olmalı. Origin yoksa tarayıcı dışı
    istemcidir; Sec-Fetch-Site varsa yalnız same-origin/none kabul edilir."""
    origin = conn.headers.get("origin")
    if origin is None:
        return conn.headers.get("sec-fetch-site", "same-origin") in ("same-origin", "none")
    return origin.lower() == own_origin(conn) or origin in ALLOWED_ORIGINS


# ---------------------------------------------------------------- oturum
@dataclass(frozen=True)
class AuthCtx:
    user_id: int
    username: str
    role: str
    scope: tuple | None
    token_hash: str
    csrf: str

    def at_least(self, role: str) -> bool:
        return store.RANK[self.role] >= store.RANK[role]

    def camera_ok(self, camera_id: str) -> bool:
        return self.scope is None or camera_id in self.scope


_CACHE: dict = {}                  # token_hash -> (AuthCtx, cache_until, last_touch, validated_at)
_CACHE_LOCK = threading.Lock()


def invalidate(token_hash: str | None = None, user_id: int | None = None) -> None:
    with _CACHE_LOCK:
        for k in [k for k, v in _CACHE.items() if k == token_hash or v[0].user_id == user_id]:
            del _CACHE[k]


def authenticate(conn: HTTPConnection) -> AuthCtx | None:
    """Çerezdeki oturumu doğrular. DB erişilemezse 503 (oturum yok anlamına gelmez)."""
    token = conn.cookies.get(COOKIE, "")
    if not TOKEN_RE.match(token):
        return None
    th = store.token_hash(token)
    now = time.monotonic()
    with _CACHE_LOCK:
        hit = _CACHE.get(th)
    if hit and hit[1] > now:
        return hit[0]
    try:
        with get_session() as s, s.begin():
            row = store.load_session(s, th, SESSION_IDLE)
            if row is None:
                invalidate(token_hash=th)
                return None
            last_touch = hit[2] if hit else 0.0
            if now - last_touch >= TOUCH_EVERY:
                store.touch_session(s, th)
                last_touch = now
    except (SQLAlchemyError, RuntimeError):
        # DB kesintisi: canlı izleme DB'ye bağımlı olmasın diye, DAHA ÖNCE doğrulanmış oturum sınırlı süre son
        # bilinen yetkisiyle kabul edilir (bu sürede iptal/çıkış denetlenemez). Yeni oturum doğrulanamaz -> 503.
        if hit and now - hit[3] < DB_GRACE:
            return hit[0]
        raise HTTPException(status_code=503, detail="veritabanı şu an kullanılamıyor; tekrar deneyin") from None
    ctx = AuthCtx(row["user_id"], row["username"], row["role"],
                  tuple(row["camera_scope"]) if row["camera_scope"] is not None else None, th, row["csrf_token"])
    with _CACHE_LOCK:
        _CACHE[th] = (ctx, now + CACHE_TTL, last_touch, now)
    return ctx


def csrf_ok(conn: HTTPConnection, ctx: AuthCtx) -> bool:
    sent = conn.headers.get("x-csrf-token", "")
    return bool(sent) and hmac.compare_digest(sent.encode(), ctx.csrf.encode()) and origin_ok(conn)


# ---------------------------------------------------------------- hız sınırları
class IpLimiter:
    """IP başına kayan pencerede başarısız giriş sayısı (süreç içi; tek uvicorn süreci varsayımı)."""

    def __init__(self, limit: int, window: float):
        self.limit, self.window = limit, window
        self.hits: dict = {}
        self.lock = threading.Lock()

    def _prune(self, ip, now):
        q = self.hits.get(ip)
        while q and now - q[0] > self.window:
            q.popleft()
        return q

    def blocked(self, ip: str) -> float:
        """Döner: kalan bekleme sn (0 = serbest)."""
        now = time.monotonic()
        with self.lock:
            q = self._prune(ip, now)
            return (self.window - (now - q[0])) if q and len(q) >= self.limit else 0.0

    def fail(self, ip: str) -> None:
        with self.lock:
            self.hits.setdefault(ip, deque()).append(time.monotonic())

    def clear(self) -> None:
        with self.lock:
            self.hits.clear()


IP_LIMITER = IpLimiter(IP_MAX_FAILURES, IP_WINDOW)
_AUDIT_SEEN: dict = {}
_AUDIT_LOCK = threading.Lock()


def audit(event: str, outcome: str, *, conn: HTTPConnection | None = None, user_id=None, detail=None,
          dedupe: str | None = None, every: float = 60.0) -> None:
    """Denetim kaydı (best effort: DB hatası isteği bozmaz). dedupe anahtarı verilirse 'every' saniyede bir yazılır."""
    if dedupe is not None:
        now = time.monotonic()
        with _AUDIT_LOCK:
            if now - _AUDIT_SEEN.get(dedupe, -every) < every:
                return
            _AUDIT_SEEN[dedupe] = now
            if len(_AUDIT_SEEN) > 10000:
                _AUDIT_SEEN.clear()
    try:
        with get_session() as s, s.begin():
            store.audit(s, event, outcome, user_id=user_id, ip=client_ip(conn) if conn else None, detail=detail)
    except (SQLAlchemyError, RuntimeError):
        pass


def deny(conn: HTTPConnection, status: int, reason: str, ctx: AuthCtx | None = None, required: str | None = None):
    audit("access_denied", "denied", conn=conn, user_id=ctx.user_id if ctx else None,
          detail={"reason": reason, "path": conn.url.path[:120], "required_role": required},
          dedupe=f"deny:{reason}:{client_ip(conn)}:{conn.url.path[:60]}")
    raise HTTPException(status_code=status, detail="oturum gerekli" if status == 401 else "yetkiniz yok")


# ---------------------------------------------------------------- güvenlik başlıkları
def csp_for(index_html: Path) -> str:
    """Satır içi betiğin SHA-256'sı ile CSP (unsafe-inline betik yok)."""
    import base64
    html = index_html.read_text(encoding="utf-8")
    hashes = [base64.b64encode(hashlib.sha256(m.encode("utf-8")).digest()).decode()
              for m in re.findall(r"<script>(.*?)</script>", html, re.S)]
    scripts = " ".join(f"'sha256-{h}'" for h in hashes)
    return ("default-src 'none'; script-src " + scripts + "; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' blob: data:; connect-src 'self'; font-src 'self'; base-uri 'none'; form-action 'self'; "
            "frame-ancestors 'none'; object-src 'none'")


class SecurityMiddleware:
    """Saf ASGI (MJPEG akışını ve WebSocket'i bozmaz): Host denetimi, lan'da HTTPS zorunluluğu, güvenlik başlıkları."""
    MACHINE_PREFIXES = ("/ingest/", "/healthz", "/health/ready")

    def __init__(self, app, csp: str, ingest_tokens=lambda: (), on_ingest_reject=None):
        self.app, self.csp = app, csp.encode()
        self.ingest_tokens, self.on_ingest_reject = ingest_tokens, on_ingest_reject

    def _ingest_ok(self, conn) -> bool:
        """Makine token'ı GÖVDE OKUNMADAN denetlenir: kimliksiz istemci multipart/JSON ayrıştırmayı tetikleyemez
        (python-multipart / Starlette form ayrıştırma DoS sınıflarına karşı savunma)."""
        tokens = [t for t in self.ingest_tokens() if t]
        if not tokens:
            return True
        given = conn.headers.get("x-token", "").encode()
        return any([hmac.compare_digest(given, t.encode()) for t in tokens])

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        conn = HTTPConnection(scope)
        path = scope.get("path", "")
        reject = None
        machine = path.startswith(self.MACHINE_PREFIXES)
        host = conn.headers.get("host", "")
        # makine uçları (aynı makinedeki dedektör, container healthcheck) lan'da da localhost'tan gelebilir
        if not (host_allowed(host) or (machine and host_of(host) in LOCAL_HOSTS)):
            reject = (400, b"gecersiz Host")
        elif PROFILE == "lan" and not machine and not is_https(conn):
            reject = (403, b"HTTPS gerekli")
        if reject:
            if scope["type"] == "websocket":
                return await send({"type": "websocket.close", "code": 1008})
            await send({"type": "http.response.start", "status": reject[0],
                        "headers": [(b"content-type", b"text/plain; charset=utf-8")]})
            return await send({"type": "http.response.body", "body": reject[1]})
        if path.startswith("/ingest/") and scope["type"] == "http" and not self._ingest_ok(conn):
            if self.on_ingest_reject is not None:
                await run_in_threadpool(self.on_ingest_reject, conn)
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"content-type", b"application/json")]})
            return await send({"type": "http.response.body", "body": b'{"detail":"bad token"}'})
        if scope["type"] == "websocket":
            return await self.app(scope, receive, send)
        https = is_https(conn)
        api = path.startswith(("/api/", "/snapshots/", "/video_feed/"))

        async def send_wrapped(msg):
            if msg["type"] == "http.response.start":
                h = [(k, v) for k, v in msg.get("headers", []) if k.lower() != b"cache-control" or not api]
                h += [(b"x-content-type-options", b"nosniff"), (b"x-frame-options", b"DENY"),
                      (b"referrer-policy", b"no-referrer"), (b"cross-origin-opener-policy", b"same-origin"),
                      (b"cross-origin-resource-policy", b"same-origin"),
                      (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
                      (b"content-security-policy", self.csp)]
                if api:
                    h.append((b"cache-control", b"no-store"))
                if PROFILE == "lan" and https:
                    h.append((b"strict-transport-security", b"max-age=31536000"))
                msg = {**msg, "headers": h}
            await send(msg)
        return await self.app(scope, receive, send_wrapped)
