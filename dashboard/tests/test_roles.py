"""FAZ 5: PostgreSQL least privilege (db/roles.py). Yalnızca '_test' veritabanları ve '_test' rolleri kullanılır."""
import uuid

import psycopg
import pytest
from alembic import command
from sqlalchemy import text

from db import roles
from db.session import get_session
from tests.conftest import (APP_ROLE, MIGRATOR_ROLE, ROLE_PASSWORDS, _admin_exec, alembic_cfg, psycopg_conn,
                            regrant_app, setup_test_roles, url_for)
from tests.helpers import lifecycle, frame_payload, post_frame, report

DENIED = "42501"                                       # insufficient_privilege
TABLES = {"cameras", "detector_runs", "incidents", "violation_events", "person_run_reports"}


def denied(url, stmt):
    with psycopg_conn(url) as c:
        c.autocommit = True                           # CREATE DATABASE transaction bloğunda çalışmaz
        with pytest.raises(psycopg.Error) as ei:
            c.execute(stmt)
    assert ei.value.sqlstate == DENIED, (stmt, ei.value.sqlstate)


def owners(database):
    with psycopg_conn(url_for(database)) as c:
        return {r[0]: r[1] for r in c.execute("SELECT tablename, tableowner FROM pg_tables WHERE schemaname='public'")}


@pytest.fixture
def app_db(db_url):
    """ppe_test'te uygulama yetkileri (migration testleri tabloları süper kullanıcıyla yeniden oluşturmuş olabilir)."""
    regrant_app()
    return db_url


