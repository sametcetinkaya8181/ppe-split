"""FAZ 9 dedektör gözlemi: güvenilirlik kapısı nedenlerinin PPEMonitor ile eşdeğerliği, kapsam sayımı (ANALIZ uygun
sayılmaz; track != kişi), sinyal yok / donmuş kare, gecikme, yüzdelikler, kamera izolasyonu, yeniden bağlanma
sayaçları, run_once entegrasyonu (kararlar değişmez, gizli bilgi sızmaz) ve Ultralytics çevrimdışı modu.
GPU/model gerekmez."""
import json
import os
import random
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

import detector as d
import ppe_core as core
import telemetry as tm
from tests.test_run_once import SCENARIO, SRC, FakeCap, FakeModel, RecSender, person

HERE = Path(__file__).resolve().parent.parent
WH = (640, 480)


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def tel(cid="cam1", clock=None, **kw):
    c = clock or Clock()
    t = tm.CameraTelemetry(cid, clock=c, wall=lambda: 1.7e9, sample_resources=False, **kw)
    t.run_started("r1", loop=1, source_kind="file", fps=10.0, width=WH[0], height=WH[1], vid_stride=1)
    return t, c


def frame(value=None):
    if value is None:
        return np.random.default_rng(0).integers(0, 255, (WH[1], WH[0], 3), dtype=np.uint8)
    return np.full((WH[1], WH[0], 3), value, np.uint8)


# ---------------------------------------------------------------- kapı eşdeğerliği
def test_gate_reasons_match_ppemonitor_reliability_exactly():
    rng = random.Random(7)
    for _ in range(400):
        x1, y1 = rng.uniform(-5, 640), rng.uniform(-5, 480)
        box = (x1, y1, x1 + rng.uniform(1, 200), y1 + rng.uniform(1, 200))
        conf = rng.choice([0.39, 0.4, 0.41, rng.random()])
        m = core.PPEMonitor()
        m.update(1.0, WH, [(1, box, conf)], {}, {})
        reliable = m.stats[1]["n"] == 1                       # PPEMonitor yalnız güvenilir kareyi gözleme ekler
        assert (tm.gate_reasons(box, conf, WH) == ()) == reliable, (box, conf)


def test_gate_reason_names():
    assert tm.gate_reasons((100, 100, 200, 400), 0.9, WH) == ()
    assert tm.gate_reasons((100, 100, 200, 140), 0.9, WH) == ("too_small",)
    assert tm.gate_reasons((2, 100, 200, 400), 0.3, WH) == ("low_conf", "edge")


def test_percentiles_match_numpy_and_empty_is_none():
    xs = [random.Random(1).random() * 100 for _ in range(101)]
    p = tm.percentiles(xs)
    for q in (50, 95, 99):
        assert p[f"p{q}"] == pytest.approx(float(np.percentile(xs, q)), abs=1e-3)
    assert tm.percentiles([])["p95"] is None and tm.percentiles([])["n"] == 0


# ---------------------------------------------------------------- kapsam
def _feed(t_obj, clock, persons_by_frame, fps=10.0):
    mon = core.PPEMonitor()
    for i, persons in enumerate(persons_by_frame, start=1):
        t = i / fps
        clock.t += 1 / fps
        statuses, events = mon.update(t, WH, persons, {pid: 1 for pid, _, _ in persons}, {})
        t_obj.frame(t=t, frame=frame(), persons=persons, statuses=statuses, stage_ms={}, events=events)
    return mon


def test_coverage_counts_unevaluated_tracks_by_reason_and_never_counts_analiz_as_ok():
    t, c = tel()
    near = (1, (100, 100, 200, 400), 0.9)
    far = (2, (300, 100, 330, 140), 0.9)                       # 40 px: uzak/küçük -> hiç değerlendirilmez
    _feed(t, c, [[near, far]] * 40 + [[]] * 120)               # sonra ikisi de kaybolur (STALE)
    cov = t.snapshot(resources=False)["coverage"]
    assert cov["track_frames"] == 80 and cov["unevaluated_by_reason"]["too_small"] == 40
    assert cov["track_frames_unevaluated"] == 40
    assert cov["tracks_closed"] == 2 and cov["tracks_closed_never_evaluated"] == 1
    assert cov["never_evaluated_reason"]["too_small"] == 1
    assert cov["never_evaluated_duration_sec"]["max"] == pytest.approx(3.9)
    # ilk MIN_SPAN boyunca yakın kişi de ANALIZ: 'warmup' (uygun sayılmaz)
    assert cov["track_frames_analyzing"] > 40


def test_current_coverage_and_stale_decision_flag():
    t, c = tel()
    near = (1, (100, 100, 200, 400), 0.9)
    edge = (1, (0, 100, 100, 400), 0.9)
    far = (2, (300, 100, 330, 140), 0.9)
    _feed(t, c, [[near]] * 30 + [[edge, far]])
    cur = t.snapshot(resources=False)["coverage"]["current"]
    assert cur == {"visible": 2, "decided": 1, "analyzing": 1, "analyzing_by_reason": {"too_small": 1},
                   "stale_decision": 1}                         # kenardaki kişinin kararı eski karelerden
    assert "unevaluated_persons" in t.snapshot(resources=False)["flags"]


