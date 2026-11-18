"""Sender: retry, HTTP sınıflandırma, FIFO/bariyer, kuyruk sınırı, Session sahipliği, kapanış (sahte HTTP)."""
import threading
import time

import pytest
import requests

import protocol as p
from tests.conftest import FakeSession, wait_until

JPG = b"\xff\xd8JPEG\xff\xd9"


def make(session, start=True, **kw):
    logs = []
    opts = dict(session=session, timeout=1.0, backoff_base=0.01, backoff_max=0.05, log=logs.append)
    opts.update(kw)
    s = p.Sender("cam1", "Giriş", "http://dashboard.test", **opts)
    s.logs = logs
    if start:
        s.start()
    return s


def ev(kind="start", run="run-A", cam="cam1", pid=1, **extra):
    missing = "" if kind == "end" else "Baretsiz"
    e = {"type": kind, "pid": pid, "t": 1.0, "missing": missing, "helmet_rate": 10, "vest_rate": 90,
         "box": [1, 2, 3, 4], "event_id": p.new_id(), "incident_id": p.new_id(), "run_id": run, "camera_id": cam,
         "occurred_at": p.utc_now_iso(), "missing_helmet": kind != "end", "missing_vest": False}
    e.update(extra)
    return e


def frame_payload(run="run-A"):
    return {"meta": {"status": "running"}, "summary": {"t": 1.0}, "people": [], "run_id": run, "camera_id": "cam1"}


def sent_event_ids(session):
    return [e["event_id"] for c in session.frames() for e in c["payload"]["events"]]


# ---------------------------------------------------------------- temel teslim + geriye uyumlu payload
def test_frame_with_events_delivered_once():
    sess = FakeSession()
    s = make(sess)
    e1, e2 = ev(), ev("ongoing")
    s.submit(JPG, frame_payload(), [e1, e2])
    assert wait_until(lambda: s.snapshot_stats()["events_sent"] == 2)
    c = sess.frames()[0]
    assert c["url"] == "http://dashboard.test/ingest/frame" and c["jpg"] == JPG
    assert c["payload"]["cam"] == "cam1" and c["payload"]["name"] == "Giriş"
    assert set(c["payload"]) >= {"meta", "summary", "people", "events", "run_id", "camera_id"}
    assert sent_event_ids(sess) == [e1["event_id"], e2["event_id"]]
    s.close(1)


# ---------------------------------------------------------------- retry: aynı istek (3, 13)
def test_timeout_then_retry_sends_identical_payload():
    sess = FakeSession([requests.Timeout("t"), requests.ConnectionError("c"), 503, 200])
    s = make(sess)
    e = ev()
    s.submit(JPG, frame_payload(), [e])
    s.submit(b"NEWER", frame_payload(), [])                # retry sırasında gelen yeni kare isteği değiştirmez
    assert wait_until(lambda: s.snapshot_stats()["events_sent"] == 1)
    tries = [c for c in sess.frames() if c["payload"]["events"]]
    assert len(tries) == 4
    assert len({c["raw"] for c in tries}) == 1 and len({c["jpg"] for c in tries}) == 1
    assert tries[0]["payload"]["events"][0]["event_id"] == e["event_id"]
    assert tries[0]["payload"]["events"][0]["occurred_at"] == e["occurred_at"]
    assert s.snapshot_stats()["retries"] == 3
    s.close(1)


def test_event_stays_queued_until_2xx():
    gate = threading.Event()
    sess = FakeSession([503, 503, lambda c: (gate.wait(5), 200)[1]])
    s = make(sess)
    s.submit(JPG, frame_payload(), [ev()])
    assert wait_until(lambda: len(sess.calls) >= 3)
    assert s.snapshot_stats()["queued_events"] == 1            # yanıt gelmeden kuyruktan çıkmadı
    gate.set()
    assert wait_until(lambda: s.snapshot_stats()["queued_events"] == 0)
    s.close(1)


