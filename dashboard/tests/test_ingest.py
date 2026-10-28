"""FAZ 3: /ingest/frame ve /ingest/report kalıcı yazımı (gerçek PostgreSQL 17, ayrı ppe_test veritabanı)."""
import base64
import json
import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

import app as app_module
from db import repository as repo
from ingest import snapshots
from tests.conftest import count
from tests.helpers import (B64_A, B64_B, FRAME, JPEG_A, JPEG_B, T0, event, frame_payload, iso, lifecycle,
                           post_frame, report)


def rows(engine, sql, **p):
    with engine.connect() as c:
        return c.execute(text(sql), p).mappings().all()


def ram_alerts():
    return list(app_module.ALERTS)


# ================================================================ A. olay kalıcılığı
def test_single_start_persisted_with_snapshot(api, engine):
    run, inc = uuid.uuid4(), uuid.uuid4()
    ev = event("start", run_id=run, incident_id=inc, snapshot=B64_A)
    r = post_frame(api, frame_payload([ev], run_id=run))
    assert r.status_code == 200 and r.json() == {"ok": True}
    (row,) = rows(engine, "SELECT * FROM violation_events")
    assert str(row["event_id"]) == ev["event_id"] and str(row["incident_id"]) == str(inc)
    assert str(row["run_id"]) == str(run) and row["camera_id"] == "cam1" and row["pid"] == 7
    assert row["event_type"] == "start" and row["missing_helmet"] and not row["missing_vest"]
    assert str(row["video_time_sec"]) == "2.1" and row["occurred_at"] == T0
    assert row["box"] == [100, 100, 200, 400]
    assert row["snapshot_path"] == f"snapshots/alert_{ev['event_id']}.jpg"
    assert (app_module.SNAP_DIR / f"alert_{ev['event_id']}.jpg").read_bytes() == JPEG_A
    (run_row,) = rows(engine, "SELECT * FROM detector_runs")
    assert run_row["status"] == "running" and run_row["finished_at"] is None
    assert run_row["fps"] == 25 and run_row["width"] == 1280 and run_row["loop_no"] == 1 and run_row["started_at"] == T0
    (cam,) = rows(engine, "SELECT * FROM cameras")
    assert cam["id"] == "cam1" and cam["source_kind"] == "file" and cam["last_seen_at"] is not None
    a = ram_alerts()[0]
    assert a["snapshot"] == f"/snapshots/alert_{ev['event_id']}.jpg" and "snapshot_b64" not in a
    assert api.get(a["snapshot"]).content == JPEG_A


def test_lifecycle_same_incident_and_new_incident(api, engine):
    run = uuid.uuid4()
    inc1, evs1 = lifecycle(run)
    inc2, evs2 = lifecycle(run)                                  # aynı pid, yeni ihlal
    for ev in evs1 + evs2:
        assert post_frame(api, frame_payload([ev], run_id=run)).status_code == 200
    got = rows(engine, "SELECT incident_id, event_type FROM violation_events ORDER BY id")
    assert [r["event_type"] for r in got] == ["start", "ongoing", "end"] * 2
    assert {r["incident_id"] for r in got[:3]} == {inc1} and {r["incident_id"] for r in got[3:]} == {inc2}
    assert count(engine, "incidents") == 2


def test_same_pid_different_runs_and_cameras_isolated(api, engine):
    r1, r2, r3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    for run, cam in ((r1, "cam1"), (r2, "cam1"), (r3, "cam2")):
        _, evs = lifecycle(run, cam=cam, pid=3)
        assert post_frame(api, frame_payload(evs, cam=cam, run_id=run)).status_code == 200
    got = rows(engine, "SELECT run_id, camera_id, count(*) n, count(DISTINCT incident_id) i FROM violation_events "
                       "GROUP BY run_id, camera_id")
    assert {(r["run_id"], r["camera_id"], r["n"], r["i"]) for r in got} == {(r1, "cam1", 3, 1), (r2, "cam1", 3, 1),
                                                                           (r3, "cam2", 3, 1)}
    assert count(engine, "cameras") == 2 and count(engine, "detector_runs") == 3


