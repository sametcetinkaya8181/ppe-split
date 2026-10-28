"""Alembic migration ve ORM-şema tutarlılığı (gerçek PostgreSQL 17)."""
import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from db import Base
from tests.conftest import alembic_cfg

TABLES = {"cameras", "detector_runs", "violation_events", "person_run_reports", "incidents"}


def _tables(url):
    eng = create_engine(url)
    try:
        return set(inspect(eng).get_table_names()) - {"alembic_version"}
    finally:
        eng.dispose()


def _current(url):
    eng = create_engine(url)
    try:
        with eng.connect() as c:
            return set(MigrationContext.configure(c).get_current_heads())
    finally:
        eng.dispose()


def test_server_is_postgres_17(engine):
    with engine.connect() as c:
        assert c.execute(text("SHOW server_version_num")).scalar().startswith("17")


def test_single_head_and_upgrade_to_head(db_url):
    heads = ScriptDirectory.from_config(alembic_cfg()).get_heads()
    assert heads == ["0002"]
    assert _current(db_url) == {"0002"}
    assert _tables(db_url) == TABLES


def test_upgrade_head_is_repeatable(db_url):
    command.upgrade(alembic_cfg(), "head")
    command.upgrade(alembic_cfg(), "head")
    assert _current(db_url) == {"0002"}


def test_downgrade_then_upgrade(db_url):
    command.downgrade(alembic_cfg(), "base")
    assert _tables(db_url) == set() and _current(db_url) == set()
    command.upgrade(alembic_cfg(), "head")
    assert _tables(db_url) == TABLES and _current(db_url) == {"0002"}


def test_downgrade_refuses_when_data_exists(db_url, engine):
    with engine.begin() as c:
        c.execute(text("INSERT INTO cameras (id, name) VALUES ('guard_cam', 'x')"))
    try:
        with pytest.raises(RuntimeError, match="veri var"):
            command.downgrade(alembic_cfg(), "base")
        assert _tables(db_url) == TABLES
    finally:
        with engine.begin() as c:
            c.execute(text("DELETE FROM cameras WHERE id = 'guard_cam'"))


def _seed_0001_events(engine, conflicting=False):
    """0001 şemasında (incidents yokken) olay verisi oluşturur."""
    import uuid
    r, inc = uuid.uuid4(), uuid.uuid4()
    with engine.begin() as c:
        c.execute(text("INSERT INTO cameras (id, name) VALUES ('mcam', 'x')"))
        c.execute(text("INSERT INTO detector_runs (id, camera_id) VALUES (:r, 'mcam')"), {"r": r})
        for i, pid in enumerate((5, 6 if conflicting else 5)):
            c.execute(text("INSERT INTO violation_events (event_id, incident_id, run_id, camera_id, pid, event_type, "
                           "missing_helmet, missing_vest, occurred_at) VALUES (:e, :i, :r, 'mcam', :p, :t, :mh, false, now())"),
                      {"e": uuid.uuid4(), "i": inc, "r": r, "p": pid, "t": ("start", "end")[i], "mh": i == 0})
    return r, inc


def _wipe(engine):
    with engine.begin() as c:
        c.execute(text("TRUNCATE violation_events, person_run_reports, detector_runs, cameras RESTART IDENTITY"))


def test_0002_upgrade_backfills_incidents_and_keeps_events(db_url, engine):
    command.downgrade(alembic_cfg(), "0001")
    try:
        r, inc = _seed_0001_events(engine)
        command.upgrade(alembic_cfg(), "head")
        with engine.connect() as c:
            assert c.execute(text("SELECT count(*) FROM violation_events")).scalar() == 2
            assert c.execute(text("SELECT id, run_id, camera_id, pid FROM incidents")).all() == [(inc, r, "mcam", 5)]
        command.downgrade(alembic_cfg(), "0001")                 # metadata boş: veri kaybı yok, izinli
        with engine.connect() as c:
            assert c.execute(text("SELECT count(*) FROM violation_events")).scalar() == 2
    finally:
        command.upgrade(alembic_cfg(), "head")
        with engine.begin() as c:
            c.execute(text("TRUNCATE detector_runs, cameras CASCADE"))


def test_0002_upgrade_stops_on_conflicting_incident_data(db_url, engine):
    command.downgrade(alembic_cfg(), "0001")
    try:
        _seed_0001_events(engine, conflicting=True)              # aynı incident iki farklı pid ile
        with pytest.raises(Exception, match="incidents"):
            command.upgrade(alembic_cfg(), "head")
        assert _current(db_url) == {"0001"}                      # transaction geri alındı, şema 0001'de kaldı
        with engine.connect() as c:
            assert c.execute(text("SELECT count(*) FROM violation_events")).scalar() == 2
    finally:
        _wipe(engine)
        command.upgrade(alembic_cfg(), "head")


def test_0002_downgrade_refuses_to_drop_report_metadata(db_url, engine):
    import uuid
    with engine.begin() as c:
        c.execute(text("INSERT INTO cameras (id, name) VALUES ('mcam', 'x')"))
        c.execute(text("INSERT INTO detector_runs (id, camera_id, status, finished_at, report_metadata) "
                       "VALUES (:r, 'mcam', 'finished', now(), '{\"orphan_events\": 1}')"), {"r": uuid.uuid4()})
    try:
        with pytest.raises(RuntimeError, match="report_metadata"):
            command.downgrade(alembic_cfg(), "0001")
        assert _current(db_url) == {"0002"}
    finally:
        with engine.begin() as c:
            c.execute(text("TRUNCATE detector_runs, cameras CASCADE"))


def test_orm_matches_migration(engine):
    """ORM modelleri ile migration sonucu oluşan şema arasında autogenerate farkı olmamalı."""
    with engine.connect() as c:
        diff = compare_metadata(MigrationContext.configure(c), Base.metadata)
    assert diff == []


def test_check_constraint_names_match_orm(engine):
    orm = {ck.name for t in Base.metadata.tables.values() for ck in t.constraints
           if ck.__class__.__name__ == "CheckConstraint"}
    with engine.connect() as c:
        db = set(c.execute(text("SELECT conname FROM pg_constraint WHERE contype = 'c' "
                                "AND conrelid::regclass::text = ANY(:t)"), {"t": list(TABLES)}).scalars())
    assert db == orm and len(db) == 24


def test_no_create_all_in_app_code():
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    for p in [root / "app.py", *(root / "db").glob("*.py")]:
        assert not re.search(r"\.create_all\(", p.read_text(encoding="utf-8")), p
