"""FAZ 10-A.7: admin / user rol deneyimi ve model olasılıklarının (oranların) yalnız admin'e gitmesi; canlı uyarıların
izin listesiyle sunulması ve incident/run ilişkileri. Gerçek PostgreSQL 17 + sınırlı ppe_app rolü; veriler gerçek
ingest uçlarından yazılır. Yanıtların yalnız durum kodu değil JSON içeriği de denetlenir."""
import io
import re
import threading
import time
import uuid

import pytest
from fastapi.testclient import TestClient

import app as app_module
from auth import cli, store
from tests.conftest import TEST_PASSWORD, login, make_user
from tests.helpers import B64_A, event, frame_payload, lifecycle, post_frame, report

SCORE = re.compile(r"(^|_)(conf|confidence|score|scores|prob|probs|probability|probabilities|logit|logits)$|_rate$")
NOT_SCORE = {"low_conf", "model_scores"}       # kapsam nedeni sayısı; yetki bayrağı


def client_as(role, cameras=None):
    c = TestClient(app_module.app)
    name = f"u_{role}_{uuid.uuid4().hex[:6]}"
    make_user(name, role, cameras)
    login(c, name)
    return c


def score_keys(v, path="$", rowish=False):
    """Olasılık/oran adlı anahtarlar + canlı kişi/rapor satırındaki 'helmet'/'vest' oranları."""
    out = []
    if isinstance(v, list):
        for i, x in enumerate(v):
            out += score_keys(x, f"{path}[{i}]", rowish)
    elif isinstance(v, dict):
        for k, x in v.items():
            if (SCORE.search(k) and k not in NOT_SCORE) or (rowish and k in ("helmet", "vest")):
                out.append(f"{path}.{k}")
            out += score_keys(x, f"{path}.{k}", k in ("people", "report_rows"))
    return out


def seed(api):
    """cam1: kapanmış ihlal (snapshot'lı) + run raporu; cam1 canlı kare (kişi oranlı, meta'da skor adlı alan)."""
    run = uuid.uuid4()
    inc, evs = lifecycle(run)
    evs[0]["conf"] = 0.93                                   # dedektör ileride alan eklese bile tarayıcıya gitmemeli
    evs[0]["source_label"] = "rtsp://admin:S3cr3t@10.0.0.9/x"
    p = frame_payload(evs, run_id=run, det_conf=0.81,
                      health={"state": "ok", "coverage": {"analyzing": 1, "analyzing_by_reason": {"low_conf": 1}}})
    assert post_frame(api, p).status_code == 200
    assert api.post("/ingest/report", json=report(run)).status_code == 200
    return {"run": run, "inc": inc, "start": evs[0]}


def responses(c, seed_):
    """Rolün erişebildiği tüm veri uçlarının JSON gövdeleri (403 olanlar None)."""
    paths = {"state": "/api/state?after=0", "alerts": "/api/alerts", "report_rows": "/api/report",
             "incidents": "/api/history/incidents", "incident": f"/api/history/incidents/{seed_['inc']}",
             "runs": "/api/history/runs", "run": f"/api/history/runs/{seed_['run']}", "summary": "/api/history/summary",
             "cameras": "/api/history/cameras", "me": "/api/auth/me"}
    out = {}
    for k, p in paths.items():
        r = c.get(p)
        out[k] = r.json() if r.status_code == 200 else None
    return out


# ---------------------------------------------------------------- rol: user = supervisor
def test_user_role_alias_is_stored_as_supervisor(api):
    assert store.resolve_role("user") == "supervisor" and store.resolve_role("admin") == "admin"
    with pytest.raises(store.StoreError):
        store.resolve_role("operator")
    with pytest.raises(store.StoreError):
        store.resolve_role("superadmin")
    make_user("saha_user", "user")
    c = TestClient(app_module.app)
    login(c, "saha_user")
    body = c.get("/api/auth/me").json()
    assert body["user"]["role"] == "supervisor"
    assert body["permissions"] == {"live": True, "snapshots": True, "review": True, "admin": False,
                                   "model_scores": False}