def test_exponential_backoff():
    sess = FakeSession([503, 503, 503, 503, 200])
    s = make(sess, backoff_base=0.05, backoff_max=1.0)
    s.submit(JPG, frame_payload(), [ev()])
    assert wait_until(lambda: s.snapshot_stats()["events_sent"] == 1)
    t = [c["t"] for c in sess.frames()]
    gaps = [b - a for a, b in zip(t, t[1:])]
    assert gaps[0] >= 0.02 and gaps[3] >= 0.2 and gaps[3] > gaps[0]
    s.close(1)


def test_retry_after_is_honoured():
    sess = FakeSession([(429, {"Retry-After": "1"}), 200])
    s = make(sess, retry_after_max=2.0)
    s.submit(JPG, frame_payload(), [ev()])
    assert wait_until(lambda: s.snapshot_stats()["events_sent"] == 1)
    t = [c["t"] for c in sess.frames()]
    assert t[1] - t[0] >= 0.95
    s.close(1)


@pytest.mark.parametrize("status", [408, 500, 502, 503, 504])
def test_transient_status_retried(status):
    sess = FakeSession([status, 200])
    s = make(sess)
    s.submit(JPG, frame_payload(), [ev()])
    assert wait_until(lambda: s.snapshot_stats()["events_sent"] == 1)
    assert s.snapshot_stats().get("dropped_permanent", 0) == 0
    s.close(1)


# ---------------------------------------------------------------- kalıcı hatalar (12)
@pytest.mark.parametrize("status", [400, 404, 413, 415, 422])
def test_permanent_error_single_event_dropped_no_infinite_retry(status):
    sess = FakeSession(default=status)
    s = make(sess)
    s.submit(JPG, frame_payload(), [ev()])
    assert wait_until(lambda: s.snapshot_stats().get("dropped_permanent") == 1)
    time.sleep(0.2)
    assert len(sess.frames()) == 1 and s.snapshot_stats()["queued_events"] == 0
    assert any(f"HTTP {status}" in m for m in s.logs)
    s.close(1)


def test_permanent_error_bisects_batch_and_keeps_good_events():
    def judge(call):
        return 422 if any(e["pid"] == 99 for e in call["payload"]["events"]) else 200
    sess = FakeSession(default=judge)
    s = make(sess)
    good = [ev(pid=i) for i in range(1, 8)]
    bad = ev(pid=99)
    s.submit(JPG, frame_payload(), good[:3] + [bad] + good[3:])
    assert wait_until(lambda: s.snapshot_stats()["queued_events"] == 0)
    delivered = [e["event_id"] for c in sess.frames() if c["payload"]["events"] and judge(c) == 200
                 for e in c["payload"]["events"]]
    assert delivered == [e["event_id"] for e in good]           # sıra da korunur
    assert s.snapshot_stats()["dropped_permanent"] == 1 and len(sess.frames()) < 12
    s.close(1)


@pytest.mark.parametrize("status", [401, 403])
def test_auth_error_drops_batch_with_hint(status):
    sess = FakeSession(default=status)
    s = make(sess)
    s.submit(JPG, frame_payload(), [ev(), ev(), ev()])
    assert wait_until(lambda: s.snapshot_stats().get("dropped_permanent") == 3)
    assert len(sess.frames()) == 1 and any("INGEST_TOKEN" in m for m in s.logs)
    s.close(1)


def test_permanent_report_error_drops_report_and_continues():
    sess = FakeSession([422, 200])
    s = make(sess)
    s.submit_report({"cam": "cam1", "run_id": "A", "report": []})
    s.submit_report({"cam": "cam1", "run_id": "B", "report": []})
    assert wait_until(lambda: s.snapshot_stats().get("reports_sent") == 1)
    assert s.snapshot_stats()["reports_dropped"] == 1
    assert [c["body"]["run_id"] for c in sess.reports()] == ["A", "B"]
    s.close(1)


