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