def test_cli_creates_user_role_without_password_argument(api, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(TEST_PASSWORD + "\n"))
    assert cli.main(["create", "saha_cli", "--role", "user", "--password-stdin"]) == 0
    out = capsys.readouterr().out
    assert "saha_cli (supervisor" in out and TEST_PASSWORD not in out
    users = {u["username"]: u for u in api.get("/api/admin/users").json()}
    assert users["saha_cli"]["role"] == "supervisor"
    with pytest.raises(SystemExit):                          # parola argüman olarak verilemez
        cli.main(["create", "x_user", "--role", "user", "--password", TEST_PASSWORD])


def test_admin_api_accepts_user_role_and_audits_stored_role(api, engine):
    r = api.post("/api/admin/users", json={"username": "saha_api", "password": TEST_PASSWORD, "role": "user"})
    assert r.status_code == 201
    uid = r.json()["id"]
    users = {u["username"]: u for u in api.get("/api/admin/users").json()}
    assert users["saha_api"]["role"] == "supervisor"
    assert api.patch(f"/api/admin/users/{uid}", json={"role": "viewer"}).status_code == 200
    assert api.patch(f"/api/admin/users/{uid}", json={"role": "user"}).status_code == 200
    assert {u["username"]: u for u in api.get("/api/admin/users").json()}["saha_api"]["role"] == "supervisor"
    assert api.post("/api/admin/users", json={"username": "saha_bad", "password": TEST_PASSWORD,
                                              "role": "operator"}).status_code == 422
    from sqlalchemy import text
    with engine.connect() as c:
        roles = [r[0] for r in c.execute(text("SELECT detail->>'role' FROM audit_log WHERE event = 'user_create'"))]
    assert "supervisor" in roles and "user" not in roles


def test_user_cannot_manage_accounts_or_escalate(api):
    user = client_as("user")
    name = user.get("/api/auth/me").json()["user"]["username"]
    ids = {u["username"]: u["id"] for u in api.get("/api/admin/users").json()}
    assert user.get("/api/admin/users").status_code == 403
    assert user.post("/api/admin/users", json={"username": "kotu_admin", "password": TEST_PASSWORD,
                                               "role": "admin"}).status_code == 403
    for uid in (ids[name], ids["test_admin"]):                 # kendini yükseltme / yöneticiyi değiştirme
        assert user.patch(f"/api/admin/users/{uid}", json={"role": "admin"}).status_code == 403
        assert user.post(f"/api/admin/users/{uid}/password", json={"password": TEST_PASSWORD}).status_code == 403
    assert user.get("/api/admin/audit").status_code == 403
    assert user.get("/api/auth/me").json()["user"]["role"] == "supervisor"
    assert "kotu_admin" not in {u["username"] for u in api.get("/api/admin/users").json()}


# ---------------------------------------------------------------- olasılıklar yalnız admin
def test_user_and_viewer_receive_no_model_scores_anywhere(api):
    s = seed(api)
    for role in ("user", "viewer"):
        c = client_as(role)
        body = responses(c, s)
        assert score_keys(body) == [], (role, score_keys(body))
        assert body["run"]["persons"] and body["incident"]["timeline"], role       # veri gerçekten döndü
    user = responses(client_as("user"), s)
    assert user["state"]["cameras"][0]["people"] == [{"pid": 7, "status": "IHLAL"}]
    assert user["report_rows"] == [{"pid": 7, "first": 0.1, "last": 9.5, "n": 40, "violation": True,
                                    "cam": "cam1", "cam_name": "Kamera cam1"}]
    assert user["state"]["cameras"][0]["meta"]["health"]["coverage"]["analyzing_by_reason"] == {"low_conf": 1}