def test_mixed_run_batch_uses_each_events_own_ids(api, engine):
    old_run, new_run = uuid.uuid4(), uuid.uuid4()
    old_inc = uuid.uuid4()
    old_end = event("end", run_id=old_run, incident_id=old_inc, t=30.0)    # önceki run'ın son olayı
    new_start = event("start", run_id=new_run, incident_id=uuid.uuid4(), t=2.1, snapshot=B64_A)
    r = post_frame(api, frame_payload([old_end, new_start], run_id=new_run, loop=2, fps=30.0))
    assert r.status_code == 200
    got = {str(x["event_id"]): x for x in rows(engine, "SELECT event_id, run_id FROM violation_events")}
    assert got[old_end["event_id"]]["run_id"] == old_run and got[new_start["event_id"]]["run_id"] == new_run
    runs = {r["id"]: r for r in rows(engine, "SELECT id, loop_no, fps FROM detector_runs")}
    assert runs[new_run]["loop_no"] == 2 and float(runs[new_run]["fps"]) == 30.0     # kare meta'sı yalnızca kendi run'ına
    assert runs[old_run]["loop_no"] is None and runs[old_run]["fps"] is None


def test_orphan_event_without_start_is_stored_as_is(api, engine):
    run = uuid.uuid4()
    orphan = event("end", run_id=run, incident_id=uuid.uuid4(), orphan=True)
    assert post_frame(api, frame_payload([orphan], run_id=run)).status_code == 200
    assert [r["event_type"] for r in rows(engine, "SELECT event_type FROM violation_events")] == ["end"]


# ================================================================ B. idempotency
def test_exact_replay_creates_no_new_row_and_no_new_alert(api, engine):
    run = uuid.uuid4()
    _, evs = lifecycle(run)
    payload = frame_payload(evs, run_id=run)
    assert post_frame(api, payload).status_code == 200
    first_alerts = ram_alerts()
    for _ in range(3):                                           # ör. yanıt zaman aşımı sonrası Sender retry'ı
        assert post_frame(api, payload).status_code == 200
    assert count(engine, "violation_events") == 3 and count(engine, "incidents") == 1
    assert ram_alerts() == first_alerts and app_module.ALERT_SEQ == 3
    assert api.get("/api/state?after=3").json()["alerts"] == []


def test_duplicate_within_same_request(api, engine):
    run = uuid.uuid4()
    ev = event("start", run_id=run, incident_id=uuid.uuid4(), snapshot=B64_A)
    assert post_frame(api, frame_payload([ev, dict(ev)], run_id=run)).status_code == 200
    assert count(engine, "violation_events") == 1 and len(ram_alerts()) == 1
    other = dict(ev, helmet_rate=55)
    assert post_frame(api, frame_payload([ev, other], run_id=run)).status_code == 409


@pytest.mark.parametrize("field,value", [("incident_id", None), ("run_id", None), ("camera_id", "cam9"), ("pid", 8),
                                         ("type", "ongoing"), ("helmet_rate", 1), ("t", 2.2), ("box", [1, 1, 2, 2]),
                                         ("occurred_at", iso(T0 + timedelta(seconds=1)))])
def test_conflicting_replay_rejected_409(api, engine, field, value):
    run = uuid.uuid4()
    ev = event("start", run_id=run, incident_id=uuid.uuid4())
    assert post_frame(api, frame_payload([ev], run_id=run)).status_code == 200
    if value is None:
        value = str(uuid.uuid4())
    changed = dict(ev, **{field: value})
    r = post_frame(api, frame_payload([changed], run_id=run))
    assert r.status_code == 409, r.text
    assert count(engine, "violation_events") == 1 and len(ram_alerts()) == 1


