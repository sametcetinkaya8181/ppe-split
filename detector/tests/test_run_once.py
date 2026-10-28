"""run_once uçtan uca (sahte YOLO + gerçek PPEMonitor/ppe_core): run kimliği, incident yaşam döngüsü, STALE,
rapor durumu (finished/error/interrupted), çoklu kamera izolasyonu ve kaynak maskeleme. GPU/model gerekmez."""
import threading
import uuid
from datetime import datetime, timedelta

import numpy as np
import pytest

import detector as d
import ppe_core as core

W, H, FPS = 640, 480, 10.0
SRC = "rtsp://admin:S3cr3t@10.0.0.5:554/stream1"
PERSON, HELMET, VEST = (100, 100, 200, 400), (130, 90, 170, 130), (120, 200, 180, 300)


class _T:
    def __init__(self, a):
        self.a = np.asarray(a)

    def cpu(self):
        return self

    def numpy(self):
        return self.a


class _Boxes:
    def __init__(self, rows):          # rows: (cls, box, conf, track_id)
        self.cls = _T([r[0] for r in rows])
        self.xyxy = _T(np.array([r[1] for r in rows], dtype=float).reshape(-1, 4))
        self.conf = _T([r[2] for r in rows])
        self.id = _T([r[3] for r in rows])

    def __len__(self):
        return len(self.cls.a)


class _Result:
    def __init__(self, rows):
        self.orig_img = np.zeros((H, W, 3), np.uint8)
        self.boxes = _Boxes(rows) if rows else None


class FakeModel:
    names = {0: "helmet", 2: "vest", 6: "Person"}

    def __init__(self, frames, fail_at=None, exc=None, on_frame=None):
        self.frames, self.fail_at, self.exc, self.on_frame = frames, fail_at, exc, on_frame
        self.predictor, self.track_kwargs = "eski", None

    def track(self, **kw):
        self.track_kwargs = kw
        for i, rows in enumerate(self.frames, start=1):
            if self.fail_at == i:
                raise self.exc
            if self.on_frame:
                self.on_frame(i)
            yield _Result(rows)


class FakeCap:
    def __init__(self, src, ok=True):
        self.ok = ok

    def get(self, prop):
        return {d.cv2.CAP_PROP_FPS: FPS, d.cv2.CAP_PROP_FRAME_WIDTH: W if self.ok else 0,
                d.cv2.CAP_PROP_FRAME_HEIGHT: H if self.ok else 0}.get(prop, 0)

    def release(self):
        pass


class RecSender:
    def __init__(self):
        self.frames, self.events, self.reports = [], [], []

    def submit(self, jpg, payload, events=()):
        self.frames.append(payload)
        self.events.extend(events)

    def submit_report(self, body):
        self.reports.append(body)


def person(pid=1, helmet=False, vest=True):
    rows = [(core.CLASS_PERSON, PERSON, 0.9, pid)]
    if helmet:
        rows.append((core.CLASS_HELMET, HELMET, 0.9, 900 + pid))
    if vest:
        rows.append((core.CLASS_VEST, VEST, 0.9, 950 + pid))
    return rows


# t = kare * 0.1 sn. ihlal -> ekipman geri gelir -> yeni ihlal -> kişi kaybolur (STALE, end yok)
SCENARIO = ([person()] * 60 + [person(helmet=True)] * 60 + [person()] * 60 + [[]] * 120)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(d.cv2, "VideoCapture", FakeCap)
    monkeypatch.setattr(d, "VID_STRIDE", 1)
    monkeypatch.setattr(d, "LOOP", True)


def run(frames=SCENARIO, cid="cam1", loop_no=1, sender=None, stop=None, **model_kw):
    sender = sender or RecSender()
    model = FakeModel(frames, **model_kw)
    d.run_once(model, cid, "Giriş", SRC, sender, loop_no, stop)
    return sender, model


