"""FAZ 10-A kaynak yaşam döngüsü: dosya sonu / kullanıcı durdurması / canlı akış kopması (donma sonrası dahil) /
hata ayrımı; sonu bilinen ağ videosu; üstel geri çekilme (sınırlı, sapmalı, sağlıklı run sonrası sıfırlanır);
kare vermeyen kaynakta hızlı döngü yok; bağlantı yokken operatöre yer tutucu kare; LOOP=0'da canlı kaynak yine
yeniden bağlanır; aynı kamera kimliği. GPU/model/ağ gerekmez."""
import json
import random
from pathlib import Path

import numpy as np
import pytest

import detector as d
import telemetry as tm
from tests.test_run_once import SCENARIO, SRC, FakeCap, FakeModel, RecSender, _Result, person


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(d.cv2, "VideoCapture", FakeCap)
    monkeypatch.setattr(d, "VID_STRIDE", 1)
    monkeypatch.setattr(d, "LOOP", True)
    tm._REGISTRY.clear()
    yield
    tm._REGISTRY.clear()


# ---------------------------------------------------------------- sınıflandırma
@pytest.mark.parametrize("stopped,is_file,n,read,dist,expected", [
    (True, False, 0, 10, False, ("interrupted", "user_stop")),
    (True, True, 100, 10, False, ("interrupted", "user_stop")),
    (False, True, 0, 10, False, ("finished", "eof")),
    (False, False, 100, 99, False, ("finished", "eof")),            # sonu bilinen ağ videosu sona erdi
    (False, False, 100, 50, False, ("disconnected", "source_lost")),  # sonu bilinen ama yarıda kesildi
    (False, False, 0, 500, False, ("disconnected", "source_lost")),   # canlı akış (kare sayısı yok)
    (False, False, -1, 500, True, ("disconnected", "source_lost_after_stall")),
])
def test_classify_end(stopped, is_file, n, read, dist, expected):
    assert d.classify_end(stopped, is_file, n, read, dist) == expected


class _Lit(_Result):
    """Görüntülü (siyah olmayan) kare: telemetri sinyal kaybı saymasın."""
    def __init__(self, rows):
        super().__init__(rows)
        self.orig_img = np.random.default_rng(0).integers(0, 255, self.orig_img.shape, dtype=np.uint8)


class LitModel(FakeModel):
    def track(self, **kw):
        for r in super().track(**kw):
            lit = _Lit([])
            lit.boxes = r.boxes
            yield lit


def test_live_stream_end_is_disconnected_not_finished():
    s = RecSender()
    out = d.run_once(LitModel(SCENARIO), "camL", "Kule", SRC, s, 1)
    rep = s.reports[0]
    assert (rep["run_status"], rep["end_reason"]) == ("disconnected", "source_lost") == (out["status"],
                                                                                       out["end_reason"])
    assert out["frames"] == len(SCENARIO) and rep["status"] == "running" and rep["camera_id"] == "camL"
    assert rep["unclosed_incidents"] == [] or all(u["reason"] in ("run_end", "stale")
                                                   for u in rep["unclosed_incidents"])


class _BlackResult(_Result):
    def __init__(self):
        super().__init__([])
        self.orig_img = np.zeros_like(self.orig_img)


@pytest.mark.parametrize("black,reason", [(1, "source_lost"), (3, "source_lost_after_stall")])
def test_black_frames_before_end_mark_stall_reason(black, reason):
    """Ultralytics kopmada tek siyah kare üretir (kopma); >= 3 tek renk kare = sinyal kaybı sonrası kopma."""
    class Model(LitModel):
        def track(self, **kw):
            yield from super().track(**kw)
            for _ in range(black):
                yield _BlackResult()
    s = RecSender()
    d.run_once(Model([person()] * 5), "camB", "Kule", SRC, s, 1)
    assert s.reports[0]["end_reason"] == reason


def test_stop_and_exception_and_network_vod(monkeypatch):
    class Stop:
        def __init__(self):
            self.v = False

        def is_set(self):
            return self.v
    stop = Stop()
    s = RecSender()
    d.run_once(FakeModel(SCENARIO, on_frame=lambda i: setattr(stop, "v", i >= 20)), "camS", "Kule", SRC, s, 1, stop)
    assert (s.reports[0]["run_status"], s.reports[0]["end_reason"]) == ("interrupted", "user_stop")
    s2 = RecSender()
    with pytest.raises(OSError):
        d.run_once(FakeModel(SCENARIO, fail_at=10, exc=OSError("okuma hatası")), "camE", "Kule", SRC, s2, 1)
    assert (s2.reports[0]["run_status"], s2.reports[0]["end_reason"]) == ("error", "exception")

    class VodCap(FakeCap):
        def get(self, prop):
            return 30 if prop == d.cv2.CAP_PROP_FRAME_COUNT else super().get(prop)
    monkeypatch.setattr(d.cv2, "VideoCapture", VodCap)
    s3 = RecSender()
    d.run_once(FakeModel([person()] * 30), "camV", "Kule", "https://example.test/v.mp4", s3, 1)
    assert (s3.reports[0]["run_status"], s3.reports[0]["end_reason"]) == ("finished", "eof")


# ---------------------------------------------------------------- geri çekilme
def test_reconnect_delay_exponential_capped_with_jitter():
    rng = random.Random(1)
    base = [min(d.RECONNECT_MAX_SEC, d.RECONNECT_BASE_SEC * 2 ** (n - 1)) for n in range(1, 10)]
    got = [d.reconnect_delay(n, rng) for n in range(1, 10)]
    assert all(0.8 * b - 0.01 <= g <= 1.2 * b + 0.01 for b, g in zip(base, got))
    assert base[:4] == [2, 4, 8, 16] and base[-1] == d.RECONNECT_MAX_SEC == 60
    assert max(got) <= 72