def test_concurrent_duplicate_requests_single_row(api, engine):
    run = uuid.uuid4()
    _, evs = lifecycle(run)
    payload = frame_payload(evs, run_id=run)
    with ThreadPoolExecutor(8) as pool:
        codes = list(pool.map(lambda _: post_frame(api, payload).status_code, range(8)))
    assert codes == [200] * 8
    assert count(engine, "violation_events") == 3 and count(engine, "incidents") == 1
    assert len(ram_alerts()) == 3                                # yalnızca bir istek "yeni" saydı


def test_concurrent_incident_hijack_rejected(api, engine):
    """Aynı incident_id'yi eşzamanlı olarak farklı pid'lerle bağlama girişimi: yalnızca biri kazanır."""
    run, inc = uuid.uuid4(), uuid.uuid4()
    payloads = [frame_payload([event("start", run_id=run, incident_id=inc, pid=p)], run_id=run) for p in range(1, 7)]
    with ThreadPoolExecutor(6) as pool:
        codes = sorted(pool.map(lambda p: post_frame(api, p).status_code, payloads))
    assert codes == [200] + [409] * 5
    assert count(engine, "incidents") == 1 and count(engine, "violation_events") == 1


def test_incident_cannot_move_to_other_run_camera_or_pid(api, engine):
    run, other_run, inc = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    assert post_frame(api, frame_payload([event("start", run_id=run, incident_id=inc)], run_id=run)).status_code == 200
    for ev in (event("ongoing", run_id=other_run, incident_id=inc),
               event("ongoing", run_id=run, incident_id=inc, pid=99)):
        assert post_frame(api, frame_payload([ev], run_id=run)).status_code == 409
    assert count(engine, "violation_events") == 1


def test_second_start_for_same_incident_rejected(api, engine):
    run, inc = uuid.uuid4(), uuid.uuid4()
    assert post_frame(api, frame_payload([event("start", run_id=run, incident_id=inc)])).status_code == 200
    assert post_frame(api, frame_payload([event("start", run_id=run, incident_id=inc)])).status_code == 409


# ================================================================ C. transaction güvenliği
def test_invalid_event_in_batch_rejects_whole_batch(api, engine):
    run = uuid.uuid4()
    _, evs = lifecycle(run)
    evs[1]["helmet_rate"] = 101
    r = post_frame(api, frame_payload(evs, run_id=run))
    assert r.status_code == 422 and r.json()["detail"][0]["loc"][:2] == ["events", 1]
    assert count(engine, "violation_events") == 0 and count(engine, "detector_runs") == 0 and ram_alerts() == []


def test_fk_conflict_rolls_back_whole_batch(api, engine):
    run = uuid.uuid4()
    assert post_frame(api, frame_payload([event("start", run_id=run, incident_id=uuid.uuid4())],
                                         run_id=run)).status_code == 200
    ok = event("start", run_id=uuid.uuid4(), incident_id=uuid.uuid4(), cam="cam2")
    bad = event("start", run_id=run, incident_id=uuid.uuid4(), cam="cam2")      # run cam1'e ait
    r = post_frame(api, frame_payload([ok, bad], cam="cam2"))
    assert r.status_code == 409
    assert count(engine, "violation_events") == 1 and count(engine, "cameras", "WHERE id = 'cam2'") == 0


def test_db_error_mid_transaction_rolls_back_and_503(api, engine, monkeypatch):
    real = repo.insert_events

    def boom(s, rows_, ids):
        real(s, rows_, ids)
        raise OperationalError("INSERT", {}, Exception("bağlantı koptu"))
    monkeypatch.setattr(repo, "insert_events", boom)
    run = uuid.uuid4()
    r = post_frame(api, frame_payload([event("start", run_id=run, incident_id=uuid.uuid4())], run_id=run))
    assert r.status_code == 503
    for t in ("violation_events", "incidents", "detector_runs", "cameras"):
        assert count(engine, t) == 0, t
    assert ram_alerts() == []


