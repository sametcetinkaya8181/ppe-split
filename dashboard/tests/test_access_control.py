"""FAZ 6: kimlik doğrulama, rol + kamera kapsamı (nesne düzeyi), CSRF/Origin, oturum ömrü, kaba kuvvet, makine
kimliği (ingest), güvenlik başlıkları, Host denetimi, denetim kaydı. Gerçek PostgreSQL 17 + sınırlı ppe_app rolü."""
import json
import os
import subprocess
import sys
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from starlette.websockets import WebSocketDisconnect

import app as app_module
from auth import security, store
from tests.conftest import APP_ROLE, ORIGIN, TEST_PASSWORD, count, login, make_user, url_for
from tests.helpers import B64_A, FRAME, event, frame_payload, lifecycle, post_frame

ROLES = ("viewer", "supervisor", "admin")


def client_as(role, cameras=None, name=None):
    c = TestClient(app_module.app)
    name = name or f"u_{role}_{uuid.uuid4().hex[:6]}"
    make_user(name, role, cameras)
    login(c, name)
    return c


def seed_two_cameras(api):
    """cam1 ve cam2'de birer kapalı ihlal (cam1'inki snapshot'lı) + canlı kareler."""
    r1, r2 = uuid.uuid4(), uuid.uuid4()
    inc1, evs1 = lifecycle(r1, cam="cam1")
    inc2, evs2 = lifecycle(r2, cam="cam2", snapshot=B64_A)
    assert post_frame(api, frame_payload(evs1, cam="cam1", run_id=r1)).status_code == 200
    assert post_frame(api, frame_payload(evs2, cam="cam2", run_id=r2)).status_code == 200
    snap2 = f"/snapshots/alert_{evs2[0]['event_id']}.jpg"
    snap1 = f"/snapshots/alert_{evs1[0]['event_id']}.jpg"
    return {"r1": r1, "r2": r2, "inc1": inc1, "inc2": inc2, "snap1": snap1, "snap2": snap2}


# ================================================================ uç matrisi
def protected(seed):
    """(yol, gereken en düşük rol)."""
    return [("/api/history/incidents", "viewer"), (f"/api/history/incidents/{seed['inc1']}", "viewer"),
            ("/api/history/summary", "viewer"), ("/api/history/cameras", "viewer"), ("/api/history/runs", "viewer"),
            (f"/api/history/runs/{seed['r1']}", "viewer"), ("/api/auth/me", "viewer"),
            ("/api/state", "supervisor"), ("/api/alerts", "supervisor"), ("/api/report", "supervisor"),
            (seed["snap1"], "supervisor"), ("/api/admin/users", "admin"), ("/api/admin/audit", "admin")]


def test_endpoint_matrix_anonymous_and_roles(api, anon):
    seed = seed_two_cameras(api)
    clients = {r: client_as(r) for r in ROLES}
    for path, need in protected(seed):
        assert anon.get(path).status_code == 401, path
        for role, c in clients.items():
            code = c.get(path).status_code
            want = 200 if store.RANK[role] >= store.RANK[need] else 403
            assert code == want, (path, role, code)
    for path in ("/", "/healthz", "/health/ready"):
        assert anon.get(path).status_code == 200, path
    assert anon.get("/healthz").json() == {"status": "ok"}
    assert anon.get("/health/ready").json() == {"status": "ready"}
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert anon.get(path).status_code == 404, path


def test_snapshot_requires_supervisor_even_if_url_known(api, anon):
    seed = seed_two_cameras(api)
    for c, code in ((anon, 401), (client_as("viewer"), 403), (client_as("supervisor"), 200)):
        assert c.get(seed["snap2"]).status_code == code
    orphan = f"alert_{uuid.uuid4()}.jpg"
    (app_module.SNAP_DIR / orphan).write_bytes(b"\xff\xd8x\xff\xd9")                  # dosya var, DB'de referansı yok
    assert api.get(f"/snapshots/{orphan}").status_code == 404
    for bad in ("/snapshots/../app.py", "/snapshots/x.jpg", "/snapshots/alert_1_cam2_ID3.jpg"):
        assert api.get(bad).status_code == 404


