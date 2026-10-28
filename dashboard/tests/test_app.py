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
    assert r.json() == {"status": "ready", "db": "ok", "schema": "current", "revision": ["0001"]}


def test_ready_503_when_schema_outdated(client, db_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", db_url)
    command.downgrade(alembic_cfg(), "base")
    try:
        r = client.get("/health/ready")
        assert r.status_code == 503
        assert r.json() == {"status": "not_ready", "db": "ok", "schema": "outdated", "current": [], "expected": ["0001"]}
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
def test_existing_endpoints_unchanged(client, engine):
    assert client.get("/healthz").json() == {"cameras": 0, "running": 0}
    assert client.get("/").status_code == 200 and "Saha KKD" in client.get("/").text

    ev = {"type": "start", "pid": 7, "t": 12.4, "missing": "Baretsiz", "helmet_rate": 12, "vest_rate": 78,
          "box": [20, 30, 200, 420]}
    payload = {"cam": "cam1", "name": "Giriş", "meta": {"status": "running", "fps": 25.0, "width": 1280,
               "height": 720, "loop": 1}, "summary": {"t": 12.4, "people": 1, "ok": 0, "viol": 1, "analyzing": 0},
               "people": [{"pid": 7, "status": "IHLAL", "helmet": 12, "vest": 78}], "events": [ev]}
    r = client.post("/ingest/frame", files={"frame": ("f.jpg", JPG, "image/jpeg")}, data={"payload": json.dumps(payload)})
    assert r.status_code == 200 and r.json() == {"ok": True}

    st = client.get("/api/state?after=0").json()
    assert set(st) == {"cameras", "totals", "alerts", "last_alert_id"}
    cam = st["cameras"][0]
    assert set(cam) == {"id", "idx", "name", "meta", "summary", "people"} and cam["id"] == "cam1" and cam["idx"] == 0
    assert st["totals"] == {"people": 1, "ok": 0, "viol": 1, "analyzing": 0}
    a = st["alerts"][0]
    assert a["id"] == 1 and st["last_alert_id"] == 1 and a["type"] == "start" and a["cam_name"] == "Giriş"
    assert client.get("/api/state?after=1").json()["alerts"] == []
    assert client.get("/api/alerts").json()[0]["id"] == 1

    r = client.post("/ingest/report", json={"cam": "cam1", "name": "Giriş", "status": "finished",
                                            "report": [{"pid": 7, "first": 1.0, "last": 9.0, "n": 30, "helmet": 10,
                                                        "vest": 90, "violation": True}]})
    assert r.json() == {"ok": True}
    rep = client.get("/api/report").json()
    assert rep[0]["cam"] == "cam1" and rep[0]["pid"] == 7

    assert client.get("/video_feed/yok").status_code == 404

    with client.websocket_connect("/ws") as ws:                   # binary protokol: 1 bayt idx + JPEG
        msg = ws.receive_bytes()
    assert msg[0] == 0 and msg[1:] == JPG

    # FAZ 1'de ingest henüz DB'ye yazmaz
    with engine.connect() as c:
        for t in ("cameras", "detector_runs", "violation_events", "person_run_reports"):
            assert c.execute(text(f"SELECT count(*) FROM {t}")).scalar() == 0, t


def test_detector_v2_payload_accepted_by_existing_endpoints(client, engine):
    """FAZ 2 dedektör payload'ı (kimlikli olaylar, farklı run'dan olay, yeni rapor alanları) mevcut uçlarla uyumlu."""
    import base64
    run_old, run_new = "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"
    inc = "33333333-3333-4333-8333-333333333333"
    start = {"type": "start", "pid": 4, "t": 2.1, "missing": "Baretsiz", "helmet_rate": 0, "vest_rate": 100,
             "box": [100, 100, 200, 400], "event_id": "44444444-4444-4444-8444-444444444444", "run_id": run_old,
             "camera_id": "camv2", "incident_id": inc, "occurred_at": "2026-10-28T11:29:00.123Z",
             "missing_helmet": True, "missing_vest": False, "snapshot_b64": base64.b64encode(JPG).decode()}
    end = dict(start, type="end", missing="", missing_helmet=False, event_id="55555555-5555-4555-8555-555555555555",
               run_id=run_new)
    end.pop("snapshot_b64")
    payload = {"cam": "camv2", "name": "V2", "run_id": run_new, "camera_id": "camv2",
               "meta": {"source": "rtsp://***@10.0.0.5/s", "source_kind": "rtsp", "run_id": run_new, "status": "running",
                        "fps": 10.0, "width": 640, "height": 480, "error": None, "loop": 2, "proc_fps": 9.5},
               "summary": {"t": 2.1, "people": 1, "ok": 0, "viol": 1, "analyzing": 0, "raw_helmets": 0, "raw_vests": 1},
               "people": [], "events": [start, end]}
    r = client.post("/ingest/frame", files={"frame": ("f.jpg", JPG, "image/jpeg")}, data={"payload": json.dumps(payload)})
    assert r.status_code == 200 and r.json() == {"ok": True}
    alerts = [a for a in client.get("/api/alerts").json() if a["cam"] == "camv2"]
    by_type = {a["type"]: a for a in alerts}
    assert by_type["start"]["event_id"] == start["event_id"] and by_type["start"]["run_id"] == run_old
    assert by_type["end"]["incident_id"] == inc and by_type["end"]["run_id"] == run_new
    assert by_type["start"]["snapshot"].startswith("/snapshots/") and "snapshot_b64" not in by_type["start"]
    assert client.get(by_type["start"]["snapshot"]).content == JPG
    cam = next(c for c in client.get("/api/state").json()["cameras"] if c["id"] == "camv2")
    assert cam["meta"]["source"] == "rtsp://***@10.0.0.5/s"

    rep = {"cam": "camv2", "name": "V2", "status": "running", "run_id": run_new, "camera_id": "camv2",
           "run_status": "finished", "loop": 2, "source_kind": "rtsp", "started_at": "2026-10-28T11:28:00.000Z",
           "finished_at": "2026-10-28T11:30:00.000Z", "video_time_sec": 120.0, "error": None,
           "unclosed_incidents": [{"incident_id": inc, "pid": 4, "reason": "stale", "t": 99.0}],
           "unclosed_overflow": 0, "orphan_events": 0,
           "report": [{"pid": 4, "first": 0.1, "last": 9.0, "n": 50, "helmet": 5, "vest": 95, "violation": True}]}
    assert client.post("/ingest/report", json=rep).json() == {"ok": True}
    assert [r for r in client.get("/api/report").json() if r["cam"] == "camv2"][0]["pid"] == 4
    with engine.connect() as c:                                  # FAZ 2'de de DB'ye yazım yok
        assert c.execute(text("SELECT count(*) FROM violation_events")).scalar() == 0