def test_track_count_is_not_person_count_note_present():
    t, _ = tel()
    assert "benzersiz kişi" in t.snapshot(resources=False)["coverage"]["note"]


# ---------------------------------------------------------------- sinyal / donma / gecikme
def test_black_frame_flags_no_signal_and_frozen_after_threshold(monkeypatch):
    monkeypatch.setattr(tm, "FREEZE_WARN_SEC", 5.0)
    t, c = tel()
    for i in range(1, 80):
        c.t += 0.1
        t.frame(t=i * 0.1, frame=frame(0), persons=[], statuses={}, stage_ms={})
    s = t.snapshot(resources=False)
    assert "no_signal" in s["flags"] and "frozen" in s["flags"] and s["counters"]["no_signal_frames"] == 79
    c.t += 0.1
    t.frame(t=8.0, frame=frame(), persons=[], statuses={}, stage_ms={})
    s = t.snapshot(resources=False)
    assert "no_signal" not in s["flags"] and "frozen" not in s["flags"]


def test_startup_measured_separately_and_lag_is_backlog_after_first_frame():
    t, c = tel()
    c.t += 4.0                                                # kaynak açma + model hazırlığı: 4 sn
    t.frame(t=0.1, frame=frame(), persons=[], statuses={}, stage_ms={})
    s = t.snapshot(resources=False)
    assert s["run"]["startup_sec"] == pytest.approx(4.0) and s["startup_sec"]["max"] == pytest.approx(4.0)
    assert s["lag_sec"]["current"] == pytest.approx(0.0) and "lagging" not in s["flags"]
    c.t += 5.0                                                # 5 sn geçti, kaynakta yalnız 2 sn işlendi
    t.frame(t=2.1, frame=frame(), persons=[], statuses={}, stage_ms={})
    s = t.snapshot(resources=False)
    assert s["lag_sec"]["current"] == pytest.approx(3.0) and "lagging" in s["flags"]


def test_stage_timings_percentiles():
    t, _ = tel()
    for v in range(1, 101):
        t.timings_add({"inference_ms": float(v), "frame_ms": None, "bogus": 1.0})
    s = t.snapshot(resources=False)["timings_ms"]
    assert s["inference_ms"]["p50"] == pytest.approx(50.5) and s["inference_ms"]["n"] == 100
    assert s["frame_ms"]["n"] == 0 and "bogus" not in s


# ---------------------------------------------------------------- yaşam döngüsü / izolasyon
def test_lifecycle_states_and_reconnect_counters():
    t, _ = tel()
    t.run_ended("error", "ConnectionError")
    assert t.state == "disconnected" and "camera_disconnected" in t.snapshot(resources=False)["flags"]
    t.reconnecting()
    t.open_failed("source_open_failed")
    t.reconnecting()
    s = t.snapshot(resources=False)
    assert s["counters"]["reconnect_attempts"] == 2 and s["counters"]["source_open_failures"] == 1
    assert s["counters"]["runs_failed"] == 1


def test_cameras_isolated_and_registry_is_per_camera():
    a, ca = tel("camA")
    b, _ = tel("camB")
    _feed(a, ca, [[(1, (100, 100, 200, 400), 0.9)]] * 5)
    assert b.snapshot(resources=False)["counters"]["frames_processed"] == 0
    assert tm.for_camera("x1") is tm.for_camera("x1") and tm.for_camera("x1") is not tm.for_camera("x2")


def test_health_file_written_atomically_without_secrets(tmp_path):
    t, c = tel("cam9")
    t.set_output(tmp_path / "cam9.health.json")
    t.flush(force=True)
    data = json.loads((tmp_path / "cam9.health.json").read_text(encoding="utf-8"))
    assert data["camera_id"] == "cam9" and data["state"] == "connected"
    assert not list(tmp_path.glob("*.tmp"))


# ---------------------------------------------------------------- run_once entegrasyonu
@pytest.fixture
def _prod(monkeypatch):
    monkeypatch.setattr(d.cv2, "VideoCapture", FakeCap)
    monkeypatch.setattr(d, "VID_STRIDE", 1)
    monkeypatch.setattr(d, "LOOP", True)
    tm._REGISTRY.clear()
    yield
    tm._REGISTRY.clear()


