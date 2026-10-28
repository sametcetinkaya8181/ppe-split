"""Gerçek PostgreSQL test altyapısı. Yalnızca adı '_test' ile biten TEST_DATABASE_URL veritabanı kullanılır;
oturum başında sıfırdan oluşturulur, Alembic ile head'e getirilir, sonunda silinir. Ana DB'ye dokunulmaz."""
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

ROOT = Path(__file__).resolve().parent.parent
TEST_URL = os.getenv("TEST_DATABASE_URL", "")


def _test_db_name() -> str:
    if not TEST_URL:
        pytest.exit("TEST_DATABASE_URL tanımlı değil (docker compose --profile test run --rm tests).", 2)
    name = make_url(TEST_URL).database or ""
    if not name.endswith("_test"):
        pytest.exit(f"Güvenlik: test veritabanı adı '_test' ile bitmeli (gelen: {name!r}).", 2)
    return name


def alembic_cfg(url: str = TEST_URL) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.attributes["url"] = url
    cfg.attributes["configure_logger"] = False
    return cfg


def _admin_exec(sql: str):
    eng = create_engine(make_url(TEST_URL).set(database="postgres"), isolation_level="AUTOCOMMIT")
    with eng.connect() as c:
        c.execute(text(sql))
    eng.dispose()


@pytest.fixture(scope="session")
def db_url():
    name = _test_db_name()
    _admin_exec(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    _admin_exec(f'CREATE DATABASE "{name}"')
    command.upgrade(alembic_cfg(), "head")
    yield TEST_URL
    from db.session import _engine_for
    _engine_for.cache_clear()                     # önbellekteki engine'ler bırakılır; DROP ... WITH (FORCE) bağlantıları kapatır
    _admin_exec(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture(scope="session")
def engine(db_url):
    eng = create_engine(db_url)
    yield eng
    eng.dispose()


@pytest.fixture
def conn(engine):
    """Her test kendi transaction'ında çalışır ve sonunda geri alınır."""
    with engine.connect() as c:
        tx = c.begin()
        yield c
        tx.rollback()


ALL_TABLES = "violation_events, incidents, person_run_reports, detector_runs, cameras"


@pytest.fixture
def api(db_url, engine, monkeypatch):
    """Uygulama ppe_test'e bağlı; her test boş DB, boş RAM ve boş test snapshot klasörüyle başlar."""
    import app as app_module
    from fastapi.testclient import TestClient
    monkeypatch.setenv("DATABASE_URL", db_url)
    with engine.begin() as c:
        c.execute(text(f"TRUNCATE {ALL_TABLES} RESTART IDENTITY"))
    with app_module.LOCK:
        app_module.CAMS.clear()
        app_module.ALERTS.clear()
        app_module.ALERT_SEQ = 0
    app_module._HB.clear()
    for f in app_module.SNAP_DIR.iterdir():
        f.unlink()
    yield TestClient(app_module.app)
    with engine.begin() as c:                     # sonraki (şema/migration) testlerine veri taşınmasın
        c.execute(text(f"TRUNCATE {ALL_TABLES} RESTART IDENTITY"))


def count(engine, table: str, where: str = "", **params) -> int:
    with engine.connect() as c:
        return c.execute(text(f"SELECT count(*) FROM {table} {where}"), params).scalar()


# ---------------------------------------------------------------- yardımcılar
UNIQUE, FK, CHECK, NOT_NULL = "23505", "23503", "23514", "23502"


def violates(conn, sql: str, params: dict, sqlstate: str):
    """Komutun belirtilen SQLSTATE ile reddedildiğini doğrular (savepoint ile; dış transaction bozulmaz)."""
    with pytest.raises(IntegrityError) as ei:
        with conn.begin_nested():
            conn.execute(text(sql), params)
    assert ei.value.orig.sqlstate == sqlstate, f"beklenen {sqlstate}, gelen {ei.value.orig.sqlstate}: {ei.value.orig}"


def seed(conn) -> dict:
    """cam1/cam2 kameraları ve her birine bir run."""
    ids = {"r1": uuid.uuid4(), "r2": uuid.uuid4()}
    conn.execute(text("INSERT INTO cameras (id, name, source_kind) VALUES ('cam1', 'Giriş', 'file'), ('cam2', 'Kule', 'rtsp')"))
    conn.execute(text("INSERT INTO detector_runs (id, camera_id, loop_no) VALUES (:r1, 'cam1', 1), (:r2, 'cam2', 1)"), ids)
    return ids


# 0002'den beri her olayın incidents satırı olmalı; aynı ifadede (CTE) eklenir.
INCIDENT_CTE = """WITH inc AS (INSERT INTO incidents (id, run_id, camera_id, pid)
    VALUES (:incident_id, :run_id, :camera_id, :pid) ON CONFLICT (id) DO NOTHING) """
EVENT_INSERT = """INSERT INTO violation_events (event_id, incident_id, run_id, camera_id, pid, event_type,
    missing_helmet, missing_vest, missing_text, helmet_rate, vest_rate, video_time_sec, occurred_at, box, snapshot_path)
    VALUES (:event_id, :incident_id, :run_id, :camera_id, :pid, :event_type, :missing_helmet, :missing_vest,
    :missing_text, :helmet_rate, :vest_rate, :video_time_sec, :occurred_at, CAST(:box AS jsonb), :snapshot_path)"""
EVENT_SQL = INCIDENT_CTE + EVENT_INSERT


def event(run_id, **over) -> dict:
    ev = {"event_id": uuid.uuid4(), "incident_id": uuid.uuid4(), "run_id": run_id, "camera_id": "cam1", "pid": 7,
          "event_type": "start", "missing_helmet": True, "missing_vest": False, "missing_text": "Baretsiz",
          "helmet_rate": 12, "vest_rate": 78, "video_time_sec": 12.4,
          "occurred_at": datetime(2026, 10, 28, 11, 29, tzinfo=timezone.utc),
          "box": "[20, 30, 200, 420]", "snapshot_path": None}
    ev.update(over)
    return ev
