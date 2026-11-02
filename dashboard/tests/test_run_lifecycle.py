"""FAZ 10-A run durum makinesi (0006 'disconnected'): canlı kaynak kopması normal bitişten ayrı kaydedilir;
end_reason doğrulanır ve run_status ile çelişemez; terminal run canlılık/olayla yeniden açılmaz; aynı raporun tekrarı
idempotent, farklı rapor 409; 'abandoned' -> gerçek rapor geçişi; eski (end_reason'sız) raporların birebir tekrarı
bozulmaz; geçmiş API filtresi ve özet; 0006 downgrade koruması. Gerçek PostgreSQL 17, ppe_app_test rolü."""
import uuid

import pytest
from alembic import command
from sqlalchemy import text

from maintenance import runs
from tests.conftest import alembic_cfg
from tests.helpers import event, frame_payload, post_frame, report


def sql(engine, q, **p):
    with engine.begin() as c:
        return c.execute(text(q), p)


def started_run(api, cam="cam1"):
    r = uuid.uuid4()
    assert post_frame(api, frame_payload([event("start", run_id=r, incident_id=uuid.uuid4(), cam=cam)], run_id=r,
                                         cam=cam)).status_code == 200
    return r


def status_of(engine, r):
    return tuple(sql(engine, "SELECT status, finished_at IS NOT NULL, report_metadata->>'end_reason' "
                             "FROM detector_runs WHERE id = :r", r=r).one())


@pytest.mark.parametrize("run_status,end_reason", [
    ("disconnected", "source_lost"), ("disconnected", "source_lost_after_stall"), ("finished", "eof"),
    ("interrupted", "user_stop"), ("error", "exception")])
def test_report_statuses_and_end_reasons_persist(api, engine, run_status, end_reason):
    r = started_run(api)
    body = report(r, status=run_status, end_reason=end_reason, source_kind="rtsp")
    assert api.post("/ingest/report", json=body).status_code == 200
    assert status_of(engine, r) == (run_status, True, end_reason)
    h = api.get(f"/api/history/runs/{r}").json()
    assert h["status"] == run_status and h["report"]["end_reason"] == end_reason and h["liveness"] is None


@pytest.mark.parametrize("run_status,end_reason", [("finished", "source_lost"), ("disconnected", "eof"),
                                                   ("disconnected", "kablo_koptu"), ("bitti", None)])
def test_contradicting_or_unknown_values_rejected_422(api, engine, run_status, end_reason):
    r = started_run(api)
    assert api.post("/ingest/report", json=report(r, status=run_status, end_reason=end_reason)).status_code == 422
    assert status_of(engine, r)[0] == "running"


def test_terminal_disconnected_not_reopened_and_replay_is_idempotent(api, engine):
    r = started_run(api)
    body = report(r, status="disconnected", end_reason="source_lost")
    assert api.post("/ingest/report", json=body).status_code == 200
    assert api.post("/ingest/report", json=body).status_code == 200                # outbox tekrarı (ACK kaybı)
    assert api.post("/ingest/report", json=report(r, status="finished", end_reason="eof")).status_code == 409
    # aynı run'a gecikmiş canlılık/olay: durum geri açılmaz
    assert post_frame(api, frame_payload([], run_id=r)).status_code == 200
    from app import flush_heartbeats
    flush_heartbeats()
    late = event("ongoing", run_id=r, incident_id=uuid.uuid4())
    post_frame(api, frame_payload([late], run_id=r))
    assert status_of(engine, r) == ("disconnected", True, "source_lost")
    assert sql(engine, "SELECT count(*) FROM person_run_reports WHERE run_id = :r", r=r).scalar() == 1


def test_old_report_without_end_reason_replays_identically(api, engine):
    """Yükseltmeden önce kaydedilmiş rapor (end_reason yok) outbox'tan yeniden gelirse 409 OLMAMALI."""
    r = started_run(api)
    old = report(r)                                                                  # eski dedektör: alan yok
    assert api.post("/ingest/report", json=old).status_code == 200
    assert api.post("/ingest/report", json=old).status_code == 200
    md = sql(engine, "SELECT report_metadata FROM detector_runs WHERE id = :r", r=r).scalar()
    assert "end_reason" not in md and status_of(engine, r)[0] == "finished"


def test_abandoned_run_receives_late_disconnected_report(api, engine):
    r = started_run(api)
    sql(engine, "UPDATE detector_runs SET last_seen_at = now() - interval '3 hours' WHERE id = :r", r=r)
    assert runs.main(["reconcile", "--apply", "--confirm", "1"]) == 0
    assert status_of(engine, r)[0] == "abandoned"
    assert api.post("/ingest/report", json=report(r, status="disconnected", end_reason="source_lost")).status_code == 200
    assert status_of(engine, r) == ("disconnected", True, "source_lost")


def test_history_filter_and_incident_status_for_disconnected_run(api, engine):
    r = started_run(api)
    other = started_run(api, cam="cam2")
    inc = sql(engine, "SELECT id FROM incidents WHERE run_id = :r", r=r).scalar()
    body = report(r, status="disconnected", end_reason="source_lost",
                  unclosed_incidents=[{"incident_id": str(inc), "pid": 7, "reason": "run_end", "t": 2.1}])
    assert api.post("/ingest/report", json=body).status_code == 200
    items = api.get("/api/history/runs", params={"status": "disconnected"}).json()["items"]
    assert [i["run_id"] for i in items] == [str(r)] and str(other) not in {i["run_id"] for i in items}
    i = [x for x in api.get("/api/history/incidents").json()["items"] if x["run_id"] == str(r)][0]
    assert i["status"] == "run_end"                                  # ihlal sürerken kaynak koptu; sahte 'closed' yok
    assert api.get("/api/history/runs", params={"status": "kopuk"}).status_code == 422


def test_0006_downgrade_refuses_with_disconnected_rows(api, engine, db_url):
    r = started_run(api)
    assert api.post("/ingest/report", json=report(r, status="disconnected", end_reason="source_lost")).status_code == 200
    with pytest.raises(RuntimeError, match="disconnected"):
        command.downgrade(alembic_cfg(), "0005")
    sql(engine, "TRUNCATE violation_events, incidents, person_run_reports, detector_runs, cameras, user_sessions, "
                "audit_log, app_users")
    command.downgrade(alembic_cfg(), "0005")
    with pytest.raises(Exception):                                   # 0005 şeması yeni durumu kabul etmez
        sql(engine, "INSERT INTO cameras (id, name) VALUES ('dc', 'x'); "
                    "INSERT INTO detector_runs (id, camera_id, status, finished_at) "
                    "VALUES (gen_random_uuid(), 'dc', 'disconnected', now())")
    command.upgrade(alembic_cfg(), "head")
    from tests.conftest import regrant_app
    regrant_app()
