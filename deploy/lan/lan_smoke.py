"""LAN TLS ters vekil smoke testi — İZOLE. Ana DB'ye, ana dashboard'a ve LAN'a dokunmaz.

    python deploy/lan/lan_smoke.py

Kurulum: ayrı ağ ppe-lantest (172.31.251.0/28), lan profilli test dashboard'u (ppe_lan_test DB), Caddy (yerel test
CA, `tls internal`) yalnız 127.0.0.1:8443/8080'de. Kontroller GERÇEK TLS üzerinden yapılır; sonunda her şey silinir.
Not: yerel test CA'sı üretim sertifikası değildir; bu test yalnız yapılandırmanın davranışını doğrular."""
import base64
import http.client
import json
import os
import secrets
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NET, SUBNET, CADDY_IP, DASH_IP = "ppe-lantest", "172.31.251.0/28", "172.31.251.10", "172.31.251.11"
SITE, PORT, HPORT = "ppe.lan.test", 8443, 8080
HOST = f"{SITE}:{PORT}"
ORIGIN = f"https://{HOST}"
TOKEN, PASS = "lan-smoke-" + secrets.token_hex(8), secrets.token_urlsafe(18)
results = []


def sh(*cmd, check=True, **kw):
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, **kw)
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:4])}: {r.stderr.strip()[-300:]}")
    return r


def check(name, cond, detail=""):
    results.append(("PASS" if cond else "FAIL", name, "" if cond else str(detail)[:200]))


class Conn(http.client.HTTPSConnection):
    def connect(self):
        sock = socket.create_connection(("127.0.0.1", PORT), 10)
        self.sock = self._context.wrap_socket(sock, server_hostname=SITE)


def req(ctx, method, path, headers=None, body=None):
    c = Conn(SITE, PORT, context=ctx, timeout=10)
    c.request(method, path, body=body, headers={"Host": HOST, **(headers or {})})
    r = c.getresponse()
    data = r.read()
    c.close()
    return r, data


def ws_open(ctx, cookie, origin):
    raw = socket.create_connection(("127.0.0.1", PORT), 10)
    s = ctx.wrap_socket(raw, server_hostname=SITE)
    key = base64.b64encode(os.urandom(16)).decode()
    s.sendall((f"GET /ws HTTP/1.1\r\nHost: {HOST}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\nOrigin: {origin}\r\n"
               f"Cookie: {cookie}\r\n\r\n").encode())
    head = b""
    while b"\r\n\r\n" not in head:
        chunk = s.recv(1)
        if not chunk:
            break
        head += chunk
    return s, head.split(b"\r\n", 1)[0].decode()


def ws_frame(s):
    def n(k):
        b = b""
        while len(b) < k:
            b += s.recv(k - len(b))
        return b
    b0, b1 = n(2)
    ln = b1 & 0x7F
    ln = int.from_bytes(n(2), "big") if ln == 126 else int.from_bytes(n(8), "big") if ln == 127 else ln
    return b0 & 0x0F, n(ln)


def multipart(payload):
    b = uuid.uuid4().hex
    body = (f"--{b}\r\nContent-Disposition: form-data; name=\"frame\"; filename=\"f.jpg\"\r\n"
            f"Content-Type: image/jpeg\r\n\r\n").encode() + b"\xff\xd8LANSMOKE\xff\xd9" + (
        f"\r\n--{b}\r\nContent-Disposition: form-data; name=\"payload\"\r\n\r\n{json.dumps(payload)}\r\n--{b}--\r\n"
    ).encode()
    return body, f"multipart/form-data; boundary={b}"