class _Stop:
    """stop.wait gerçekten beklemez; istenen bekleme süreleri kaydedilir. n çağrıdan sonra durur."""
    def __init__(self, waits_until_stop=1000):
        self.flag, self.waits, self.left = False, [], waits_until_stop

    def is_set(self):
        return self.flag

    def wait(self, t):
        self.waits.append(t)
        self.left -= 1
        if self.left <= 0:
            self.flag = True
        return self.flag


class _Sender(RecSender):
    def start(self):
        pass

    def close(self, timeout):
        return {}

    def snapshot_stats(self):
        return {"events_sent": 0, "reports_sent": len(self.reports), "pending_count": 0, "dead_letter_count": 0,
                "quarantine_count": 0, "evicted_ongoing": 0, "dropped_ongoing_full": 0}


def _worker(monkeypatch, outcomes, stop, sender=None, max_backoffs=6):
    """run_once yerine sıralı sonuçlar (dict veya istisna); stream_worker'ın geri çekilme kararları ölçülür."""
    sender = sender or _Sender()
    calls = []

    def fake_run_once(model, cid, name, source, snd, loop_no, stp=None):
        calls.append(loop_no)
        o = outcomes[min(len(calls) - 1, len(outcomes) - 1)]
        if isinstance(o, BaseException):
            raise o
        return o
    monkeypatch.setattr(d, "run_once", fake_run_once)
    monkeypatch.setattr(d, "_make_sender", lambda cid, name: sender)
    monkeypatch.setattr(d, "YOLO", lambda path: object())
    monkeypatch.setattr(d.telemetry.CameraTelemetry, "set_output", lambda self, p: None)
    delays = []

    def delay(n, rng=None):
        delays.append(n)
        if len(delays) >= max_backoffs:
            stop.flag = True                               # bu kadar geri çekilmeden sonra dur
        return 0.001
    monkeypatch.setattr(d, "reconnect_delay", delay)
    d.stream_worker("camW", "Kule", SRC, 0, stop)
    return calls, delays, sender


def test_backoff_grows_on_repeated_failures_and_resets_after_healthy_run(monkeypatch):
    lost = {"status": "disconnected", "end_reason": "source_lost", "frames": 100}
    seq = [RuntimeError("açılamadı")] * 3 + [lost] + [RuntimeError("açılamadı")] * 2
    monkeypatch.setattr(d, "RECONNECT_HEALTHY_SEC", 0.0)   # kare veren run sağlıklı sayılsın
    calls, delays, sender = _worker(monkeypatch, seq, _Stop(), max_backoffs=6)
    assert delays[:6] == [1, 2, 3, 1, 2, 3]                # sağlıklı run'dan sonra sayaç 1'e döner
    assert len(calls) >= 6


def test_no_tight_loop_when_file_source_yields_no_frames(monkeypatch):
    calls, delays, _ = _worker(monkeypatch, [{"status": "finished", "end_reason": "eof", "frames": 0}], _Stop(),
                               max_backoffs=4)
    assert delays[:4] == [1, 2, 3, 4] and len(calls) <= 10   # her deneme bekletiliyor ve sayaç büyüyor


def test_file_loop_restarts_immediately_without_backoff(monkeypatch):
    stop = _Stop()
    ok = {"status": "finished", "end_reason": "eof", "frames": 50}
    n = {"c": 0}
    seq = [ok] * 5 + [{"status": "interrupted", "end_reason": "user_stop", "frames": 3}]

    def fake_run_once(model, cid, name, source, snd, loop_no, stp=None):
        n["c"] += 1
        if n["c"] >= 5:
            stp.flag = True
        return seq[n["c"] - 1]
    monkeypatch.setattr(d, "run_once", fake_run_once)
    monkeypatch.setattr(d, "_make_sender", lambda cid, name: _Sender())
    monkeypatch.setattr(d, "YOLO", lambda path: object())
    delays = []
    monkeypatch.setattr(d, "reconnect_delay", lambda k, rng=None: delays.append(k) or 0.001)
    d.stream_worker("camF", "Kule", "videos/a.mp4", 0, stop)
    assert n["c"] == 5 and delays == []                     # dosya döngüsü bekletilmez


def test_live_source_reconnects_even_with_loop_disabled(monkeypatch):
    monkeypatch.setattr(d, "LOOP", False)
    lost = {"status": "disconnected", "end_reason": "source_lost", "frames": 10}
    calls, delays, _ = _worker(monkeypatch, [lost], _Stop(), max_backoffs=3)
    assert len(calls) >= 2 and delays                      # eski davranışta süreç ilk kopmada biterdi


def test_offline_placeholder_frames_visible_to_operator_without_secrets(monkeypatch):
    sender = _Sender()
    _worker(monkeypatch, [RuntimeError("Kaynak açılamadı: rtsp://admin:S3cr3t@10.0.0.5/s")], _Stop(), sender,
            max_backoffs=2)
    assert sender.frames, "bağlantı yokken dashboard'a hiç kare gitmedi"
    p = sender.frames[0]
    assert p["meta"]["status"] == "reconnecting" and p["camera_id"] == "camW"
    assert "camera_disconnected" in p["meta"]["health"]["flags"]
    assert p["meta"]["error"] == "source_unavailable"
    dump = json.dumps(sender.frames)
    assert "S3cr3t" not in dump and "admin" not in dump
    img = d.offline_frame("Kule", "source_unavailable")
    assert img[:2] == b"\xff\xd8"


def test_default_stream_read_timeout_is_set_unless_user_overrides():
    assert "rw_timeout;" in __import__("os").environ.get("OPENCV_FFMPEG_CAPTURE_OPTIONS", "")