# ---------------------------------------------------------------- kuyruk sınırı (14)
def test_queue_full_evicts_oldest_ongoing_then_rejects_new_and_logs():
    s = make(FakeSession(), start=False, queue_max=3)
    a, o1, o2 = ev("start", pid=1), ev("ongoing", pid=1), ev("ongoing", pid=1)
    s.submit(JPG, frame_payload(), [a, o1, o2])
    e = ev("end", pid=1)
    s.submit(None, None, [e])                                    # dolu: en eski ongoing (o1) atılır
    ids = lambda: [__import__("json").loads(q[1])["event_id"] for q in s.queue if q[0] == "event"]  # noqa: E731
    assert ids() == [a["event_id"], o2["event_id"], e["event_id"]]
    s.submit(None, None, [ev("ongoing", pid=2)])                 # o2 atılır, yeni ongoing girer
    s.submit(None, None, [ev("ongoing", pid=3)])                 # yeni gelen ongoing'e yer açmak için önceki ongoing atılır
    assert s.stats["evicted_ongoing"] == 3
    s.queue = type(s.queue)(q for q in s.queue if q[2] != "ongoing")
    s.n_events = len(s.queue)
    s.submit(None, None, [ev("start", pid=4)])                   # şimdi [start, end, start]: dolu, ongoing yok
    rejected = ev("start", pid=5)
    s.submit(None, None, [rejected])
    assert rejected["event_id"] not in ids() and s.stats["dropped_queue_full"] == 1
    assert any("REDDEDİLDİ" in m for m in s.logs) and any("atıldı" in m for m in s.logs)
    s.submit_report({"cam": "cam1", "report": []})              # rapor sınıra tabi değil
    assert s.queue[-1][0] == "report"


def test_queue_eviction_never_touches_inflight_items():
    s = make(FakeSession(), start=False, queue_max=2)
    o1, o2 = ev("ongoing"), ev("ongoing")
    s.submit(JPG, frame_payload(), [o1, o2])
    s.inflight = 2                                               # ikisi de gönderimde
    s.submit(None, None, [ev("ongoing")])
    assert s.n_events == 2 and s.stats["dropped_queue_full"] == 1 and s.stats.get("evicted_ongoing", 0) == 0


# ---------------------------------------------------------------- FIFO / rapor bariyeri (15)
def test_report_waits_for_earlier_events_and_fifo_order():
    sess = FakeSession([503, 503, 200])
    s = make(sess)
    e1 = ev(run="R1")
    s.submit(JPG, frame_payload("R1"), [e1])
    s.submit_report({"cam": "cam1", "run_id": "R1", "report": [{"pid": 1}]})
    e2 = ev(run="R2")
    s.submit(None, None, [e2])
    assert wait_until(lambda: s.snapshot_stats()["queued_items"] == 0)
    order = [("report", c["body"]["run_id"]) if c["kind"] == "report" else
             ("events", tuple(e["run_id"] for e in c["payload"]["events"])) for c in sess.calls]
    first_ok_event = 2                                          # ilk iki deneme 503
    report_idx = order.index(("report", "R1"))
    assert report_idx > first_ok_event
    assert all(o[0] == "events" and o[1] == ("R1",) for o in order[:report_idx])
    assert order[report_idx + 1] == ("events", ("R2",))
    s.close(1)


def test_mixed_runs_in_one_batch_keep_own_ids():
    s = make(FakeSession(), start=False)
    old, new = ev(run="RUN-OLD", cam="cam1"), ev(run="RUN-NEW", cam="cam1")
    s.submit(JPG, frame_payload("RUN-OLD"), [old])
    s.submit(JPG, frame_payload("RUN-NEW"), [new])               # yeni run'ın karesi en yenisi
    s.start()
    assert wait_until(lambda: s.snapshot_stats()["events_sent"] == 2)
    c = s.sess.frames()[0]
    assert c["payload"]["run_id"] == "RUN-NEW"
    assert [(e["run_id"], e["event_id"]) for e in c["payload"]["events"]] == [("RUN-OLD", old["event_id"]),
                                                                          ("RUN-NEW", new["event_id"])]
    s.close(1)