def test_admin_keeps_model_scores(api):
    s = seed(api)
    body = responses(api, s)
    start = [a for a in body["alerts"] if a["type"] == "start"][0]
    assert (start["helmet_rate"], start["vest_rate"]) == (0, 100)
    assert body["state"]["cameras"][0]["people"][0] == {"pid": 7, "status": "IHLAL", "helmet": 0, "vest": 100}
    assert body["state"]["cameras"][0]["meta"]["det_conf"] == 0.81
    assert {"helmet", "vest"} <= set(body["report_rows"][0])
    assert {"helmet_rate", "vest_rate"} <= set(body["run"]["persons"][0])
    assert all({"helmet_rate", "vest_rate"} <= set(e) for e in body["incident"]["timeline"])
    assert body["me"]["permissions"]["model_scores"] is True


def test_role_view_never_changes_decisions(api):
    """Aynı olay iki rolde: karar alanları (tür, eksik ekipman, kimlikler, zaman, snapshot) birebir aynı."""
    s = seed(api)
    admin, user = responses(api, s), responses(client_as("user"), s)
    strip = lambda a: {k: v for k, v in a.items() if k not in ("helmet_rate", "vest_rate")}  # noqa: E731
    assert [strip(a) for a in admin["alerts"]] == user["alerts"]
    assert {k: v for k, v in admin["incident"].items() if k != "timeline"} == \
        {k: v for k, v in user["incident"].items() if k != "timeline"}
    assert [strip(e) for e in admin["incident"]["timeline"]] == user["incident"]["timeline"]
    assert [{k: v for k, v in p.items() if k not in ("helmet_rate", "vest_rate")} for p in admin["run"]["persons"]] \
        == user["run"]["persons"]
    assert admin["summary"] == user["summary"]


def test_alerts_are_allowlisted_for_every_role(api):
    """Ham dedektör olayındaki bilinmeyen alanlar (ör. güven skoru, maskelenmemiş kaynak etiketi) hiçbir role gitmez."""
    seed(api)
    for c in (api, client_as("user")):
        text = c.get("/api/alerts").text + c.get("/api/state").text
        assert '"conf"' not in text and "S3cr3t" not in text and "source_label" not in text
        for a in c.get("/api/alerts").json():
            assert set(a) <= set(app_module.ALERT_KEYS) | set(app_module.ALERT_SCORE_KEYS)


# ---------------------------------------------------------------- canlı uyarı ↔ snapshot ↔ run
def test_alert_snapshot_and_run_link_match_the_incident(api):
    r1, r2 = uuid.uuid4(), uuid.uuid4()
    inc1, evs1 = lifecycle(r1, cam="cam1")
    inc2, evs2 = lifecycle(r2, cam="cam2", snapshot=B64_A)
    assert post_frame(api, frame_payload(evs1, cam="cam1", run_id=r1)).status_code == 200
    assert post_frame(api, frame_payload(evs2, cam="cam2", run_id=r2)).status_code == 200
    user = client_as("user")
    starts = {a["incident_id"]: a for a in user.get("/api/alerts").json() if a["type"] == "start"}
    for inc, evs, run in ((inc1, evs1, r1), (inc2, evs2, r2)):
        a = starts[str(inc)]
        assert a["snapshot"] == f"/snapshots/alert_{evs[0]['event_id']}.jpg"          # kendi olayının dosyası
        assert a["run_id"] == str(run) and a["event_id"] == evs[0]["event_id"]
        assert user.get(a["snapshot"]).status_code == 200
        hist = user.get(f"/api/history/incidents/{inc}").json()
        assert hist["snapshot"] == a["snapshot"] and hist["run"]["id"] == a["run_id"]  # geçmişle aynı ilişki
        assert user.get(f"/api/history/runs/{a['run_id']}").json()["camera_id"] == a["camera_id"]
    scoped = client_as("user", cameras=["cam1"])
    assert {a["cam"] for a in scoped.get("/api/alerts").json()} == {"cam1"}
    assert scoped.get(starts[str(inc2)]["snapshot"]).status_code == 404               # kapsam dışı görüntü
    assert scoped.get(f"/api/history/runs/{r2}").status_code == 404


def test_running_run_is_reported_as_running(api):
    run = uuid.uuid4()
    p = frame_payload([event("start", run_id=run, incident_id=uuid.uuid4(), snapshot=B64_A)], run_id=run)
    assert post_frame(api, p).status_code == 200
    r = client_as("user").get(f"/api/history/runs/{run}").json()
    assert r["status"] == "running" and r["finished_at"] is None and r["persons"] == []