def main():
    sh("docker", "compose", "exec", "-T", "db", "pg_isready", "-q")
    try:
        sh("docker", "network", "create", "--subnet", SUBNET, NET)
        sh("docker", "compose", "--profile", "test", "run", "-d", "--name", "ppe-lantest-dash",
           "-e", "SECURITY_PROFILE=lan", "-e", f"ALLOWED_HOSTS={SITE}", "-e", f"TRUSTED_PROXIES={CADDY_IP}/32",
           "-e", f"INGEST_TOKEN={TOKEN}", "-e", "PASSWORD_HASH_PROFILE=default", "-e", f"LAN_SMOKE_PASS={PASS}",
           "-e", "DATA_DIR=/tmp/lan", "tests", "sh", "-c",
           "pip install -q --root-user-action=ignore --disable-pip-version-check -r requirements-dev.txt "
           "&& python -m tests.lan_server")
        sh("docker", "network", "connect", "--ip", DASH_IP, NET, "ppe-lantest-dash")
        caddyfile = (ROOT / "deploy" / "lan" / "Caddyfile").as_posix()
        sh("docker", "run", "-d", "--name", "ppe-lantest-caddy", "--network", NET, "--ip", CADDY_IP,
           "-p", f"127.0.0.1:{PORT}:443", "-p", f"127.0.0.1:{HPORT}:80", "-e", f"PPE_SITE={SITE}",
           "-e", f"PPE_UPSTREAM={DASH_IP}:8000", "-v", f"{caddyfile}:/etc/caddy/Caddyfile:ro",
           "--read-only", "--tmpfs", "/tmp", "--tmpfs", "/config", "--tmpfs", "/data",
           "--cap-drop", "ALL", "--cap-add", "NET_BIND_SERVICE", "--security-opt", "no-new-privileges:true",
           "caddy:2.8.4-alpine")
        ca = Path(tempfile.mkdtemp()) / "root.crt"
        for _ in range(180):                         # /data tmpfs: docker cp okuyamaz, exec ile okunur
            pem = sh("docker", "exec", "ppe-lantest-caddy", "cat", "/data/caddy/pki/authorities/local/root.crt",
                     check=False).stdout
            up = sh("docker", "exec", "ppe-lantest-dash", "python", "-c", "import urllib.request; urllib.request"
                    ".urlopen('http://localhost:8000/healthz', timeout=2)", check=False).returncode == 0
            if "BEGIN CERTIFICATE" in pem and up:
                ca.write_text(pem, encoding="ascii")
                break
            time.sleep(1)
        else:
            raise RuntimeError("Caddy CA veya LAN test dashboard'u hazır olmadı")
        ctx = ssl.create_default_context(cafile=str(ca))
        time.sleep(2)
        run_checks(ctx)
    finally:
        sh("docker", "rm", "-f", "ppe-lantest-caddy", "ppe-lantest-dash", check=False)
        sh("docker", "network", "rm", NET, check=False)
        sh("docker", "compose", "exec", "-T", "db", "sh", "-c",
           'psql -q -U "$POSTGRES_USER" -d postgres -c "DROP DATABASE IF EXISTS ppe_lan_test WITH (FORCE)"',
           check=False)
    for st, n, d in results:
        print(f"[{st}] {n}" + (f" — {d}" if d else ""))
    failed = sum(1 for r in results if r[0] == "FAIL")
    print(f"SONUÇ: {len(results) - failed}/{len(results)} PASS")
    sys.exit(1 if failed or not results else 0)


