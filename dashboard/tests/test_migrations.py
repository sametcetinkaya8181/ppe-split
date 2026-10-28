"""Alembic migration ve ORM-şema tutarlılığı (gerçek PostgreSQL 17)."""
import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from db import Base
from tests.conftest import alembic_cfg

TABLES = {"cameras", "detector_runs", "violation_events", "person_run_reports"}


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
    assert heads == ["0001"]
    assert _current(db_url) == {"0001"}
    assert _tables(db_url) == TABLES


def test_upgrade_head_is_repeatable(db_url):
    command.upgrade(alembic_cfg(), "head")
    command.upgrade(alembic_cfg(), "head")
    assert _current(db_url) == {"0001"}


def test_downgrade_then_upgrade(db_url):
    command.downgrade(alembic_cfg(), "base")
    assert _tables(db_url) == set() and _current(db_url) == set()
    command.upgrade(alembic_cfg(), "head")
    assert _tables(db_url) == TABLES and _current(db_url) == {"0001"}


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
    assert db == orm and len(db) == 22


def test_no_create_all_in_app_code():
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    for p in [root / "app.py", *(root / "db").glob("*.py")]:
        assert not re.search(r"\.create_all\(", p.read_text(encoding="utf-8")), p