def test_commit_failure_returns_503_then_retry_succeeds(api, engine, monkeypatch):
    from sqlalchemy.orm import SessionTransaction
    real = SessionTransaction.commit
    calls = {"n": 0}

    def flaky(self, *a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OperationalError("COMMIT", {}, Exception("sunucu kapandı"))
        return real(self, *a, **k)
    monkeypatch.setattr(SessionTransaction, "commit", flaky)
    run = uuid.uuid4()
    payload = frame_payload([event("start", run_id=run, incident_id=uuid.uuid4(), snapshot=B64_A)], run_id=run)
    assert post_frame(api, payload).status_code == 503              # Sender: geçici -> aynı istekle tekrar
    assert count(engine, "violation_events") == 0 and ram_alerts() == []
    assert post_frame(api, payload).status_code == 200              # aynı event_id + aynı snapshot
    assert count(engine, "violation_events") == 1 and len(ram_alerts()) == 1


def test_db_unreachable_events_503_but_live_frames_200(api, engine, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:COK_GIZLI@olmayan-sunucu:5432/x")
    run = uuid.uuid4()
    r = post_frame(api, frame_payload([event("start", run_id=run, incident_id=uuid.uuid4())], run_id=run))
    assert r.status_code == 503 and "COK_GIZLI" not in r.text and "olmayan-sunucu" not in r.text
    assert post_frame(api, frame_payload([], run_id=run), frame=b"\xff\xd8CANLI\xff\xd9").status_code == 200
    with api.websocket_connect("/ws") as ws:
        assert ws.receive_bytes()[1:] == b"\xff\xd8CANLI\xff\xd9"   # canlı görüntü DB'den bağımsız
    assert api.post("/ingest/report", json=report(run)).status_code == 503
    assert ram_alerts() == []


def test_report_failure_mid_transaction_rolls_back(api, engine, monkeypatch):
    run = uuid.uuid4()
    real = repo.complete_run

    def boom(s, **kw):
        real(s, **kw)
        raise OperationalError("INSERT", {}, Exception("x"))
    monkeypatch.setattr(repo, "complete_run", boom)
    assert api.post("/ingest/report", json=report(run)).status_code == 503
    assert count(engine, "detector_runs") == 0 and count(engine, "person_run_reports") == 0
    assert api.get("/api/report").json() == []                     # RAM de güncellenmedi


# ================================================================ D. snapshot
@pytest.mark.parametrize("b64,why", [("!!!bozuk!!!", "base64"), ("aGVsbG8gZMO8bnlh", "JPEG"),
                                     (base64.b64encode(JPEG_A[:-2]).decode(), "JPEG"),
                                     (base64.b64encode(b"\xff\xd8" + b"\x00" * 50 + b"\xff\xd9").decode(), "JPEG")])
def test_invalid_snapshot_rejected(api, engine, b64, why):
    run = uuid.uuid4()
    r = post_frame(api, frame_payload([event("start", run_id=run, incident_id=uuid.uuid4(), snapshot=b64)]))
    assert r.status_code == 422 and why in r.text and b64 not in r.text
    assert count(engine, "violation_events") == 0 and list(app_module.SNAP_DIR.iterdir()) == []


def test_oversized_snapshot_rejected(api, engine, monkeypatch):
    monkeypatch.setattr(app_module, "MAX_SNAPSHOT_BYTES", 100)
    r = post_frame(api, frame_payload([event("start", run_id=uuid.uuid4(), incident_id=uuid.uuid4(), snapshot=B64_A)]))
    assert r.status_code == 422 and "sınırını" in r.text and count(engine, "violation_events") == 0


def test_snapshot_only_on_start(api, engine):
    r = post_frame(api, frame_payload([event("ongoing", run_id=uuid.uuid4(), incident_id=uuid.uuid4(), snapshot=B64_A)]))
    assert r.status_code == 422


def test_payload_path_is_never_trusted(api, engine):
    run = uuid.uuid4()
    ev = event("start", run_id=run, incident_id=uuid.uuid4(), snapshot=B64_A, snapshot_path="../../../x.jpg")
    ev["snapshot"] = "../../etc/passwd"                         # payload'dan gelen yol: yok sayılmalı
    assert post_frame(api, frame_payload([ev], run_id=run)).status_code == 200
    (row,) = rows(engine, "SELECT snapshot_path FROM violation_events")
    assert row["snapshot_path"] == f"snapshots/alert_{ev['event_id']}.jpg"
    assert ram_alerts()[0]["snapshot"] == f"/snapshots/alert_{ev['event_id']}.jpg"
    traversal = dict(event("start", run_id=run, incident_id=uuid.uuid4(), snapshot=B64_A), event_id="../../x")
    assert post_frame(api, frame_payload([traversal])).status_code == 422
    assert sorted(p.name for p in app_module.SNAP_DIR.iterdir()) == [f"alert_{ev['event_id']}.jpg"]


def test_snapshot_retry_same_content_is_idempotent_and_different_content_conflicts(api, engine):
    run = uuid.uuid4()
    ev = event("start", run_id=run, incident_id=uuid.uuid4(), snapshot=B64_A)
    path = app_module.SNAP_DIR / f"alert_{ev['event_id']}.jpg"
    assert post_frame(api, frame_payload([ev], run_id=run)).status_code == 200
    mtime = path.stat().st_mtime_ns
    assert post_frame(api, frame_payload([ev], run_id=run)).status_code == 200
    assert path.stat().st_mtime_ns == mtime                        # üzerine yazılmadı
    r = post_frame(api, frame_payload([dict(ev, snapshot_b64=B64_B)], run_id=run))
    assert r.status_code == 409 and path.read_bytes() == JPEG_A
    assert count(engine, "violation_events") == 1


def test_concurrent_snapshot_writes_same_event_id(tmp_path):
    eid = uuid.uuid4()
    with ThreadPoolExecutor(8) as pool:
        same = list(pool.map(lambda _: snapshots.store(tmp_path, eid, JPEG_A), range(8)))
    assert set(same) == {f"snapshots/alert_{eid}.jpg"}
    eid2 = uuid.uuid4()
    barrier = threading.Barrier(2)

    def write(data):
        barrier.wait()
        try:
            snapshots.store(tmp_path, eid2, data)
            return "ok"
        except snapshots.SnapshotConflict:
            return "conflict"
    with ThreadPoolExecutor(2) as pool:
        results = sorted(pool.map(write, [JPEG_A, JPEG_B]))
    assert results == ["conflict", "ok"]
    assert (tmp_path / f"alert_{eid2}.jpg").read_bytes() in (JPEG_A, JPEG_B)
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".tmp-")]


