"""FAZ 9 operasyonel metrikler (dashboard). Düşük maliyet: süreç içi sayaçlar + sınırlı kayan pencereler; dış
bağımlılık yok. Kardinalite sınırlı: etiketler yalnız route ŞABLONU (/api/history/incidents/{incident_id}), HTTP
metodu ve durum kodu; event_id/incident_id/kullanıcı/IP hiçbir zaman etiket olmaz. Gizli bilgi, kaynak adresi ve
görüntü içeriği tutulmaz. Dedektörden gelen sağlık özeti (meta.health / meta.outbox) beyaz listeyle temizlenir.

Ayarlar: OPS_METRICS=0 kapatır (uç 404 döner, sayaç tutulmaz); OPS_LATENCY_WINDOW yüzdelik penceresi (istek sayısı).
Sayaçlar süreç yeniden başlayınca sıfırlanır (uptime_sec ile birlikte okunmalı)."""
import os
import shutil
import threading
import time
from collections import defaultdict, deque
from contextlib import contextmanager

ENABLED = os.getenv("OPS_METRICS", "1") == "1"
WINDOW = int(os.getenv("OPS_LATENCY_WINDOW", 2048))
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
STREAM_ROUTES = ("/video_feed/{cid}",)          # uzun yaşayan akış: gecikme penceresine girmez, yalnız sayılır
TRACKED_STATUS = (400, 401, 403, 404, 409, 413, 422, 429, 500, 503)


def percentiles(values, ps=(50, 95, 99)) -> dict:
    xs = sorted(values)
    out = {}
    for p in ps:
        if not xs:
            out[f"p{p}"] = None
            continue
        k = (len(xs) - 1) * p / 100
        f = int(k)
        c = min(f + 1, len(xs) - 1)
        out[f"p{p}"] = round(xs[f] + (xs[c] - xs[f]) * (k - f), 3)
    out["max"] = round(xs[-1], 3) if xs else None
    out["n"] = len(xs)
    return out


class Registry:
    def __init__(self, window: int = WINDOW, clock=time.monotonic):
        self.lock = threading.Lock()
        self.clock = clock
        self.started = clock()
        self.window = window
        self.requests = defaultdict(int)                       # (route, method, status) -> sayı
        self.latency = defaultdict(lambda: deque(maxlen=window))   # route -> ms
        self.status = defaultdict(int)
        self.db_write = defaultdict(lambda: deque(maxlen=window))  # tür -> ms (transaction + commit)
        self.db_write_count = defaultdict(lambda: {"ok": 0, "error": 0})
        self.counters = defaultdict(int)                       # events_received, events_inserted, ...
        self.ws = {"active": 0, "accepted": 0, "rejected": 0, "closed": 0, "frames_sent": 0}

    def observe_http(self, route: str, method: str, status: int, ms: float) -> None:
        method = method if method in METHODS else "OTHER"
        with self.lock:
            self.requests[(route, method, status)] += 1
            self.status[status] += 1
            if route not in STREAM_ROUTES:
                self.latency[route].append(ms)

    @contextmanager
    def db_write_timer(self, kind: str):
        s = time.perf_counter()
        ok = False
        try:
            yield
            ok = True
        finally:
            ms = (time.perf_counter() - s) * 1e3
            with self.lock:
                self.db_write[kind].append(ms)
                self.db_write_count[kind]["ok" if ok else "error"] += 1

    def count(self, key: str, n: int = 1) -> None:
        with self.lock:
            self.counters[key] += n

    def ws_event(self, kind: str, n: int = 1) -> None:
        with self.lock:
            if kind == "accepted":
                self.ws["accepted"] += n
                self.ws["active"] += n
            elif kind == "closed":
                self.ws["closed"] += n
                self.ws["active"] = max(0, self.ws["active"] - n)
            else:
                self.ws[kind] += n

    def snapshot(self) -> dict:
        with self.lock:
            routes: dict = {}
            for (route, method, status), n in self.requests.items():
                r = routes.setdefault(route, {"count": 0, "by_status": {}, "methods": {}})
                r["count"] += n
                r["by_status"][str(status)] = r["by_status"].get(str(status), 0) + n
                r["methods"][method] = r["methods"].get(method, 0) + n
            for route, lat in self.latency.items():
                routes.setdefault(route, {"count": 0, "by_status": {}, "methods": {}})["latency_ms"] = \
                    percentiles(lat)
            ingest = {}
            for route, r in routes.items():
                if route.startswith("/ingest/"):
                    ok = sum(n for s, n in r["by_status"].items() if s.startswith("2"))
                    ingest[route] = {"success": ok, "error": r["count"] - ok}
            history_lat = [v for route, lat in self.latency.items() if route.startswith("/api/history/")
                           for v in lat]
            return {
                "uptime_sec": round(self.clock() - self.started, 1),
                "http": {"routes": routes, "status_counts": {str(k): v for k, v in sorted(self.status.items())},
                         "tracked_status": {str(s): self.status.get(s, 0) for s in TRACKED_STATUS}},
                "ingest": {"by_route": ingest, **{k: v for k, v in self.counters.items()
                                                  if k.startswith(("events_", "reports_", "frames_"))}},
                "history_latency_ms": percentiles(history_lat),
                "db_write": {k: {**percentiles(v), **self.db_write_count[k]} for k, v in self.db_write.items()},
                "websocket": dict(self.ws),
                "counters": dict(self.counters),
            }


REG = Registry()