def test_run_once_records_telemetry_without_changing_events(_prod):
    s1 = RecSender()
    d.run_once(FakeModel(SCENARIO), "camT", "Giriş", SRC, s1, 1)
    snap = tm.for_camera("camT").snapshot(resources=False)
    assert snap["counters"]["frames_processed"] == len(SCENARIO)
    assert snap["counters"]["events_start"] == sum(e["type"] == "start" for e in s1.events) == 2
    assert snap["state"] == "ended" and snap["run"]["source_kind"] == "rtsp"
    assert snap["timings_ms"]["frame_ms"]["n"] == len(SCENARIO) and snap["timings_ms"]["monitor_ms"]["n"] > 0
    text = json.dumps(snap) + json.dumps(tm.for_camera("camT").compact())
    assert "S3cr3t" not in text and "10.0.0.5" not in text and "admin" not in text
    # aynı senaryo telemetry olmadan (FAZ 8 karar kopyası) aynı olayları üretir: tests/test_evaluation_pipeline.py


def test_source_open_failure_and_reconnect_counted_in_stream_worker(_prod, monkeypatch):
    calls = {"n": 0}

    class Stop:
        def __init__(self):
            self.flag = False

        def is_set(self):
            return self.flag

        def wait(self, _t):
            return self.flag

    stop = Stop()

    class Cap(FakeCap):
        def __init__(self, src):
            calls["n"] += 1
            if calls["n"] >= 3:
                stop.flag = True
            super().__init__(src, ok=calls["n"] >= 3)

    class Sender(RecSender):
        def start(self):
            pass

        def close(self, timeout):
            return {}

        def snapshot_stats(self):
            return {"events_sent": 0, "reports_sent": 0, "pending_count": 0, "dead_letter_count": 0,
                    "quarantine_count": 0, "evicted_ongoing": 0, "dropped_ongoing_full": 0}

    monkeypatch.setattr(d.cv2, "VideoCapture", Cap)
    monkeypatch.setattr(d, "_make_sender", lambda cid, name: Sender())
    monkeypatch.setattr(d, "YOLO", lambda path: FakeModel([person()] * 3))
    monkeypatch.setattr(d.outbox_mod, "default_dir", lambda: Path(os.environ.get("TMP", ".")))
    monkeypatch.setattr(d.telemetry.CameraTelemetry, "set_output", lambda self, p: None)
    d.stream_worker("camR", "Kule", SRC, 0, stop)
    s = tm.for_camera("camR").snapshot(resources=False)
    assert s["counters"]["source_open_failures"] == 2 and s["counters"]["reconnect_attempts"] == 2
    assert s["counters"]["runs_started"] == 1 and s["camera_id"] == "camR"


# ---------------------------------------------------------------- Ultralytics çevrimdışı
def test_detector_import_disables_ultralytics_analytics_and_online_mode():
    env = {k: v for k, v in os.environ.items() if k not in ("YOLO_OFFLINE", "PYTEST_CURRENT_TEST")}
    env["DETECTOR_ENV_FILE"] = str(HERE / "tests" / "_no_such.env")
    code = ("import detector; from ultralytics.utils import ONLINE; from ultralytics.utils.events import events; "
            "import os; print(os.environ['YOLO_OFFLINE'], ONLINE, events.enabled)")
    out = subprocess.run([sys.executable, "-c", code], cwd=HERE, env=env, capture_output=True, text=True,
                         timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip().splitlines()[-1] == "1 False False"


def test_frame_payload_carries_compact_health_once_per_second(_prod):
    import time as _t
    s = RecSender()
    model = FakeModel([person()] * 30, on_frame=lambda i: _t.sleep(1.05) if i == 10 else None)
    d.run_once(model, "camH", "Giriş", SRC, s, 1)
    with_health = [f["meta"]["health"] for f in s.frames if "health" in f["meta"]]
    assert with_health, "sağlık özeti kare meta'sına eklenmedi"
    h = with_health[-1]
    assert set(h) == {"state", "uptime_sec", "proc_fps", "source_fps", "vid_stride", "lag_sec", "last_frame_age_sec",
                      "frame_ms_p95", "inference_ms_p95", "flags", "coverage", "reconnects"}
    assert h["coverage"]["visible"] == 1 and h["state"] == "connected"
    assert "S3cr3t" not in json.dumps(h) and "rtsp" not in json.dumps(h)


def test_stalled_flag_and_flusher_writes_without_frames(tmp_path, monkeypatch):
    """Donmuş akış: kaynak okuması bloke -> kare yok. Sağlık dosyası yine güncellenir ve 'stalled' görünür."""
    monkeypatch.setattr(tm, "HEALTH_EVERY", 0.05)
    t, c = tel("camS")
    t.frame(t=0.1, frame=frame(), persons=[], statuses={}, stage_ms={})
    c.t += 6.0                                                # 6 sn hiç kare yok (STALL_WARN_SEC=5)
    s = t.snapshot(resources=False)
    assert s["last_frame_age_sec"] == pytest.approx(6.0) and "stalled" in s["flags"]
    t.set_output(tmp_path / "camS.health.json")
    t.start_flusher()
    import time as _t
    deadline = _t.monotonic() + 5
    while not (tmp_path / "camS.health.json").exists() and _t.monotonic() < deadline:
        _t.sleep(0.02)
    data = json.loads((tmp_path / "camS.health.json").read_text(encoding="utf-8"))
    assert "stalled" in data["flags"]