def test_viewer_gets_no_image_links(api):
    seed = seed_two_cameras(api)
    viewer = client_as("viewer")
    items = viewer.get("/api/history/incidents").json()["items"]
    with_snap = [i for i in items if i["incident_id"] == str(seed["inc2"])][0]
    assert with_snap["snapshot"] is None and with_snap["snapshot_restricted"] is True
    d = viewer.get(f"/api/history/incidents/{seed['inc2']}").json()
    assert d["snapshot"] is None and all(e["snapshot"] is None for e in d["timeline"])
    assert api.get(f"/api/history/incidents/{seed['inc2']}").json()["snapshot"] == seed["snap2"]


# ================================================================ nesne düzeyi yetki (kamera kapsamı)
def test_camera_scope_object_level_authorization(api):
    seed = seed_two_cameras(api)
    c = client_as("supervisor", cameras=["cam1"])
    assert {i["camera_id"] for i in c.get("/api/history/incidents").json()["items"]} == {"cam1"}
    assert c.get(f"/api/history/incidents/{seed['inc2']}").status_code == 404      # UUID bilmek yetmez
    assert c.get(f"/api/history/incidents/{seed['inc1']}").status_code == 200
    assert c.get(f"/api/history/runs/{seed['r2']}").status_code == 404
    assert {r["camera_id"] for r in c.get("/api/history/runs").json()["items"]} == {"cam1"}
    assert [x["id"] for x in c.get("/api/history/cameras").json()] == ["cam1"]
    s = c.get("/api/history/summary").json()
    assert [x["camera_id"] for x in s["by_camera"]] == ["cam1"] and s["events"]["total"] == 3
    assert c.get(f"/api/history/incidents?camera_id=cam2").json()["total"] == 0
    assert c.get(seed["snap2"]).status_code == 404                                  # kapsam dışı snapshot
    st = c.get("/api/state").json()
    assert [x["id"] for x in st["cameras"]] == ["cam1"] and {a["cam"] for a in st["alerts"]} == {"cam1"}
    assert {a["cam"] for a in c.get("/api/alerts").json()} == {"cam1"}
    assert c.get("/video_feed/cam2").status_code == 404
    none = client_as("viewer", cameras=[])
    assert none.get("/api/history/incidents").json()["total"] == 0


# ================================================================ WebSocket
def ws_first(client, headers=None):
    with client.websocket_connect("/ws", headers=headers or {}) as ws:
        return ws.receive_bytes()


def test_websocket_auth_role_origin_and_scope(api, anon):
    seed_two_cameras(api)
    for c in (anon, client_as("viewer")):
        with pytest.raises(WebSocketDisconnect) as ei:
            ws_first(c)
        assert ei.value.code == 1008
    with pytest.raises(WebSocketDisconnect):
        ws_first(api, {"Origin": "http://kotu-site.example"})                      # CSWSH
    msg = ws_first(api, {"Origin": "http://testserver"})
    assert msg[1:3] == b"\xff\xd8"                                                  # 1 bayt idx + JPEG
    only1 = client_as("supervisor", cameras=["cam2"])
    idx = {c["id"]: c["idx"] for c in api.get("/api/state").json()["cameras"]}
    with only1.websocket_connect("/ws") as ws:
        got = {ws.receive_bytes()[0]}
    assert got == {idx["cam2"]}
    assert count_audit("ws_rejected") >= 2


def test_websocket_closed_after_session_revoked(api, monkeypatch):
    monkeypatch.setattr(app_module, "WS_RECHECK_SEC", 0.2)
    monkeypatch.setattr(security, "CACHE_TTL", 0.0)
    seed_two_cameras(api)
    sup = client_as("supervisor", name="ws_sup")
    uid = user_id("ws_sup")
    with sup.websocket_connect("/ws") as ws:
        ws.receive_bytes()
        assert api.post(f"/api/admin/users/{uid}/revoke-sessions", headers=ORIGIN).json()["revoked"] == 1
        with pytest.raises(WebSocketDisconnect) as ei:
            for i in range(50):
                post_frame(api, frame_payload([], cam="cam1"), frame=FRAME + bytes([i]))
                ws.receive_bytes()
        assert ei.value.code == 1008