def test_existing_old_snapshot_files_untouched(api, engine):
    old = {"alert_1_cam2_ID3.jpg": b"eski-1", "alert_5_cam3_ID160.jpg": b"eski-2"}
    for n, b in old.items():
        (app_module.SNAP_DIR / n).write_bytes(b)
    run = uuid.uuid4()
    assert post_frame(api, frame_payload([event("start", run_id=run, incident_id=uuid.uuid4(), snapshot=B64_A)],
                                         run_id=run)).status_code == 200
    for n, b in old.items():
        assert (app_module.SNAP_DIR / n).read_bytes() == b


def test_is_jpeg_on_real_samples():
    assert snapshots.is_jpeg(JPEG_A) and snapshots.is_jpeg(JPEG_B)
    assert not snapshots.is_jpeg(b"GIF89a" + JPEG_A[6:]) and not snapshots.is_jpeg(JPEG_A[:100] + b"\xff\xd9")


# ================================================================ E. run raporları
def test_report_persists_rows_and_metadata(api, engine):
    run = uuid.uuid4()
    inc = uuid.uuid4()
    body = report(run, rows=[{"pid": 7, "first": 0.1, "last": 9.5, "n": 40, "helmet": 10, "vest": 95, "violation": True},
                             {"pid": 9, "first": 3.0, "last": 4.0, "n": 8, "helmet": 100, "vest": 100, "violation": False}],
                  unclosed_incidents=[{"incident_id": str(inc), "pid": 7, "reason": "stale", "t": 42.0}],
                  orphan_events=2, unclosed_overflow=1, error="akış koptu rtsp://admin:S3cr3t@10.0.0.5/s",
                  extra_unknown_field={"x": 1})
    assert api.post("/ingest/report", json=body).status_code == 200
    (run_row,) = rows(engine, "SELECT * FROM detector_runs")
    assert run_row["status"] == "finished" and run_row["finished_at"] == T0 + timedelta(seconds=30)
    assert run_row["started_at"] == T0 - timedelta(seconds=3) and run_row["loop_no"] == 1
    md = run_row["report_metadata"]
    assert set(md) == {"unclosed_incidents", "unclosed_overflow", "orphan_events", "error", "video_time_sec"}
    assert md["unclosed_incidents"] == [{"incident_id": str(inc), "pid": 7, "reason": "stale", "t": 42.0}]
    assert md["orphan_events"] == 2 and "S3cr3t" not in md["error"] and "***@10.0.0.5" in md["error"]
    got = rows(engine, "SELECT pid, first_sec, last_sec, observations, helmet_rate, vest_rate, ever_violation "
                       "FROM person_run_reports ORDER BY pid")
    assert [(r["pid"], str(r["first_sec"]), str(r["last_sec"]), r["observations"], r["ever_violation"]) for r in got] == \
           [(7, "0.1", "9.5", 40, True), (9, "3.0", "4.0", 8, False)]


