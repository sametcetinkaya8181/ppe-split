"""FastAPI: /health/ready (200/503) ve mevcut endpoint'lerin regresyon kontrolü."""
import json

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import text

from tests.conftest import alembic_cfg

import app as app_module  # noqa: E402  (DATA_DIR ortamdan okunur)

JPG = b"\xff\xd8\xff\xe0FAKEJPEG\xff\xd9"


@pytest.fixture
def client():
    return TestClient(app_module.app)


# ---------------------------------------------------------------- /health/ready
def test_ready_200_at_head(client, db_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", db_url)
    r = client.get("/health/ready")
    assert r.status_code == 200
    assert r.json() == {"status": "ready", "db": "ok", "schema": "current", "revision": ["0002"]}


def test_ready_503_when_schema_outdated(client, db_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", db_url)
    command.downgrade(alembic_cfg(), "base")
    try:
        r = client.get("/health/ready")
        assert r.status_code == 503
        assert r.json() == {"status": "not_ready", "db": "ok", "schema": "outdated", "current": [], "expected": ["0002"]}
    finally:
        command.upgrade(alembic_cfg(), "head")
    assert client.get("/health/ready").status_code == 200


def test_ready_503_when_db_unreachable_without_leaking_secrets(client, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://gizli_kullanici:COK_GIZLI_PAROLA@olmayan-sunucu:5432/ppe")
    r = client.get("/health/ready")
    assert r.status_code == 503
    assert r.json() == {"status": "not_ready", "db": "unreachable"}
    for secret in ("COK_GIZLI_PAROLA", "gizli_kullanici", "olmayan-sunucu", "postgresql"):
        assert secret not in r.text


@pytest.mark.parametrize("value", [None, "", "sqlite:///x.db", "postgresql://u:p@h/d"])
def test_ready_503_when_misconfigured(client, monkeypatch, value):
    if value is None:
        monkeypatch.delenv("DATABASE_URL", raising=False)
    else:
        monkeypatch.setenv("DATABASE_URL", value)
    r = client.get("/health/ready")
    assert r.status_code == 503 and r.json() == {"status": "not_ready", "db": "misconfigured"}


# ---------------------------------------------------------------- mevcut davranış (regresyon)
def test_existing_endpoints_unchanged(api, engine):
    """Yanıt biçimleri FAZ 1 ile aynı; olaylar artık v2 kimlikli ve DB'ye yazılıyor."""
    import uuid
    from tests.helpers import FRAME, event, frame_payload, post_frame, report
    client = api
    assert client.get("/healthz").json() == {"cameras": 0, "running": 0}
    assert client.get("/").status_code == 200 and "Saha KKD" in client.get("/").text

    run = uuid.uuid4()
    ev = event("start", run_id=run, incident_id=uuid.uuid4(), t=12.4)
    r = post_frame(client, frame_payload([ev], run_id=run))
    assert r.status_code == 200 and r.json() == {"ok": True}

    st = client.get("/api/state?after=0").json()
    assert set(st) == {"cameras", "totals", "alerts", "last_alert_id"}
    cam = st["cameras"][0]
    assert set(cam) == {"id", "idx", "name", "meta", "summary", "people"} and cam["id"] == "cam1" and cam["idx"] == 0
    assert st["totals"] == {"people": 1, "ok": 0, "viol": 1, "analyzing": 0}
    a = st["alerts"][0]
    assert a["id"] == 1 and st["last_alert_id"] == 1 and a["type"] == "start" and a["cam_name"] == "Kamera cam1"
    assert a["event_id"] == ev["event_id"] and a["t"] == 12.4 and a["missing"] == "Baretsiz"
    assert client.get("/api/state?after=1").json()["alerts"] == []
    assert client.get("/api/alerts").json()[0]["id"] == 1

    r = client.post("/ingest/report", json=report(run, rows=[{"pid": 7, "first": 1.0, "last": 9.0, "n": 30,
                                                              "helmet": 10, "vest": 90, "violation": True}]))
    assert r.status_code == 200 and r.json() == {"ok": True}
    rep = client.get("/api/report").json()
    assert rep == [{"pid": 7, "first": 1.0, "last": 9.0, "n": 30, "helmet": 10, "vest": 90, "violation": True,
                    "cam": "cam1", "cam_name": "Kamera cam1"}]

    assert client.get("/video_feed/yok").status_code == 404
    with client.websocket_connect("/ws") as ws:                   # binary protokol: 1 bayt idx + JPEG
        msg = ws.receive_bytes()
    assert msg[0] == 0 and msg[1:] == FRAME

    with engine.connect() as c:                                   # FAZ 3: kalıcı yazım
        assert c.execute(text("SELECT count(*) FROM violation_events")).scalar() == 1
        assert c.execute(text("SELECT count(*) FROM person_run_reports")).scalar() == 1


def test_v1_events_without_identity_are_rejected(api, engine):
    """Kasıtlı davranış değişikliği: kimliksiz (FAZ 2 öncesi) olay 422 alır; sunucu kimlik uydurmaz.
    Olaysız eski kareler eskisi gibi kabul edilir."""
    from tests.helpers import frame_payload, post_frame
    v1 = {"type": "start", "pid": 7, "t": 12.4, "missing": "Baretsiz", "helmet_rate": 12, "vest_rate": 78,
          "box": [20, 30, 200, 420]}
    r = post_frame(api, frame_payload([v1]))
    assert r.status_code == 422
    locs = {tuple(e["loc"]) for e in r.json()["detail"]}
    assert {("events", 0, "event_id"), ("events", 0, "run_id"), ("events", 0, "incident_id")} <= locs
    assert api.get("/api/alerts").json() == []
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM violation_events")).scalar() == 0
    legacy = {"cam": "cam1", "name": "Eski", "meta": {"status": "running"}, "summary": {"t": 1.0}, "people": []}
    assert post_frame(api, legacy).status_code == 200
    v1_report = {"cam": "cam1", "status": "finished", "report": [{"pid": 7, "first": 1.0, "last": 9.0, "n": 30,
                                                               "helmet": 10, "vest": 90, "violation": True}]}
    assert api.post("/ingest/report", json=v1_report).status_code == 422