# ================================================================ giriş / kaba kuvvet
def test_login_cookie_flags_and_generic_errors(api, anon):
    make_user("ali", "viewer")
    r = anon.post("/api/auth/login", json={"username": "ali", "password": TEST_PASSWORD}, headers=ORIGIN)
    ck = r.headers["set-cookie"]
    assert r.status_code == 200 and "ppe_session=" in ck and "HttpOnly" in ck and "SameSite=strict" in ck
    assert "Path=/" in ck and "Secure" not in ck                                    # local profil: HTTP
    bad = anon.post("/api/auth/login", json={"username": "ali", "password": "yanlis-parola-123"}, headers=ORIGIN)
    unknown = anon.post("/api/auth/login", json={"username": "yok_boyle", "password": "x" * 12}, headers=ORIGIN)
    assert bad.status_code == unknown.status_code == 401 and bad.json() == unknown.json()
    assert TEST_PASSWORD not in r.text + bad.text


@pytest.mark.parametrize("kw", [dict(headers={"Origin": "http://kotu.example"}),
                                dict(headers={"Sec-Fetch-Site": "cross-site"})])
def test_login_rejects_cross_origin(api, anon, kw):
    make_user("veli", "viewer")
    r = anon.post("/api/auth/login", json={"username": "veli", "password": TEST_PASSWORD}, **kw)
    assert r.status_code == 403


def test_login_form_post_not_accepted(api, anon):
    make_user("form", "viewer")
    r = anon.post("/api/auth/login", data={"username": "form", "password": TEST_PASSWORD}, headers=ORIGIN)
    assert r.status_code == 422                                                     # yalnız JSON (CSRF'li form yok)


def test_account_lockout_after_failures(api, anon):
    make_user("kilit", "viewer")
    for _ in range(security.LOGIN_MAX_FAILURES):
        assert anon.post("/api/auth/login", json={"username": "kilit", "password": "yanlis-parola-1"},
                         headers=ORIGIN).status_code == 401
    r = anon.post("/api/auth/login", json={"username": "kilit", "password": TEST_PASSWORD}, headers=ORIGIN)
    assert r.status_code == 401                                                     # doğru parola da kilitli
    users = {u["username"]: u for u in api.get("/api/admin/users").json()}
    assert users["kilit"]["locked"] is True
    assert count_audit("login", "failure") >= security.LOGIN_MAX_FAILURES + 1


def test_ip_rate_limit(api, anon, monkeypatch):
    monkeypatch.setattr(security.IP_LIMITER, "limit", 3)
    for i in range(3):
        anon.post("/api/auth/login", json={"username": f"yok{i}", "password": "x" * 12}, headers=ORIGIN)
    r = anon.post("/api/auth/login", json={"username": "test_admin", "password": TEST_PASSWORD}, headers=ORIGIN)
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0


def test_disabled_user_cannot_login(api, anon):
    uid = make_user("pasif", "viewer")
    assert api.patch(f"/api/admin/users/{uid}", json={"disabled": True}, headers=ORIGIN).status_code == 200
    assert anon.post("/api/auth/login", json={"username": "pasif", "password": TEST_PASSWORD},
                     headers=ORIGIN).status_code == 401


# ================================================================ CSRF ve oturum ömrü
def test_csrf_required_for_state_changes(api):
    body = {"username": "yeni_kul", "password": "Guclu-Parola-123", "role": "viewer"}
    no_csrf = TestClient(app_module.app, cookies=api.cookies)
    assert no_csrf.post("/api/admin/users", json=body, headers=ORIGIN).status_code == 403
    assert no_csrf.post("/api/admin/users", json=body, headers={**ORIGIN, "X-CSRF-Token": "yanlis"}).status_code == 403
    assert api.post("/api/admin/users", json=body, headers={"Origin": "http://kotu.example"}).status_code == 403
    assert no_csrf.post("/api/auth/logout", headers=ORIGIN).status_code == 403
    assert count_audit("csrf_failure") >= 3
    assert api.post("/api/admin/users", json=body, headers=ORIGIN).status_code == 201


def test_logout_revokes_session(api):
    c = client_as("viewer")
    assert c.post("/api/auth/logout", headers=ORIGIN).status_code == 200
    c2 = TestClient(app_module.app, cookies={security.COOKIE: "x" * 43})
    assert c.get("/api/auth/me").status_code == 401 and c2.get("/api/auth/me").status_code == 401


@pytest.mark.parametrize("column", ["last_seen_at", "expires_at"])
def test_idle_and_absolute_expiry(api, engine, column):
    c = client_as("viewer", name=f"exp_{column[:4]}")
    with engine.begin() as conn:
        conn.execute(text(f"UPDATE user_sessions SET {column} = now() - interval '13 hours' "
                          "WHERE user_id = (SELECT id FROM app_users WHERE username = :u)"), {"u": f"exp_{column[:4]}"})
    with security._CACHE_LOCK:
        security._CACHE.clear()
    assert c.get("/api/auth/me").status_code == 401