def test_duplicate_event_is_not_a_second_alert(api):
    run = uuid.uuid4()
    ev = event("start", run_id=run, incident_id=uuid.uuid4(), snapshot=B64_A)
    for _ in range(2):
        assert post_frame(api, frame_payload([ev], run_id=run)).status_code == 200
    assert [a["event_id"] for a in client_as("user").get("/api/alerts").json()] == [ev["event_id"]]


# ---------------------------------------------------------------- arayüz kabuğu
def test_index_header_and_role_aware_client(api, anon):
    html = anon.get("/").text
    h1 = re.search(r"<h1>(.*?)</h1>", html).group(1)
    assert h1 == "Saha KKD İzleme" and "baret" not in h1.lower() and "yelek" not in h1.lower()
    script = re.search(r"<script>(.*?)</script>", html, re.S).group(1)
    assert "permissions.model_scores" in script and "SESSION" in script and "DETAIL_CACHE.clear()" in script
    assert "w.innerHTML" not in script                          # kişi/rapor tabloları DOM API ile kurulur
    assert '"helmet_rate"' not in html                          # sayfa kabuğuna veri gömülmez


# ---------------------------------------------------------------- açık video akışında yetki yeniden doğrulaması
def _open_stream(client, camera="cam1"):
    """Starlette TestClient akışı parça parça vermez: istek yalnız akış sona erince döner. Bu yüzden ayrı iş
    parçacığında açılır; iptal çalışıyorsa parça kısa sürede biter, çalışmıyorsa asılı kalır."""
    box = {}

    def run():
        box["resp"] = client.get(f"/video_feed/{camera}")
    th = threading.Thread(target=run, daemon=True)
    th.start()
    return th, box


def _frame(api, run):
    assert post_frame(api, frame_payload([], run_id=run)).status_code == 200


def test_open_video_stream_is_cut_when_role_is_demoted(api, monkeypatch):
    monkeypatch.setattr(app_module, "WS_RECHECK_SEC", 0.05)
    run = uuid.uuid4()
    _frame(api, run)
    user = client_as("user")
    name = user.get("/api/auth/me").json()["user"]["username"]
    uid = {u["username"]: u["id"] for u in api.get("/api/admin/users").json()}[name]
    th, box = _open_stream(user)
    time.sleep(0.4)
    assert th.is_alive()                                       # geçerli oturum: akış sürüyor
    assert api.patch(f"/api/admin/users/{uid}", json={"role": "viewer"}).status_code == 200
    _frame(api, run)                                           # bekleyen akışı uyandırır; yeniden doğrulama reddeder
    th.join(10)
    assert not th.is_alive(), "rolü düşürülen hesabın açık video akışı kesilmedi"
    assert box["resp"].status_code == 200 and box["resp"].content.count(b"--frame") >= 1
    assert user.get("/video_feed/cam1").status_code == 401    # rol değişimi oturumu iptal eder


def test_valid_video_stream_survives_rechecks_and_stops_on_disable(api, monkeypatch):
    monkeypatch.setattr(app_module, "WS_RECHECK_SEC", 0.05)
    run = uuid.uuid4()
    _frame(api, run)
    user = client_as("user")
    name = user.get("/api/auth/me").json()["user"]["username"]
    uid = {u["username"]: u["id"] for u in api.get("/api/admin/users").json()}[name]
    th, box = _open_stream(user)
    for _ in range(6):                                         # birkaç yeniden doğrulama döngüsü
        time.sleep(0.1)
        _frame(api, run)
    assert th.is_alive(), "geçerli oturumun akışı yanlışlıkla kesildi"
    assert api.patch(f"/api/admin/users/{uid}", json={"disabled": True}).status_code == 200
    _frame(api, run)
    th.join(10)
    assert not th.is_alive(), "devre dışı bırakılan hesabın açık video akışı kesilmedi"
    assert box["resp"].content.count(b"--frame") >= 2
