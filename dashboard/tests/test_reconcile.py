"""FAZ 7: çökmüş run uzlaştırması (0005 'abandoned'). Yalnız ppe_test; uygulama ppe_app_test rolüyle."""
import uuid

import pytest
from alembic import command
from sqlalchemy import text

import app as app_module
from db.session import get_session
from maintenance import runs
from tests.conftest import alembic_cfg
from tests.helpers import event, frame_payload, lifecycle, post_frame, report


def sql(engine, q, **p):
    with engine.begin() as c:
        return c.execute(text(q), p)


def make_runs(api, engine):
    stale, fresh, done = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    for r in (stale, fresh, done):
        assert post_frame(api, frame_payload([event("start", run_id=r, incident_id=uuid.uuid4())], run_id=r)).status_code == 200
    assert api.post("/ingest/report", json=report(done)).status_code == 200
    sql(engine, "UPDATE detector_runs SET last_seen_at = now() - interval '3 hours' WHERE id IN (:a, :b)", a=stale, b=done)
    return stale, fresh, done


def test_dry_run_lists_only_stale_running_without_report(api, engine, capsys):
    stale, fresh, done = make_runs(api, engine)
    with get_session() as s:
        ids = {c["id"] for c in runs.candidates(s, 60)}
    assert ids == {stale}
    assert runs.main(["reconcile"]) == 0 and "DRY RUN" in capsys.readouterr().out
    assert sql(engine, "SELECT count(*) FROM detector_runs WHERE status = 'running'").scalar() == 2   # değişmedi


def test_apply_requires_exact_confirmation_and_audits(api, engine):
    stale, fresh, done = make_runs(api, engine)
    with pytest.raises(SystemExit, match="Onay uyuşmuyor"):
        with get_session() as s, s.begin():
            runs.apply(s, 60, confirm=5)
    assert sql(engine, "SELECT status FROM detector_runs WHERE id = :r", r=stale).scalar() == "running"
    assert runs.main(["reconcile", "--apply", "--confirm", "1"]) == 0
    row = sql(engine, "SELECT status, finished_at = last_seen_at, reconciled_at IS NOT NULL, reconcile_reason "
                      "FROM detector_runs WHERE id = :r", r=stale).one()
    assert tuple(row) == ("abandoned", True, True, "no_heartbeat_60m")           # sahte 'finished' yok
    assert sql(engine, "SELECT status FROM detector_runs WHERE id = :r", r=fresh).scalar() == "running"
    assert sql(engine, "SELECT status FROM detector_runs WHERE id = :r", r=done).scalar() == "finished"
    audit = sql(engine, "SELECT event, detail->>'run_id' FROM audit_log WHERE event = 'run_reconcile'").all()
    assert audit == [("run_reconcile", str(stale))]


def test_history_shows_abandoned_honestly(api, engine):
    stale, _, _ = make_runs(api, engine)
    runs.main(["reconcile", "--apply", "--confirm", "1"])
    inc = [i for i in api.get("/api/history/incidents").json()["items"] if i["run_id"] == str(stale)][0]
    assert inc["status"] == "unknown" and inc["status_reason"] == "run_abandoned"
    r = api.get(f"/api/history/runs/{stale}").json()
    assert r["status"] == "abandoned" and r["liveness"] is None
    assert [x["run_id"] for x in api.get("/api/history/runs", params={"status": "abandoned"}).json()["items"]] == [str(stale)]


def test_reconciliation_reverts_on_new_evidence(api, engine):
    stale, _, _ = make_runs(api, engine)
    runs.main(["reconcile", "--apply", "--confirm", "1"])
    app_module._HB.clear()
    assert post_frame(api, frame_payload([], run_id=stale)).status_code == 200      # run aslında canlıymış
    app_module.flush_heartbeats()
    assert tuple(sql(engine, "SELECT status, finished_at FROM detector_runs WHERE id = :r", r=stale).one()) == \
        ("running", None)
    sql(engine, "UPDATE detector_runs SET last_seen_at = now() - interval '3 hours' WHERE id = :r", r=stale)
    runs.main(["reconcile", "--apply", "--confirm", "1"])
    assert api.post("/ingest/report", json=report(stale)).status_code == 200       # geç gelen rapor kazanır
    assert sql(engine, "SELECT status FROM detector_runs WHERE id = :r", r=stale).scalar() == "finished"


def test_threshold_floor(capsys):
    assert runs.main(["reconcile", "--stale-minutes", "1"]) == 2


def test_0005_downgrade_refuses_with_abandoned_rows(api, engine, db_url):
    stale, _, _ = make_runs(api, engine)
    runs.main(["reconcile", "--apply", "--confirm", "1"])
    with pytest.raises(RuntimeError, match="uzlaştırılmış"):
        command.downgrade(alembic_cfg(), "0004")
    sql(engine, "TRUNCATE violation_events, incidents, person_run_reports, detector_runs, cameras, user_sessions, "
                "audit_log, app_users")
    command.downgrade(alembic_cfg(), "0004")
    command.upgrade(alembic_cfg(), "head")