# ---------------------------------------------------------------- run kimliği (1)
def test_each_run_once_gets_new_run_id():
    s = RecSender()
    run(sender=s, loop_no=1)
    run(sender=s, loop_no=2)                                    # aynı video tekrar oynatıldı
    ids = [r["run_id"] for r in s.reports]
    assert len(set(ids)) == 2 and all(uuid.UUID(i) for i in ids)
    assert [r["loop"] for r in s.reports] == [1, 2]
    for rid in ids:                                             # her olay kendi run'ını taşır
        assert {e["run_id"] for e in s.events if e["run_id"] == rid}
    assert {f["run_id"] for f in s.frames} == set(ids)


# ---------------------------------------------------------------- olaylar + incident (2, 4, 5, 9, 10, 11)
def test_event_identity_and_incident_lifecycle_with_real_ppemonitor():
    s, _ = run()
    rid = s.reports[0]["run_id"]
    types = [e["type"] for e in s.events]
    assert types[0] == "start" and "ongoing" in types and "end" in types
    assert types.count("start") == 2 and types.count("end") == 1   # 2. ihlal STALE ile kayboldu
    assert len({e["event_id"] for e in s.events}) == len(s.events)

    first_end = types.index("end")
    inc1 = {e["incident_id"] for e in s.events[:first_end + 1]}
    assert len(inc1) == 1                                       # start/ongoing/end aynı incident
    inc2 = {e["incident_id"] for e in s.events[first_end + 1:]}
    assert len(inc2) == 1 and inc1.isdisjoint(inc2)             # yeni ihlal yeni incident

    for e in s.events:
        assert e["run_id"] == rid and e["camera_id"] == "cam1" and e["pid"] == 1 and "orphan" not in e
        assert set(e) >= {"type", "pid", "t", "missing", "helmet_rate", "vest_rate", "box"}   # eski alanlar
        dt = datetime.fromisoformat(e["occurred_at"].replace("Z", "+00:00"))
        assert dt.utcoffset() == timedelta(0)
        if e["type"] == "end":
            assert (e["missing_helmet"], e["missing_vest"], e["missing"]) == (False, False, "")
        else:
            assert (e["missing_helmet"], e["missing_vest"], e["missing"]) == (True, False, "Baretsiz")
        assert ("snapshot_b64" in e) == (e["type"] == "start")

    rep = s.reports[0]
    (unclosed,) = rep["unclosed_incidents"]                     # STALE: sahte end üretilmedi, raporda görünür
    assert unclosed["incident_id"] == inc2.pop() and unclosed["reason"] == "stale" and unclosed["pid"] == 1
    assert rep["orphan_events"] == 0


def test_violation_open_at_video_end_reported_as_run_end():
    s, _ = run([person()] * 40)
    (u,) = s.reports[0]["unclosed_incidents"]
    assert u["reason"] == "run_end" and u["incident_id"] == s.events[0]["incident_id"]
    assert [e["type"] for e in s.events] == ["start"]


# ---------------------------------------------------------------- izolasyon (6, 7)
def test_same_pid_in_different_runs_is_isolated():
    s = RecSender()
    run(sender=s)
    run(sender=s)
    by_run = {}
    for e in s.events:
        by_run.setdefault(e["run_id"], set()).add(e["incident_id"])
    (a, b) = by_run.values()
    assert a.isdisjoint(b) and {e["pid"] for e in s.events} == {1}


def test_cameras_are_isolated():
    s1, _ = run(cid="cam1")
    s2, _ = run(cid="cam2")
    assert {e["camera_id"] for e in s1.events} == {"cam1"} and {e["camera_id"] for e in s2.events} == {"cam2"}
    assert s1.reports[0]["camera_id"] == "cam1" and s2.reports[0]["camera_id"] == "cam2"
    assert {e["incident_id"] for e in s1.events}.isdisjoint(e["incident_id"] for e in s2.events)
    assert s1.reports[0]["run_id"] != s2.reports[0]["run_id"]