def test_admin_revocation_and_role_change_take_effect(api):
    c = client_as("supervisor", name="hedef")
    uid = user_id("hedef")
    assert c.get("/api/state").status_code == 200
    assert api.patch(f"/api/admin/users/{uid}", json={"role": "viewer"}, headers=ORIGIN).status_code == 200
    assert c.get("/api/auth/me").status_code == 401                                 # yeni rol yeni oturumla
    login(c, "hedef")
    assert c.get("/api/auth/me").json()["user"]["role"] == "viewer" and c.get("/api/state").status_code == 403


def test_no_privilege_escalation(api):
    viewer = client_as("viewer", name="izleyici")
    uid = user_id("izleyici")
    assert viewer.patch(f"/api/admin/users/{uid}", json={"role": "admin"}, headers=ORIGIN).status_code == 403
    assert viewer.post("/api/admin/users", json={"username": "x_admin", "password": "Guclu-Parola-123",
                                                  "role": "admin"}, headers=ORIGIN).status_code == 403
    sup = client_as("supervisor")
    assert sup.get("/api/admin/audit").status_code == 403
    me = user_id("test_admin")
    r = api.patch(f"/api/admin/users/{me}", json={"role": "viewer"}, headers=ORIGIN)
    assert r.status_code == 422                                                     # son yönetici korunur
    assert api.post("/api/admin/users", json={"username": "kotu", "password": "Guclu-Parola-123", "role": "root"},
                    headers=ORIGIN).status_code == 422
    assert api.post("/api/admin/users", json={"username": "kotu2", "password": "Guclu-Parola-123", "role": "viewer",
                                              "is_admin": True}, headers=ORIGIN).status_code == 422


def test_password_change_policy_and_revocation(api, anon):
    c = client_as("viewer", name="degis")
    assert c.post("/api/auth/password", json={"current_password": "yanlis-parola-1",
                                              "new_password": "Yeni-Parola-2026"}, headers=ORIGIN).status_code == 403
    assert c.post("/api/auth/password", json={"current_password": TEST_PASSWORD, "new_password": "kisa"},
                  headers=ORIGIN).status_code == 422
    r = c.post("/api/auth/password", json={"current_password": TEST_PASSWORD, "new_password": "Yeni-Parola-2026"},
               headers=ORIGIN)
    assert r.status_code == 200 and c.get("/api/auth/me").status_code == 401
    assert anon.post("/api/auth/login", json={"username": "degis", "password": TEST_PASSWORD},
                     headers=ORIGIN).status_code == 401
    assert anon.post("/api/auth/login", json={"username": "degis", "password": "Yeni-Parola-2026"},
                     headers=ORIGIN).status_code == 200


def test_passwords_stored_as_argon2id_only(api, engine):
    with engine.connect() as c:
        h = c.execute(text("SELECT password_hash FROM app_users WHERE username = 'test_admin'")).scalar()
        dump = json.dumps([list(map(str, r)) for t in ("app_users", "user_sessions", "audit_log")
                           for r in c.execute(text(f"SELECT * FROM {t}"))])
    assert h.startswith("$argon2id$") and TEST_PASSWORD not in dump
    assert api.cookies.get(security.COOKIE) not in dump                             # yalnız token özeti saklanır


# ================================================================ makine kimliği (ingest)
def test_session_never_authorizes_ingest(api, anon, monkeypatch):
    monkeypatch.setattr(app_module, "INGEST_TOKEN", "makine-token-1")
    monkeypatch.setattr(app_module, "INGEST_TOKEN_NEXT", "makine-token-2")
    run = uuid.uuid4()
    p = frame_payload([event("start", run_id=run, incident_id=uuid.uuid4())], run_id=run)
    assert post_frame(api, p).status_code == 401                                    # admin oturumu yetmez
    assert post_frame(api, p, headers={"X-CSRF-Token": api.headers["X-CSRF-Token"]}).status_code == 401
    assert post_frame(anon, p, headers={"X-Token": "makine-token-1"}).status_code == 200
    p2 = frame_payload([event("start", run_id=run, incident_id=uuid.uuid4())], run_id=run)
    assert post_frame(anon, p2, headers={"X-Token": "makine-token-2"}).status_code == 200   # rotasyon
    assert anon.get("/api/history/incidents", headers={"X-Token": "makine-token-1"}).status_code == 401
    assert count_audit("ingest_auth", "denied") >= 1


