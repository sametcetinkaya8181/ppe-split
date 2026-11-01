"""Şema kısıtları: PK, FK, UNIQUE, CHECK, index, TIMESTAMPTZ (gerçek PostgreSQL 17)."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import ProgrammingError

from db import PersonRunReport
from tests.conftest import (CHECK, EVENT_INSERT, EVENT_SQL, FK, INCIDENT_CTE, NOT_NULL, UNIQUE, event, seed,
                            violates)


# ---------------------------------------------------------------- event_id idempotency
def test_event_id_unique(conn):
    r = seed(conn)
    ev = event(r["r1"])
    conn.execute(text(EVENT_SQL), ev)
    violates(conn, EVENT_SQL, {**ev, "incident_id": uuid.uuid4()}, UNIQUE)


def test_event_id_on_conflict_do_nothing(conn):
    r = seed(conn)
    ev = event(r["r1"])
    sql = EVENT_SQL + " ON CONFLICT (event_id) DO NOTHING RETURNING id"
    assert conn.execute(text(sql), ev).scalar() is not None
    assert conn.execute(text(sql), ev).scalar() is None          # retry: yeni satır yok
    assert conn.execute(text("SELECT count(*) FROM violation_events WHERE event_id = :e"),
                        {"e": ev["event_id"]}).scalar() == 1


def test_id_is_server_generated(conn):
    r = seed(conn)
    sql = INCIDENT_CTE + EVENT_INSERT.replace("(event_id,", "(id, event_id,", 1).replace("VALUES (", "VALUES (99, ", 1)
    with pytest.raises(ProgrammingError) as ei:                   # GENERATED ALWAYS: elle id verilemez
        with conn.begin_nested():
            conn.execute(text(sql), event(r["r1"]))
    assert ei.value.orig.sqlstate == "428C9"


# ---------------------------------------------------------------- incident_id
def test_incident_id_required(conn):
    r = seed(conn)
    violates(conn, EVENT_SQL, event(r["r1"], incident_id=None), NOT_NULL)


def test_incident_lifecycle_and_partial_uniques(conn):
    r = seed(conn)
    inc = uuid.uuid4()
    conn.execute(text(EVENT_SQL), event(r["r1"], incident_id=inc))
    for _ in range(2):                                            # birden çok ongoing serbest
        conn.execute(text(EVENT_SQL), event(r["r1"], incident_id=inc, event_type="ongoing"))
    end = dict(event_type="end", missing_helmet=False, missing_vest=False, missing_text="")
    conn.execute(text(EVENT_SQL), event(r["r1"], incident_id=inc, **end))
    violates(conn, EVENT_SQL, event(r["r1"], incident_id=inc), UNIQUE)          # ikinci start yok
    violates(conn, EVENT_SQL, event(r["r1"], incident_id=inc, **end), UNIQUE)   # ikinci end yok
    # aynı run + aynı pid, yeni ihlal -> yeni incident serbest
    conn.execute(text(EVENT_SQL), event(r["r1"], incident_id=uuid.uuid4()))
    assert conn.execute(text("SELECT count(*) FROM violation_events WHERE incident_id = :i"), {"i": inc}).scalar() == 4


def test_indexes_exist(conn):
    rows = dict(conn.execute(text("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'violation_events'")).all())
    assert "(incident_id, occurred_at, id)" in rows["ix_violation_events_incident"]
    assert "WHERE" in rows["uq_violation_events_incident_start"] and "UNIQUE" in rows["uq_violation_events_incident_start"]
    assert "WHERE" in rows["uq_violation_events_incident_end"]
    assert "(camera_id, occurred_at DESC, id DESC)" in rows["ix_violation_events_camera_occurred"]
    assert "(run_id, pid, id)" in rows["ix_violation_events_run_pid"]
    assert "(event_type, occurred_at DESC)" in rows["ix_violation_events_type_occurred"]
    assert "uq_violation_events_event_id" in rows
    runs = conn.execute(text("SELECT indexname FROM pg_indexes WHERE tablename = 'detector_runs'")).scalars().all()
    assert {"ix_detector_runs_camera_started", "uq_detector_runs_id_camera_id"} <= set(runs)


# ---------------------------------------------------------------- FK + run/kamera eşleşmesi
def test_event_unknown_run_rejected(conn):
    seed(conn)
    violates(conn, EVENT_SQL, event(uuid.uuid4()), FK)


def test_event_camera_must_match_run(conn):
    r = seed(conn)
    violates(conn, EVENT_SQL, event(r["r1"], camera_id="cam2"), FK)   # r1 cam1'e ait
    conn.execute(text(EVENT_SQL), event(r["r2"], camera_id="cam2"))


def test_run_unknown_camera_rejected(conn):
    violates(conn, "INSERT INTO detector_runs (id, camera_id) VALUES (:i, 'yok')", {"i": uuid.uuid4()}, FK)


def test_same_pid_in_two_runs_is_independent(conn):
    r = seed(conn)
    conn.execute(text("INSERT INTO detector_runs (id, camera_id, loop_no) VALUES (:i, 'cam1', 2)"), {"i": (r3 := uuid.uuid4())})
    conn.execute(text(EVENT_SQL), event(r["r1"], pid=3))
    conn.execute(text(EVENT_SQL), event(r3, pid=3))
    assert conn.execute(text("SELECT count(DISTINCT run_id) FROM violation_events WHERE pid = 3")).scalar() == 2


def test_delete_with_history_is_restricted(conn):
    r = seed(conn)
    conn.execute(text(EVENT_SQL), event(r["r1"]))
    violates(conn, "DELETE FROM detector_runs WHERE id = :i", {"i": r["r1"]}, FK)
    violates(conn, "DELETE FROM cameras WHERE id = 'cam1'", {}, FK)


# ---------------------------------------------------------------- CHECK kısıtları
END = dict(event_type="end", missing_helmet=False, missing_vest=False, missing_text="")


@pytest.mark.parametrize("over", [
    dict(event_type="foo"),
    dict(helmet_rate=101), dict(vest_rate=-1),
    dict(pid=-1), dict(video_time_sec=-0.1),
    dict(missing_helmet=False, missing_vest=False),                 # start ama eksik yok
    dict(event_type="ongoing", missing_helmet=False, missing_vest=False),
    dict(END, missing_helmet=True),                                 # end ama eksik var
    dict(box="[1, 2, 3]"), dict(box="[1, 2, 3, 4, 5]"), dict(box='{"x": 1}'), dict(box='"kutu"'),
    dict(box='[1, 2, "3", 4]'), dict(box="[10, 2, 5, 4]"), dict(box="[-1, 2, 3, 4]"),
    dict(event_type="ongoing", snapshot_path="snapshots/alert_a.jpg"),
    dict(snapshot_path="/data/snapshots/a.jpg"), dict(snapshot_path="snapshots/../x.jpg"),
    dict(snapshot_path="snapshots/a.png"), dict(snapshot_path="snapshots/a b.jpg"),
], ids=lambda o: ",".join(f"{k}={v}" for k, v in o.items()))
def test_event_checks_reject(conn, over):
    r = seed(conn)
    violates(conn, EVENT_SQL, event(r["r1"], **over), CHECK)


@pytest.mark.parametrize("over", [
    dict(), dict(missing_helmet=False, missing_vest=True), dict(missing_helmet=True, missing_vest=True),
    dict(event_type="ongoing"), END, dict(END, box=None, helmet_rate=None, vest_rate=None, video_time_sec=None),
    dict(box="[0, 0, 0.5, 0.5]"), dict(snapshot_path=f"snapshots/alert_{uuid.uuid4()}.jpg"),
], ids=lambda o: ",".join(f"{k}={v}" for k, v in o.items()) or "varsayilan")
def test_event_checks_accept(conn, over):
    r = seed(conn)
    conn.execute(text(EVENT_SQL), event(r["r1"], **over))


def test_snapshot_path_unique(conn):
    r = seed(conn)
    p = "snapshots/alert_x.jpg"
    conn.execute(text(EVENT_SQL), event(r["r1"], snapshot_path=p))
    violates(conn, EVENT_SQL, event(r["r1"], snapshot_path=p), UNIQUE)


@pytest.mark.parametrize("sql,params", [
    ("INSERT INTO cameras (id, name) VALUES ('cam 1', 'x')", {}),
    ("INSERT INTO cameras (id, name) VALUES ('cam/../1', 'x')", {}),
    ("INSERT INTO cameras (id, name) VALUES ('cam9', '')", {}),
    ("INSERT INTO cameras (id, name, source_kind) VALUES ('cam9', 'x', 'ftp')", {}),
    ("INSERT INTO detector_runs (id, camera_id, status) VALUES (:i, 'cam1', 'paused')", None),
    ("INSERT INTO detector_runs (id, camera_id, status) VALUES (:i, 'cam1', 'finished')", None),   # finished_at yok
    ("INSERT INTO detector_runs (id, camera_id, finished_at) VALUES (:i, 'cam1', now())", None),  # running + finished_at
    ("INSERT INTO detector_runs (id, camera_id, status, started_at, finished_at) "
     "VALUES (:i, 'cam1', 'finished', now(), now() - interval '1 s')", None),
    ("INSERT INTO detector_runs (id, camera_id, loop_no) VALUES (:i, 'cam1', 0)", None),
    ("INSERT INTO detector_runs (id, camera_id, fps) VALUES (:i, 'cam1', 0)", None),
    ("INSERT INTO detector_runs (id, camera_id, width) VALUES (:i, 'cam1', -5)", None),
], ids=["kamera_bosluk", "kamera_yol", "isim_bos", "kaynak_turu", "durum", "finished_bitis_yok",
        "running_bitisli", "bitis_once", "loop0", "fps0", "genislik"])
def test_camera_and_run_checks(conn, sql, params):
    seed(conn)
    violates(conn, sql, params if params is not None else {"i": uuid.uuid4()}, CHECK)


# ---------------------------------------------------------------- person_run_reports
def _report(run_id, **over):
    row = dict(run_id=run_id, pid=4, first_sec=1.0, last_sec=9.5, observations=40, helmet_rate=10, vest_rate=90,
               ever_violation=True)
    row.update(over)
    return row


def test_report_composite_pk_and_upsert(conn):
    r = seed(conn)
    conn.execute(insert(PersonRunReport), [_report(r["r1"])])
    conn.execute(insert(PersonRunReport), [_report(r["r2"])])       # aynı pid, başka run: serbest
    violates(conn, "INSERT INTO person_run_reports (run_id, pid, first_sec, last_sec, observations, helmet_rate, "
             "vest_rate, ever_violation) VALUES (:run_id, :pid, :first_sec, :last_sec, :observations, :helmet_rate, "
             ":vest_rate, :ever_violation)", _report(r["r1"]), UNIQUE)
    stmt = insert(PersonRunReport).values(_report(r["r1"], last_sec=20.0, observations=80))
    conn.execute(stmt.on_conflict_do_update(index_elements=["run_id", "pid"],
                                            set_={"last_sec": stmt.excluded.last_sec,
                                                  "observations": stmt.excluded.observations}))
    rows = conn.execute(text("SELECT last_sec, observations FROM person_run_reports WHERE run_id = :r AND pid = 4"),
                        {"r": r["r1"]}).all()
    assert len(rows) == 1 and float(rows[0][0]) == 20.0 and rows[0][1] == 80


@pytest.mark.parametrize("over", [dict(last_sec=0.5), dict(first_sec=-1), dict(observations=0),
                                  dict(helmet_rate=101), dict(vest_rate=-1), dict(pid=-2)])
def test_report_checks(conn, over):
    r = seed(conn)
    violates(conn, "INSERT INTO person_run_reports (run_id, pid, first_sec, last_sec, observations, helmet_rate, "
             "vest_rate, ever_violation) VALUES (:run_id, :pid, :first_sec, :last_sec, :observations, :helmet_rate, "
             ":vest_rate, :ever_violation)", _report(r["r1"], **over), CHECK)


def test_report_unknown_run_rejected(conn):
    seed(conn)
    violates(conn, "INSERT INTO person_run_reports (run_id, pid, first_sec, last_sec, observations, helmet_rate, "
             "vest_rate, ever_violation) VALUES (:run_id, :pid, :first_sec, :last_sec, :observations, :helmet_rate, "
             ":vest_rate, :ever_violation)", _report(uuid.uuid4()), FK)


# ---------------------------------------------------------------- zaman
def test_all_timestamps_are_timestamptz(conn):
    rows = conn.execute(text("SELECT table_name, column_name, data_type FROM information_schema.columns "
                             "WHERE table_schema = 'public' AND column_name LIKE '%\\_at' ESCAPE '\\'")).all()
    assert len(rows) == 17                                    # 0004: +7, 0005: +1 (reconciled_at)
    assert {r.data_type for r in rows} == {"timestamp with time zone"}, rows


def test_occurred_at_and_video_time_are_separate(conn):
    r = seed(conn)
    local = datetime(2026, 10, 28, 14, 29, tzinfo=timezone(timedelta(hours=3)))   # İstanbul saati
    conn.execute(text(EVENT_SQL), event(r["r1"], occurred_at=local, video_time_sec=12.4))
    conn.execute(text("SET LOCAL TIME ZONE 'UTC'"))
    occ, vt, ing = conn.execute(text("SELECT occurred_at, video_time_sec, ingested_at FROM violation_events")).one()
    assert occ == datetime(2026, 10, 28, 11, 29, tzinfo=timezone.utc) and occ.utcoffset() == timedelta(0)
    assert float(vt) == 12.4
    assert ing.tzinfo is not None