class OpsMiddleware:
    """Saf ASGI; en dışta çalışır (SecurityMiddleware'in 400/401 retlerini de sayar). Gövdeye dokunmaz."""

    def __init__(self, app, registry: Registry = REG, static_routes=()):
        self.app, self.reg = app, registry
        self.static_routes = set(static_routes)

    def label(self, scope) -> str:
        route = scope.get("route")
        path = getattr(route, "path", None)
        if path:
            return path
        p = scope.get("path", "")
        return p if p in self.static_routes else "other"     # eşleşmeyen yol etiket üretmez (kardinalite)

    async def __call__(self, scope, receive, send):
        if not ENABLED or scope["type"] != "http":
            return await self.app(scope, receive, send)
        start = time.perf_counter()
        box = {"status": 500}

        async def send_wrapped(msg):
            if msg["type"] == "http.response.start":
                box["status"] = msg["status"]
            await send(msg)
        try:
            await self.app(scope, receive, send_wrapped)
        finally:
            self.reg.observe_http(self.label(scope), scope.get("method", ""), box["status"],
                                  (time.perf_counter() - start) * 1e3)


# ---------------------------------------------------------------- dedektör sağlık özeti (beyaz liste)
STATES = {"starting", "connected", "disconnected", "reconnecting", "ended", "stopped"}
FLAGS = {"camera_disconnected", "no_signal", "frozen", "stalled", "lagging", "unevaluated_persons",
         "unevaluated_long"}
REASONS = {"warmup", "low_conf", "too_small", "edge"}
OUTBOX_STATES = {"ok", "backoff", "paused_auth", "paused_incompatible"}
OUTBOX_WARNINGS = {"high_watermark", "pending_age", "dead_letters", "quarantine", "disk_write_errors",
                   "disk_free_low", "memory_fallback", "delivery_paused", "messages_lost", "outbox_unreadable"}
_NUM_HEALTH = ("uptime_sec", "proc_fps", "source_fps", "vid_stride", "lag_sec", "last_frame_age_sec", "frame_ms_p95",
               "inference_ms_p95", "reconnects")
_NUM_COVERAGE = ("visible", "decided", "analyzing", "stale_decision", "track_frames", "track_frames_unevaluated",
                 "track_frames_analyzing", "tracks_closed_never_evaluated", "never_evaluated_max_sec",
                 "open_unevaluated_long", "open_unevaluated_max_sec", "tracks_seen")
_NUM_OUTBOX = ("pending_count", "pending_bytes", "oldest_pending_age_sec", "dead_letter_count", "quarantine_count",
               "replay_success_count", "capacity_used_ratio")


def _num(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or abs(v) > 1e12:
        return None
    return round(v, 3) if isinstance(v, float) else v


def _vocab(values, allowed) -> list:
    return [v for v in values if isinstance(v, str) and v in allowed][:16] if isinstance(values, list) else []


def sanitize_health(h) -> dict | None:
    if not isinstance(h, dict):
        return None
    out = {k: _num(h.get(k)) for k in _NUM_HEALTH}
    out["state"] = h.get("state") if h.get("state") in STATES else None
    out["flags"] = _vocab(h.get("flags"), FLAGS)
    cov = h.get("coverage") if isinstance(h.get("coverage"), dict) else {}
    out["coverage"] = {k: _num(cov.get(k)) for k in _NUM_COVERAGE}
    by = cov.get("analyzing_by_reason") if isinstance(cov.get("analyzing_by_reason"), dict) else {}
    out["coverage"]["analyzing_by_reason"] = {k: _num(v) for k, v in by.items() if k in REASONS}
    cum = cov.get("unevaluated_by_reason") if isinstance(cov.get("unevaluated_by_reason"), dict) else {}
    out["coverage"]["unevaluated_by_reason"] = {k: _num(v) for k, v in cum.items() if k in REASONS}
    return out


def sanitize_outbox(o) -> dict | None:
    if not isinstance(o, dict):
        return None
    out = {k: _num(o.get(k)) for k in _NUM_OUTBOX}
    out["state"] = o.get("state") if o.get("state") in OUTBOX_STATES else None
    out["warnings"] = _vocab(o.get("warnings"), OUTBOX_WARNINGS)
    return out


# ---------------------------------------------------------------- PostgreSQL / depolama (önbellekli)
class Cached:
    def __init__(self, fn, ttl: float):
        self.fn, self.ttl = fn, ttl
        self.value, self.at = None, -1e9
        self.lock = threading.Lock()

    def get(self):
        with self.lock:
            if time.monotonic() - self.at >= self.ttl:
                self.value, self.at = self.fn(), time.monotonic()
            return self.value


def storage_status(path) -> dict:
    """Snapshot deposu: yazılabilirlik, disk boş/toplam alan, dosya sayısı ve toplam boyut (dosya adları yok)."""
    out = {"writable": os.access(path, os.W_OK), "disk_free_bytes": None, "disk_total_bytes": None,
           "files": None, "bytes": None}
    try:
        du = shutil.disk_usage(path)
        out["disk_free_bytes"], out["disk_total_bytes"] = du.free, du.total
        n = b = 0
        with os.scandir(path) as it:
            for e in it:
                if e.is_file(follow_symlinks=False):
                    n += 1
                    b += e.stat(follow_symlinks=False).st_size
        out["files"], out["bytes"] = n, b
    except OSError:
        out["error"] = "unavailable"
    return out


def db_status(check_ready, engine_fn) -> dict:
    s = time.perf_counter()
    ok, detail = check_ready()
    out = {"ready": ok, "db": detail.get("db"), "schema": detail.get("schema"),
           "check_ms": round((time.perf_counter() - s) * 1e3, 2)}
    try:
        pool = engine_fn().pool
        out["pool"] = {"size": pool.size(), "checked_out": pool.checkedout(), "overflow": pool.overflow()}
    except Exception:                                  # noqa: BLE001 — yapılandırma eksikse havuz yok
        out["pool"] = None
    return out