def test_report_replay_is_idempotent_and_conflicts_are_409(api, engine):
    run = uuid.uuid4()
    body = report(run)
    assert api.post("/ingest/report", json=body).status_code == 200
    assert api.post("/ingest/report", json=body).status_code == 200
    assert count(engine, "person_run_reports") == 1 and count(engine, "detector_runs") == 1
    for changed in (dict(body, run_status="error"), dict(body, finished_at=iso(T0 + timedelta(seconds=31))),
                    dict(body, orphan_events=5),
                    dict(body, report=[dict(body["report"][0], n=41)]),
                    dict(body, camera_id="cam2", cam="cam2")):
        assert api.post("/ingest/report", json=changed).status_code == 409, changed
    (r,) = rows(engine, "SELECT status, observations FROM detector_runs JOIN person_run_reports ON run_id = id")
    assert r["status"] == "finished" and r["observations"] == 40


@pytest.mark.parametrize("status", ["finished", "error", "interrupted"])
def test_report_statuses_satisfy_check(api, engine, status):
    run = uuid.uuid4()
    assert api.post("/ingest/report", json=report(run, status=status)).status_code == 200
    (r,) = rows(engine, "SELECT status, finished_at FROM detector_runs")
    assert r["status"] == status and r["finished_at"] is not None


@pytest.mark.parametrize("over", [dict(run_status="running"), dict(run_status="bitti"),
                                  dict(finished_at=iso(T0 - timedelta(hours=1))),
                                  dict(started_at="2026-10-28T14:29:00+03:00"),
                                  dict(report=[{"pid": 7, "first": 5.0, "last": 1.0, "n": 1, "helmet": 1, "vest": 1,
                                                "violation": True}]),
                                  dict(report=[{"pid": 7, "first": 0.15, "last": 1.0, "n": 1, "helmet": 1, "vest": 1,
                                                "violation": True}]),
                                  dict(report=[{"pid": 7, "first": 0.1, "last": 1.0, "n": 1.5, "helmet": 1, "vest": 1,
                                                "violation": True}]),
                                  dict(unclosed_incidents=[{"incident_id": "x", "pid": 1, "reason": "stale"}]),
                                  dict(unclosed_incidents=[{"incident_id": str(uuid.uuid4()), "pid": 1,
                                                            "reason": "stale", "gizli": "veri"}]),
                                  dict(run_id=None), dict(camera_id="cam/../x")])
def test_report_validation_422(api, engine, over):
    body = report(uuid.uuid4())
    body.update(over)
    r = api.post("/ingest/report", json=body)
    assert r.status_code == 422, r.text
    assert count(engine, "detector_runs") == 0