@pytest.fixture
def scratch_db(db_url):
    """Geçici, izole veritabanı (ppe_roles_<rastgele>_test); test sonunda silinir."""
    name = f"ppe_roles_{uuid.uuid4().hex[:8]}_test"
    _admin_exec(f'CREATE DATABASE "{name}"')
    yield name
    _admin_exec(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


# ================================================================ uygulama rolü
def test_app_role_attributes_and_table_privileges(app_db):
    with psycopg_conn(url_for(role=APP_ROLE)) as c:
        s = roles.summary(c)
    assert s["role"] == APP_ROLE and not s["superuser"] and not s["createdb"] and not s["createrole"]
    assert s["schema_create"] is False
    assert {t: p for t, p in s["tables"].items() if t in TABLES} == dict.fromkeys(TABLES, ["SELECT", "INSERT", "UPDATE"])
    assert s["tables"]["alembic_version"] == ["SELECT"]
    assert s["tables"]["audit_log"] == ["SELECT", "INSERT"]                 # denetim kaydı yalnız ekleme
    assert s["tables"]["app_users"] == s["tables"]["user_sessions"] == ["SELECT", "INSERT", "UPDATE"]


@pytest.mark.parametrize("stmt", [
    "DELETE FROM violation_events", "DELETE FROM cameras", "TRUNCATE incidents", "DROP TABLE violation_events",
    "ALTER TABLE cameras ADD COLUMN x int", "CREATE TABLE kotu (id int)", "UPDATE alembic_version SET version_num = 'x'",
    "CREATE ROLE kotu_rol", "CREATE DATABASE kotu_db",
])
def test_app_role_denied(app_db, stmt):
    denied(url_for(role=APP_ROLE), stmt)


def test_app_role_cannot_grant_itself_more(app_db):
    """Sahibi olmayanın GRANT'i PostgreSQL'de uyarıyla hiçbir şey vermez; sonuç yetkisi değişmemeli."""
    with psycopg_conn(url_for(role=APP_ROLE)) as c:
        c.execute(f"GRANT DELETE ON cameras TO {APP_ROLE}")
        assert c.execute("SELECT has_table_privilege(current_user, 'cameras', 'DELETE')").fetchone()[0] is False


def test_application_really_connects_as_app_role(api):
    with get_session() as s:
        assert s.execute(text("SELECT current_user")).scalar() == APP_ROLE


def test_ingest_report_history_and_ready_as_app_role(api, engine, monkeypatch):
    run = uuid.uuid4()
    inc, evs = lifecycle(run)
    assert post_frame(api, frame_payload(evs, run_id=run)).status_code == 200
    assert api.post("/ingest/report", json=report(run)).status_code == 200
    assert api.get("/api/history/incidents").json()["items"][0]["incident_id"] == str(inc)
    for path in ("summary", "cameras", "runs", f"runs/{run}", f"incidents/{inc}"):
        assert api.get(f"/api/history/{path}").status_code == 200
    r = api.get("/health/ready")
    assert r.status_code == 200 and r.json()["schema"] == "current"


# ================================================================ migration rolü
def test_existing_install_ownership_transfer_and_migrator_ddl(scratch_db):
    """Üretimdeki gibi: tablolar süper kullanıcıya ait ve dolu -> setup -> migrator ile downgrade/upgrade."""
    admin = url_for(scratch_db)
    command.upgrade(alembic_cfg(admin), "head")
    run = uuid.uuid4()
    with psycopg_conn(admin) as c:
        c.execute("INSERT INTO cameras (id, name) VALUES ('cam1', 'x')")
        c.execute("INSERT INTO detector_runs (id, camera_id) VALUES (%s, 'cam1')", (run,))
    assert set(owners(scratch_db).values()) != {MIGRATOR_ROLE}
    setup_test_roles(scratch_db)
    setup_test_roles(scratch_db)                                        # idempotent
    assert set(owners(scratch_db).values()) == {MIGRATOR_ROLE}

    mig = url_for(scratch_db, MIGRATOR_ROLE)
    with psycopg_conn(mig) as c:
        s = roles.summary(c)
    assert not s["superuser"] and not s["createdb"] and not s["createrole"] and s["schema_create"]
    command.downgrade(alembic_cfg(mig), "0002")                         # 0003 sütunları boş: izinli
    command.upgrade(alembic_cfg(mig), "head")
    with psycopg_conn(mig) as c:
        assert roles.grants(c, app=APP_ROLE) is True                    # sahip olarak yeniden yetki
        assert c.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0005"

    app = url_for(scratch_db, APP_ROLE)
    with psycopg_conn(app) as c:                                        # veri korunmuş, yeni sütun yazılabilir
        assert c.execute("SELECT count(*) FROM detector_runs WHERE id = %s", (run,)).fetchone()[0] == 1
        c.execute("UPDATE detector_runs SET source_label = 'ok' WHERE id = %s", (run,))
    denied(app, "DELETE FROM detector_runs")
    denied(mig, "CREATE ROLE kotu_rol2")
    denied(mig, "CREATE DATABASE kotu_db2")


def test_fresh_install_roles_then_migrate(scratch_db):
    setup_test_roles(scratch_db)                                        # tablo yokken
    mig = url_for(scratch_db, MIGRATOR_ROLE)
    command.upgrade(alembic_cfg(mig), "head")
    with psycopg_conn(mig) as c:
        roles.grants(c, app=APP_ROLE)
    assert set(owners(scratch_db).values()) == {MIGRATOR_ROLE}
    app = url_for(scratch_db, APP_ROLE)
    with psycopg_conn(app) as c:
        c.execute("INSERT INTO cameras (id, name) VALUES ('cam9', 'yeni')")
        c.execute("INSERT INTO detector_runs (id, camera_id) VALUES (%s, 'cam9')", (uuid.uuid4(),))
    denied(app, "UPDATE alembic_version SET version_num = 'x'")
    denied(app, "TRUNCATE cameras")


def test_setup_requires_superuser(scratch_db):
    setup_test_roles(scratch_db)
    with psycopg_conn(url_for(scratch_db, MIGRATOR_ROLE)) as c:
        with pytest.raises(roles.RoleError, match="süper kullanıcı"):
            roles.setup(c, app=APP_ROLE, migrator=MIGRATOR_ROLE, app_password="x", migrator_password="y")


# ================================================================ CLI: eski kurulum / sır sızıntısı
def test_cli_without_passwords_is_noop(monkeypatch, capsys, db_url):
    monkeypatch.setenv("PPE_APP_ROLE", "ppe_never_created_test")
    monkeypatch.delenv("PPE_APP_PASSWORD", raising=False)
    monkeypatch.delenv("PPE_MIGRATOR_PASSWORD", raising=False)
    assert roles.main(["roles", "setup"]) == 0 and "ATLANDI" in capsys.readouterr().out
    with psycopg_conn(url_for()) as c:
        assert c.execute("SELECT count(*) FROM pg_roles WHERE rolname = 'ppe_never_created_test'").fetchone()[0] == 0


def test_cli_errors_do_not_leak_secrets(monkeypatch, capsys):
    monkeypatch.setenv("PPE_APP_PASSWORD", "APP_GIZLI_PAROLA")
    monkeypatch.setenv("PPE_MIGRATOR_PASSWORD", "MIG_GIZLI_PAROLA")
    monkeypatch.setenv("ADMIN_DATABASE_URL", "postgresql+psycopg://admin:ADMIN_GIZLI@olmayan-db:5432/x")
    assert roles.main(["roles", "setup"]) == 1
    out = capsys.readouterr()
    for secret in ("APP_GIZLI_PAROLA", "MIG_GIZLI_PAROLA", "ADMIN_GIZLI", "olmayan-db"):
        assert secret not in out.out + out.err


@pytest.mark.parametrize("name", ["Robert'); DROP TABLE x;--", "app-role", "x" * 64, "1abc"])
def test_role_names_validated(monkeypatch, name):
    monkeypatch.setenv("PPE_APP_ROLE", name)
    with pytest.raises(roles.RoleError):
        roles.role_names()


def test_passwords_stored_hashed_not_plain(db_url):
    with psycopg_conn(url_for()) as c:
        stored = c.execute("SELECT rolpassword FROM pg_authid WHERE rolname = %s", (APP_ROLE,)).fetchone()[0]
    assert stored.startswith("SCRAM-SHA-256$") and ROLE_PASSWORDS[APP_ROLE] not in stored
