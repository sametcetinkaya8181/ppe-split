"""lan profilinin uygulama düzeyi davranışı (ayrı süreçte; SECURITY_PROFILE=lan ortamıyla çalıştırılır).
Gerçek TLS yoktur: güvenilir vekil, TestClient'ın istemci adresi + X-Forwarded-* başlıklarıyla taklit edilir.
Gerçek TLS ters vekil entegrasyonu: deploy/lan/lan_smoke.py."""
import os
import sys

from fastapi.testclient import TestClient

import app as app_module
from auth import security

PROXY = ("172.31.250.10", 40000)
HTTPS = {"Host": "ppe.test", "X-Forwarded-Proto": "https", "X-Forwarded-For": "192.168.10.23",
         "Origin": "https://ppe.test"}
fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        fails.append(name)


def from_peer(app, peer):
    """TCP eş adresini ayarlar (bu Starlette sürümünde TestClient istemci adresi parametresi almıyor)."""
    async def wrapped(scope, receive, send):
        await app({**scope, "client": peer}, receive, send)
    return wrapped


assert security.PROFILE == "lan"
proxy = TestClient(from_peer(app_module.app, PROXY), base_url="https://ppe.test")
direct = TestClient(from_peer(app_module.app, ("192.168.10.99", 50000)), base_url="http://ppe.test")
creds = {"username": os.environ["LAN_USER"], "password": os.environ["LAN_PASS"]}

r = direct.get("/api/auth/me", headers={"Host": "ppe.test"})
check("vekilsiz (HTTP) tarayıcı isteği 403", r.status_code == 403 and "HTTPS" in r.text, r.status_code)
r = direct.get("/api/auth/me", headers={"Host": "ppe.test", "X-Forwarded-Proto": "https"})
check("güvenilmeyen istemcinin sahte X-Forwarded-Proto'su 403", r.status_code == 403, r.status_code)
r = proxy.get("/api/auth/me", headers={**HTTPS, "Host": "baska.host"})
check("izinli olmayan Host 400", r.status_code == 400, r.status_code)
r = proxy.post("/api/auth/login", json=creds, headers=HTTPS)
ck = r.headers.get("set-cookie", "")
check("vekil + HTTPS ile giriş 200", r.status_code == 200, r.text[:200])
check("çerez __Host- önekli, Secure, HttpOnly, SameSite=Strict",
      ck.startswith("__Host-ppe_session=") and "Secure" in ck and "HttpOnly" in ck and "SameSite=strict" in ck, ck)
check("HSTS başlığı", r.headers.get("strict-transport-security") == "max-age=31536000")
token = ck.split(";", 1)[0].split("=", 1)[1]
r = proxy.get("/api/auth/me", headers=HTTPS, cookies={"__Host-ppe_session": token})
check("oturumlu istek 200", r.status_code == 200 and r.json()["profile"] == "lan", r.status_code)
r = proxy.post("/api/auth/login", json=creds, headers={**HTTPS, "Origin": "http://ppe.test"})
check("https kökeni yerine http Origin reddedilir", r.status_code == 403, r.status_code)
r = direct.post("/ingest/report", content=b"{}", headers={"Host": "localhost:8000", "Content-Type": "application/json"})
check("ingest (makine ucu) localhost'tan token'sız 401", r.status_code == 401, r.status_code)
r = direct.get("/healthz", headers={"Host": "localhost:8000"})
check("healthz localhost'tan 200 (container healthcheck)", r.status_code == 200, r.status_code)
from db.session import get_session
from sqlalchemy import text
with get_session() as s:
    ip = s.execute(text("SELECT client_ip FROM audit_log WHERE event = 'login' AND outcome = 'success' "
                        "ORDER BY id DESC LIMIT 1")).scalar()
check("denetimde istemci IP'si güvenilir vekilin X-Forwarded-For'undan", ip == "192.168.10.23", ip)

print("LAN_OK" if not fails else f"LAN_FAIL {fails}")
sys.exit(1 if fails else 0)