def test_events_before_report_then_report_completes_run(api, engine):
    run = uuid.uuid4()
    _, evs = lifecycle(run)
    assert post_frame(api, frame_payload(evs, run_id=run)).status_code == 200
    (before,) = rows(engine, "SELECT status, started_at, fps FROM detector_runs")
    assert before["status"] == "running" and before["started_at"] == T0      # ilk olay zamanı (geçici)
    assert api.post("/ingest/report", json=report(run)).status_code == 200
    (after,) = rows(engine, "SELECT status, started_at, finished_at, fps FROM detector_runs")
    assert after["status"] == "finished" and after["started_at"] == T0 - timedelta(seconds=3)   # gerçek başlangıç
    assert after["fps"] == before["fps"] == 25                             # rapor fps'i NULL ile ezmedi


def test_finished_run_never_reopened(api, engine):
    run = uuid.uuid4()
    assert api.post("/ingest/report", json=report(run)).status_code == 200
    late = event("end", run_id=run, incident_id=uuid.uuid4(), orphan=True)
    assert post_frame(api, frame_payload([late], run_id=run)).status_code == 200     # geç gelen olay kabul
    app_module._HB.clear()
    assert post_frame(api, frame_payload([], run_id=run)).status_code == 200         # canlılık yazımı
    (r,) = rows(engine, "SELECT status, finished_at FROM detector_runs")
    assert r["status"] == "finished" and r["finished_at"] is not None


def test_run_cannot_move_to_other_camera(api, engine):
    run = uuid.uuid4()
    assert post_frame(api, frame_payload([event("start", run_id=run, incident_id=uuid.uuid4())],
                                         run_id=run)).status_code == 200
    assert api.post("/ingest/report", json=report(run, cam="cam2")).status_code == 409


# ================================================================ F. uyumluluk / canlı akış
def test_eventless_frames_do_not_write_every_frame(api, engine, monkeypatch):
    run = uuid.uuid4()
    calls = []
    real = repo.heartbeat
    monkeypatch.setattr(repo, "heartbeat", lambda s, **kw: (calls.append(kw), real(s, **kw)))
    for _ in range(20):
        assert post_frame(api, frame_payload([], run_id=run)).status_code == 200
    assert len(calls) == 1                                        # HEARTBEAT_SEC içinde tek yazım
    new_run = uuid.uuid4()
    assert post_frame(api, frame_payload([], run_id=new_run, loop=2)).status_code == 200
    assert len(calls) == 2                                        # run değişince hemen
    assert count(engine, "detector_runs") == 2 and count(engine, "violation_events") == 0


def test_dashboard_restart_keeps_db_records(api, engine):
    run = uuid.uuid4()
    _, evs = lifecycle(run)
    assert post_frame(api, frame_payload(evs, run_id=run)).status_code == 200
    with app_module.LOCK:                                         # RAM sıfırlanır (yeniden başlatma benzetimi)
        app_module.CAMS.clear()
        app_module.ALERTS.clear()
        app_module.ALERT_SEQ = 0
    assert api.get("/api/state").json()["alerts"] == []
    assert count(engine, "violation_events") == 3
    assert post_frame(api, frame_payload(evs, run_id=run)).status_code == 200      # retry: RAM'e tekrar girmez
    assert ram_alerts() == [] and count(engine, "violation_events") == 3


@pytest.mark.parametrize("payload", ["{bozuk json", "[]", '{"events": "x"}', '{"events": [1]}',
                                     '{"cam": "cam1", "events": [{"type": "start", "t": NaN}]}'])
def test_malformed_payload_is_422_not_500(api, payload):
    assert post_frame(api, payload).status_code == 422


def test_report_malformed_and_oversized(api, monkeypatch):
    assert api.post("/ingest/report", content=b"{bozuk", headers={"Content-Type": "application/json"}).status_code == 422
    assert api.post("/ingest/report", json=[1, 2]).status_code == 422
    monkeypatch.setattr(app_module, "MAX_REPORT_BYTES", 50)
    assert api.post("/ingest/report", json=report(uuid.uuid4())).status_code == 413