def run_checks(ctx):
    c = http.client.HTTPConnection("127.0.0.1", HPORT, timeout=10)
    c.request("GET", "/api/auth/me", headers={"Host": SITE})
    r = c.getresponse()
    check("HTTP -> HTTPS yönlendirme (Caddy)", r.status in (301, 308) and
          r.getheader("Location", "").startswith(f"https://{SITE}"), (r.status, r.getheader("Location")))
    r, body = req(ctx, "GET", "/")
    check("HTTPS sertifikası doğrulandı (yerel test CA) + sayfa 200", r.status == 200, r.status)
    check("HSTS başlığı (yalnız HTTPS'te)", r.getheader("Strict-Transport-Security") == "max-age=31536000")
    check("CSP + X-Frame-Options", "script-src 'sha256-" in (r.getheader("Content-Security-Policy") or "")
          and r.getheader("X-Frame-Options") == "DENY")
    check("Server başlığı gizli", r.getheader("Server") is None, r.getheader("Server"))
    r, _ = req(ctx, "GET", "/api/history/incidents")
    check("oturumsuz geçmiş API 401", r.status == 401, r.status)
    login = json.dumps({"username": "lan_sup", "password": PASS})
    r, _ = req(ctx, "POST", "/api/auth/login", {"Content-Type": "application/json", "Origin": "https://kotu.example"},
               login)
    check("yabancı Origin ile giriş 403", r.status == 403, r.status)
    r, body = req(ctx, "POST", "/api/auth/login", {"Content-Type": "application/json", "Origin": ORIGIN}, login)
    ck = r.getheader("Set-Cookie") or ""
    check("vekil üzerinden giriş 200", r.status == 200, (r.status, body[:200]))
    check("çerez __Host-, Secure, HttpOnly, SameSite=Strict", ck.startswith("__Host-ppe_session=") and
          all(x in ck for x in ("Secure", "HttpOnly", "SameSite=strict")), ck[:120])
    cookie = ck.split(";", 1)[0]
    csrf = json.loads(body)["csrf_token"]
    r, body = req(ctx, "GET", "/api/auth/me", {"Cookie": cookie})
    check("oturumlu istek 200, profil lan", r.status == 200 and json.loads(body)["profile"] == "lan", r.status)
    r, _ = req(ctx, "GET", "/api/history/incidents", {"Cookie": cookie, "Origin": "https://kotu.example"})
    check("CORS varsayılan kapalı (ACAO yok)", r.getheader("Access-Control-Allow-Origin") is None)
    mp, ctype = multipart({"cam": "lancam", "name": "LAN test", "meta": {"status": "running"}})
    r, _ = req(ctx, "POST", "/ingest/frame", {"Content-Type": ctype}, mp)
    check("ingest token'sız 401 (HTTPS üzerinden)", r.status == 401, r.status)
    r, _ = req(ctx, "POST", "/ingest/frame", {"Content-Type": ctype, "Cookie": cookie}, mp)
    check("ingest oturum çereziyle 401 (insan oturumu makine kimliği değil)", r.status == 401, r.status)
    r, _ = req(ctx, "POST", "/ingest/frame", {"Content-Type": ctype, "X-Token": TOKEN}, mp)
    check("ingest makine token'ı ile 200", r.status == 200, r.status)
    s, status = ws_open(ctx, cookie, "https://kotu.example")
    s.close()
    check("WSS yabancı Origin reddedilir", " 403 " in status + " ", status)
    s, status = ws_open(ctx, "", ORIGIN)
    s.close()
    check("WSS oturumsuz reddedilir", " 403 " in status + " ", status)
    s, status = ws_open(ctx, cookie, ORIGIN)
    ok = " 101 " in status + " "
    op, data = ws_frame(s) if ok else (None, b"")
    s.close()
    check("WSS oturum + aynı köken: 101 ve ikili kare (1 bayt idx + JPEG)", ok and op == 2 and data[1:3] == b"\xff\xd8",
          (status, op, data[:4]))
    r, _ = req(ctx, "POST", "/api/auth/logout", {"Cookie": cookie, "Origin": ORIGIN})
    check("CSRF token'sız çıkış 403", r.status == 403, r.status)
    r, _ = req(ctx, "POST", "/api/auth/logout", {"Cookie": cookie, "Origin": ORIGIN, "X-CSRF-Token": csrf})
    check("CSRF token'lı çıkış 200", r.status == 200, r.status)
    r, _ = req(ctx, "GET", "/api/auth/me", {"Cookie": cookie})
    check("çıkış sonrası oturum 401", r.status == 401, r.status)
    spoof = sh("docker", "exec", "ppe-lantest-dash", "python", "-c",
               "import urllib.request as u\ntry:\n u.urlopen(u.Request('http://127.0.0.1:8000/api/auth/me', headers="
               f"{{'Host': '{SITE}', 'X-Forwarded-Proto': 'https'}}))\nexcept Exception as e: print(e.code)",
               check=False).stdout.strip()
    check("vekili atlayan sahte X-Forwarded-Proto 403", spoof == "403", spoof)


if __name__ == "__main__":
    main()