# ================================================================ başlıklar, Host, CORS
def test_security_headers_and_cors_default_deny(api, anon):
    page = anon.get("/")
    csp = page.headers["content-security-policy"]
    assert "script-src 'sha256-" in csp and "unsafe-inline" not in csp.split("script-src")[1].split(";")[0]
    assert "frame-ancestors 'none'" in csp and page.headers["x-frame-options"] == "DENY"
    assert page.headers["x-content-type-options"] == "nosniff" and page.headers["referrer-policy"] == "no-referrer"
    assert "strict-transport-security" not in page.headers                          # local profil: HTTP
    r = api.get("/api/history/incidents", headers={"Origin": "http://kotu.example"})
    assert "access-control-allow-origin" not in r.headers and r.headers["cache-control"] == "no-store"
    pre = anon.options("/api/history/incidents", headers={"Origin": "http://kotu.example",
                                                           "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in pre.headers


def test_inline_script_hash_matches_page(api, anon):
    import base64
    import hashlib
    import re
    html = anon.get("/").text
    script = re.search(r"<script>(.*?)</script>", html, re.S).group(1)
    digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    assert f"'sha256-{digest}'" in anon.get("/").headers["content-security-policy"]
    assert not re.search(r"\son[a-z]+=", html.split("<script>")[0])                 # satır içi olay işleyicisi yok


@pytest.mark.parametrize("host", ["192.168.1.50:8000", "ppe.santiye.local", "evil.example", ""])
def test_local_profile_rejects_non_local_host(api, host):
    assert api.get("/api/auth/me", headers={"Host": host}).status_code == 400
    assert post_frame(api, frame_payload([]), headers={"Host": host}).status_code == 400


def test_ingest_token_checked_before_body_parsing(anon, monkeypatch):
    """Bozuk/çok büyük multipart gövde, yanlış token'la AYRIŞTIRILMADAN 401 alır (form ayrıştırma DoS savunması)."""
    monkeypatch.setattr(app_module, "INGEST_TOKEN", "dogru")
    bad_body = b"--x\r\n" + b"A" * 200_000
    hdr = {"Content-Type": "multipart/form-data; boundary=x", "X-Token": "yanlis"}
    assert anon.post("/ingest/frame", content=bad_body, headers=hdr).status_code == 401
    assert anon.post("/ingest/frame", content=bad_body, headers={**hdr, "X-Token": "dogru"}).status_code in (400, 422)


def test_range_requests_not_processed(api, anon):
    seed = seed_two_cameras(api)
    for c, path in ((anon, "/"), (api, seed["snap2"])):
        r = c.get(path, headers={"Range": "bytes=0-10, 20-30, 40-50"})
        assert r.status_code == 200 and "content-range" not in r.headers and len(r.content) > 51


def test_ready_fails_when_snapshots_not_writable(api, anon, monkeypatch):
    """Ayrıcalıksız container snapshot yazamıyorsa sessizce 'hazır' görünmez."""
    real = os.access
    monkeypatch.setattr(app_module.os, "access", lambda p, m: False if p == app_module.SNAP_DIR else real(p, m))
    assert anon.get("/health/ready").status_code == 503
    assert api.get("/health/ready").json()["storage"] == "snapshots_not_writable"


# ================================================================ denetim kaydı
def test_audit_log_records_without_secrets(api, anon, engine, monkeypatch):
    monkeypatch.setattr(app_module, "INGEST_TOKEN", "GIZLI_MAKINE_TOKEN")
    seed = seed_two_cameras_with_token(api, "GIZLI_MAKINE_TOKEN")
    anon.post("/api/auth/login", json={"username": "test_admin", "password": "YANLIS_GIZLI_PAROLA"}, headers=ORIGIN)
    anon.get("/api/history/incidents")
    post_frame(anon, frame_payload([]), headers={"X-Token": "YANLIS_MAKINE_TOKEN"})
    api.get(seed)
    events = {(a["event"], a["outcome"]) for a in api.get("/api/admin/audit").json()}
    assert {("login", "success"), ("login", "failure"), ("access_denied", "denied"),
            ("ingest_auth", "denied"), ("snapshot_access", "success")} <= events
    with engine.connect() as c:
        dump = json.dumps([list(map(str, r)) for r in c.execute(text("SELECT * FROM audit_log"))])
    for secret in ("YANLIS_GIZLI_PAROLA", TEST_PASSWORD, "GIZLI_MAKINE_TOKEN", "YANLIS_MAKINE_TOKEN",
                   api.cookies.get(security.COOKIE), B64_A[:40]):
        assert secret not in dump
    assert anon.get("/api/admin/audit").status_code == 401


def test_audit_log_is_append_only_for_app_role(api):
    from tests.conftest import psycopg_conn
    import psycopg
    with psycopg_conn(url_for(role=APP_ROLE)) as c:
        with pytest.raises(psycopg.Error) as ei:
            c.execute("UPDATE audit_log SET event = 'x'")
    assert ei.value.sqlstate == "42501"


# ================================================================ lan profili (ayrı süreç, gerçek ortam)
def test_lan_profile_config_validation():
    base = {k: v for k, v in os.environ.items() if k not in ("ALLOWED_HOSTS", "TRUSTED_PROXIES")}
    base.update(SECURITY_PROFILE="lan", PASSWORD_HASH_PROFILE="default")
    code = "from auth import security; security.validate_config(tuple(x for x in ['{tok}'] if x)); print('OK')"
    cases = [({}, "x", "ALLOWED_HOSTS"), ({"ALLOWED_HOSTS": "ppe.test"}, "x", "TRUSTED_PROXIES"),
             ({"ALLOWED_HOSTS": "ppe.test", "TRUSTED_PROXIES": "172.31.250.10/32"}, "", "INGEST_TOKEN"),
             ({"ALLOWED_HOSTS": "localhost", "TRUSTED_PROXIES": "10.0.0.1/32"}, "x", "yalnız localhost")]
    for env, tok, msg in cases:
        r = subprocess.run([sys.executable, "-c", code.format(tok=tok)], env={**base, **env}, capture_output=True,
                           text=True)
        assert r.returncode != 0 and msg in r.stderr, (env, r.stderr[-300:])
    r = subprocess.run([sys.executable, "-c", "import auth.passwords"], capture_output=True, text=True,
                       env={**base, "PASSWORD_HASH_PROFILE": "test"})
    assert r.returncode != 0 and "yalnız 'local'" in r.stderr
    r = subprocess.run([sys.executable, "-c", code.format(tok="")], capture_output=True, text=True,
                       env={**os.environ, "SECURITY_PROFILE": "local", "ALLOWED_HOSTS": "192.168.1.5"})
    assert r.returncode != 0 and "SECURITY_PROFILE=lan" in r.stderr


def test_lan_profile_behaviour(api):
    make_user("lan_sup", "supervisor")
    env = {**os.environ, "SECURITY_PROFILE": "lan", "ALLOWED_HOSTS": "ppe.test", "TRUSTED_PROXIES":
           "172.31.250.10/32", "INGEST_TOKEN": "lan-token", "PASSWORD_HASH_PROFILE": "default",
           "DATABASE_URL": url_for(role=APP_ROLE), "LAN_USER": "lan_sup", "LAN_PASS": TEST_PASSWORD}
    r = subprocess.run([sys.executable, "-m", "tests.lan_profile_check"], env=env, capture_output=True, text=True,
                       timeout=120)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    assert "LAN_OK" in r.stdout


# ================================================================ yardımcılar
def user_id(username):
    return {u["username"]: u["id"] for u in app_module_admin_users()}[username]


def app_module_admin_users():
    from auth import store
    from db.session import get_session
    with get_session() as s:
        return store.list_users(s)


def count_audit(event, outcome=None):
    from db.session import get_session
    with get_session() as s:
        q = "SELECT count(*) FROM audit_log WHERE event = :e" + (" AND outcome = :o" if outcome else "")
        return s.execute(text(q), {"e": event, "o": outcome}).scalar()


def seed_two_cameras_with_token(api, token):
    run = uuid.uuid4()
    ev = event("start", run_id=run, incident_id=uuid.uuid4(), snapshot=B64_A)
    assert post_frame(api, frame_payload([ev], run_id=run), headers={"X-Token": token}).status_code == 200
    return f"/snapshots/alert_{ev['event_id']}.jpg"