def test_limits_frame_size_and_event_count(api, monkeypatch):
    monkeypatch.setattr(app_module, "MAX_FRAME_BYTES", 10)
    assert post_frame(api, frame_payload([]), frame=b"x" * 11).status_code == 413
    monkeypatch.setattr(app_module, "MAX_FRAME_BYTES", 2 * 1024 * 1024)
    monkeypatch.setattr(app_module, "MAX_EVENTS_PER_REQUEST", 2)
    run = uuid.uuid4()
    _, evs = lifecycle(run)
    assert post_frame(api, frame_payload(evs, run_id=run)).status_code == 413


@pytest.mark.parametrize("over", [dict(helmet_rate=12.5), dict(pid=-1), dict(pid="7"), dict(t=2.15),
                                  dict(box=[1, 2, 3]), dict(box=[10, 0, 5, 5]), dict(missing_helmet=1),
                                  dict(missing="Yeleksiz"), dict(occurred_at="2026-10-28T14:29:00+03:00"),
                                  dict(occurred_at="2026-10-28T11:29:00"), dict(camera_id="cam 1"),
                                  dict(type="pause"), dict(event_id="değil-uuid")])
def test_event_validation_422(api, engine, over):
    ev = event("start", run_id=uuid.uuid4(), incident_id=uuid.uuid4(), **over)
    assert post_frame(api, frame_payload([ev])).status_code == 422
    assert count(engine, "violation_events") == 0


# ================================================================ G. güvenlik
def test_rtsp_credentials_never_stored_or_shown(api, engine, caplog):
    caplog.set_level(logging.DEBUG)
    run = uuid.uuid4()
    src = "rtsp://admin:S3cr3t@10.0.0.5:554/s?token=T0K3N#frag"
    payload = frame_payload([event("start", run_id=run, incident_id=uuid.uuid4(), snapshot=B64_A)], run_id=run,
                            source=src, error=f"hata {src}")
    payload["name"] = f"Kapı {src}"
    assert post_frame(api, payload).status_code == 200
    state = api.get("/api/state").text + api.get("/api/alerts").text
    for secret in ("S3cr3t", "admin", "T0K3N", "frag"):
        assert secret not in state, secret
    assert "rtsp://***@10.0.0.5:554/s?token=***" in state
    with engine.connect() as c:
        dump = json.dumps([list(map(str, r)) for t in ("cameras", "detector_runs", "violation_events")
                           for r in c.execute(text(f"SELECT * FROM {t}"))])
    assert "S3cr3t" not in dump and "T0K3N" not in dump
    assert "S3cr3t" not in caplog.text and B64_A[:40] not in caplog.text


def test_ingest_token_compare_and_unauthorized(api, engine, monkeypatch, caplog):
    monkeypatch.setattr(app_module, "INGEST_TOKEN", "dogru-token")
    run = uuid.uuid4()
    payload = frame_payload([event("start", run_id=run, incident_id=uuid.uuid4())], run_id=run)
    assert post_frame(api, payload).status_code == 401                               # token yok
    assert post_frame(api, payload, headers={"X-Token": "yanlis"}).status_code == 401
    assert api.post("/ingest/report", json=report(run), headers={"X-Token": "yanlis"}).status_code == 401
    assert count(engine, "violation_events") == 0 and ram_alerts() == []
    assert "INGEST_TOKEN" in caplog.text                                             # sessiz değil, loglandı
    assert post_frame(api, payload, headers={"X-Token": "dogru-token"}).status_code == 200
    assert count(engine, "violation_events") == 1


def test_auth_uses_constant_time_compare():
    import inspect
    assert "hmac.compare_digest" in inspect.getsource(app_module._auth)


def test_logs_have_no_secrets_on_db_failure(api, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://kullanici:PAROLA123@db-yok:5432/x")
    run = uuid.uuid4()
    post_frame(api, frame_payload([event("start", run_id=run, incident_id=uuid.uuid4(), snapshot=B64_A)], run_id=run))
    assert "PAROLA123" not in caplog.text and "kullanici" not in caplog.text and B64_A[:40] not in caplog.text