# ---------------------------------------------------------------- rapor ve durum (6. görev, 17)
def test_finished_report_keeps_legacy_fields(monkeypatch):
    s, _ = run()
    rep = s.reports[0]
    assert rep["run_status"] == "finished" and rep["status"] == "running"     # LOOP=1: eski davranış
    assert rep["cam"] == "cam1" and rep["name"] == "Giriş" and rep["error"] is None
    (row,) = rep["report"]
    assert set(row) == {"pid", "first", "last", "n", "helmet", "vest", "violation"} and row["violation"] is True
    assert rep["started_at"] <= rep["finished_at"] and rep["source_kind"] == "rtsp"
    monkeypatch.setattr(d, "LOOP", False)
    s2, _ = run()
    assert s2.reports[0]["status"] == "finished" and s2.reports[0]["run_status"] == "finished"


def test_error_mid_run_still_reports_with_redacted_error():
    s = RecSender()
    with pytest.raises(RuntimeError):
        run(sender=s, fail_at=50, exc=RuntimeError(f"akış koptu: {SRC}"))
    (rep,) = s.reports
    assert rep["run_status"] == "error" and rep["report"] and rep["finished_at"]
    assert "S3cr3t" not in rep["error"] and "admin" not in rep["error"] and "***@10.0.0.5" in rep["error"]
    assert [u["reason"] for u in rep["unclosed_incidents"]] == ["run_end"]


def test_keyboard_interrupt_reports_interrupted():
    s = RecSender()
    with pytest.raises(KeyboardInterrupt):
        run(sender=s, fail_at=30, exc=KeyboardInterrupt())
    assert s.reports[0]["run_status"] == "interrupted" and s.reports[0]["status"] == "interrupted"


def test_stop_event_ends_run_as_interrupted():
    stop = threading.Event()
    s, _ = run(stop=stop, on_frame=lambda i: stop.set() if i == 30 else None)
    assert s.reports[0]["run_status"] == "interrupted" and len(s.frames) < 30


def test_source_open_failure_sends_no_report(monkeypatch):
    monkeypatch.setattr(d.cv2, "VideoCapture", lambda src: FakeCap(src, ok=False))
    s = RecSender()
    with pytest.raises(RuntimeError) as ei:
        run(sender=s)
    assert "S3cr3t" not in str(ei.value) and "***@" in str(ei.value)
    assert s.reports == [] and s.events == []


# ---------------------------------------------------------------- maskeleme + mevcut payload (18, 19)
def test_frame_payload_redacted_and_backward_compatible():
    s, model = run([person()] * 5)
    f = s.frames[0]
    assert set(f) == {"meta", "summary", "people", "run_id", "camera_id"}
    assert f["meta"]["source"] == "rtsp://***@10.0.0.5:554/stream1" and f["meta"]["source_kind"] == "rtsp"
    assert set(f["meta"]) >= {"source", "fps", "width", "height", "status", "error", "loop", "proc_fps"}
    assert set(f["summary"]) == {"t", "people", "ok", "viol", "analyzing", "raw_helmets", "raw_vests"}
    assert "S3cr3t" not in repr(s.frames) + repr(s.reports)
    assert model.predictor is None and model.track_kwargs["tracker"] == d.TRACKER   # YOLO çağrısı aynı
    assert model.track_kwargs["source"] == SRC                                      # gerçek kaynak açılır


def test_ppe_core_untouched_contract():
    """Karar eşikleri bu fazda değişmedi (varsayılanlar)."""
    assert (core.CLASS_HELMET, core.CLASS_VEST, core.CLASS_PERSON) == (0, 2, 6)
    assert (core.ENTER_RATE, core.EXIT_RATE, core.WINDOW_SEC, core.STALE_SEC) == (0.20, 0.40, 4.0, 10.0)