# ---------------------------------------------------------------- canlı görüntü: en yeni kare
def test_latest_frame_wins_but_events_never_dropped():
    gate = threading.Event()
    sess = FakeSession([lambda c: (gate.wait(5), 200)[1]])
    s = make(sess)
    s.submit(b"F1", frame_payload(), [])
    assert wait_until(lambda: len(sess.calls) == 1)
    evs = [ev(pid=i) for i in range(2, 6)]
    for i, e in enumerate(evs, start=2):
        s.submit(f"F{i}".encode(), frame_payload(), [e])
    gate.set()
    assert wait_until(lambda: s.snapshot_stats()["events_sent"] == 4)
    jpgs = [c["jpg"] for c in sess.frames()]
    assert jpgs == [b"F1", b"F5"]                                # F2-F4 atlandı
    assert sent_event_ids(sess) == [e["event_id"] for e in evs]  # ama olayların hepsi gitti
    s.close(1)


# ---------------------------------------------------------------- dondurulmuş olay
def test_event_mutation_after_submit_has_no_effect():
    sess = FakeSession()
    s = make(sess, start=False)
    e = ev()
    original = dict(e)
    s.submit(JPG, frame_payload(), [e])
    e["event_id"], e["pid"], e["hacked"] = "değişti", 999, True
    s.start()
    assert wait_until(lambda: s.snapshot_stats()["events_sent"] == 1)
    assert sess.frames()[0]["payload"]["events"][0] == original
    s.close(1)


# ---------------------------------------------------------------- Session sahipliği (16)
def test_session_used_only_from_sender_thread():
    sess = FakeSession([503, 200])
    s = make(sess)
    s.submit(JPG, frame_payload(), [ev()])
    s.submit_report({"cam": "cam1", "report": []})              # rapor ana thread'den yalnızca kuyruğa girer
    assert wait_until(lambda: s.snapshot_stats().get("reports_sent") == 1)
    threads = {c["thread"] for c in sess.calls}
    assert threads == {s.ident} and threading.get_ident() not in threads
    assert not hasattr(s, "post_report")
    s.close(1)


def test_sender_survives_unexpected_exception():
    sess = FakeSession([ValueError("beklenmedik"), 200])
    s = make(sess)
    s.submit(JPG, frame_payload(), [ev()])
    assert wait_until(lambda: s.snapshot_stats()["events_sent"] == 1) and s.is_alive()
    s.close(1)


# ---------------------------------------------------------------- kapanış (17)
def test_graceful_close_drains_events_and_report():
    sess = FakeSession([503, 200])
    s = make(sess)
    s.submit(JPG, frame_payload(), [ev(), ev("ongoing")])
    s.submit_report({"cam": "cam1", "run_id": "A", "run_status": "interrupted", "report": []})
    left = s.close(3)
    assert left == {"events": 0, "reports": 0} and not s.is_alive()
    assert s.snapshot_stats()["events_sent"] == 2 and sess.reports()[0]["body"]["run_status"] == "interrupted"


def test_close_is_bounded_when_dashboard_down_and_reports_loss():
    sess = FakeSession(default=requests.ConnectionError("kapalı"))
    s = make(sess)
    s.submit(JPG, frame_payload(), [ev(), ev()])
    s.submit_report({"cam": "cam1", "report": []})
    t0 = time.monotonic()
    left = s.close(0.3)
    assert time.monotonic() - t0 < 3.0 and not s.is_alive()
    assert left == {"events": 2, "reports": 1}
    assert any("kayboldu" in m for m in s.logs)


# ---------------------------------------------------------------- log güvenliği (18)
def test_logs_never_contain_credentials_or_snapshot():
    sess = FakeSession([requests.ConnectionError("rtsp://admin:S3cr3t@x"), 422])
    s = p.Sender("cam1", "Giriş", "http://admin:S3cr3t@dashboard.test", session=sess, timeout=1,
                 backoff_base=0.01, backoff_max=0.02, log=(logs := []).append)
    s.start()
    s.submit(JPG, frame_payload(), [ev(snapshot_b64="R0lGODlhGİZLİSNAPSHOT")])
    assert wait_until(lambda: s.snapshot_stats().get("dropped_permanent") == 1)
    s.close(1)
    text = "\n".join(logs)
    assert logs and "S3cr3t" not in text and "admin" not in text and "GİZLİSNAPSHOT" not in text
