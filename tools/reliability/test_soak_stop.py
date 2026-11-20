"""FAZ 02-B: soak ölçüm döngüsünün durma nedenleri ve Ctrl+C'de sonuçların yine yazılması (Docker/dedektör gerektirmez).
Proje kökünden:  detector\\.venv\\Scripts\\python -m pytest -q tools/reliability/test_soak_stop.py"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import soak  # noqa: E402

GB = 2**30


class FakeProc:
    def __init__(self, exit_after=None, rc=3):
        self.polls, self.exit_after, self.returncode, self.pid = 0, exit_after, None, 4242
        self._rc = rc

    def poll(self):
        self.polls += 1
        if self.exit_after is not None and self.polls > self.exit_after:
            self.returncode = self._rc
        return self.returncode


@pytest.fixture
def host(monkeypatch):
    """Bol bellek ve disk; time.sleep ve signal.signal kaydedilir (pytest sürecinin SIGINT'i değişmez)."""
    state = {"free_gb": 100.0, "sleeps": 0, "interrupt_on_sleep": None, "signals": []}
    monkeypatch.setattr(soak.psutil, "virtual_memory", lambda: SimpleNamespace(available=8 * GB))
    monkeypatch.setattr(soak.psutil, "swap_memory", lambda: SimpleNamespace(percent=0.0, used=0))
    monkeypatch.setattr(soak.psutil, "disk_usage", lambda p: SimpleNamespace(free=state["free_gb"] * GB))

    def fake_sleep(sec):
        state["sleeps"] += 1
        if state["interrupt_on_sleep"] == state["sleeps"]:
            raise KeyboardInterrupt

    monkeypatch.setattr(soak.time, "sleep", fake_sleep)
    monkeypatch.setattr(soak.signal, "signal", lambda sig, handler: state["signals"].append((sig, handler)))
    return state


def test_ctrl_c_is_caught_and_marked(host, tmp_path):
    host["interrupt_on_sleep"] = 3
    res = {}
    soak.measure(FakeProc(), hours=1, min_disk_gb=5, sd=tmp_path, res=res)   # KeyboardInterrupt dışarı sızmaz
    assert res["stop_reason"] == "operator_interrupt"
    assert "measured_sec" in res and "measure_end_utc" in res
    assert host["signals"] == [(soak.signal.SIGINT, soak.signal.SIG_IGN)]  # ikinci Ctrl+C son işlemleri bölmez


def test_duration_reached(host, tmp_path):
    res = {}
    soak.measure(FakeProc(), hours=0, min_disk_gb=5, sd=tmp_path, res=res)
    assert res["stop_reason"] == "duration" and not host["signals"]


def test_detector_exit(host, tmp_path):
    res = {}
    soak.measure(FakeProc(exit_after=2, rc=3), hours=1, min_disk_gb=5, sd=tmp_path, res=res)
    assert res["stop_reason"] == "detector_exit" and res["detector_exit"] == 3


def test_disk_guard_after_ten_low_checks(host, tmp_path):
    host["free_gb"] = 1.0
    res = {}
    soak.measure(FakeProc(), hours=1, min_disk_gb=5, sd=tmp_path, res=res)
    assert res["stop_reason"] == "disk_guard" and res["disk_guard"]["min_gb"] == 5
    assert host["sleeps"] == 9


def test_main_writes_result_json_after_ctrl_c(host, monkeypatch, tmp_path):
    """Hata kaydı: Ctrl+C'den sonra finally dedektörü kapatıyor ama result.json yazılmıyordu."""
    out = tmp_path / "soak"
    sd = out / "cams4_stride2"
    stopped = {}

    class Dummy:
        rows = []

        def __init__(self, *a, **kw):
            self.stop_flag = SimpleNamespace(set=lambda: None)

        def start(self):
            return self

        def stop(self):
            pass

        url = "tcp://127.0.0.1:1"

        def cut(self):
            pass

    def fake_start_detector(sources, outbox, log, stride):
        log.write_text("[relc1] gönderici kapandı: 2 olay, 1 rapor iletildi\n"
                       "[relnet] gönderici kapandı: 3 olay, 1 rapor iletildi\n", encoding="utf-8")
        return FakeProc()

    def fake_psql(sql, timeout=30):
        return "5\t5" if "count(DISTINCT" in sql else "relnet\tinterrupted\tuser_stop\t1"

    c = soak.c
    monkeypatch.setattr(c, "outdir", lambda name: out.mkdir(parents=True, exist_ok=True) or out)
    monkeypatch.setattr(c, "image_provenance", lambda: {"pinned": False, "matches_requested": None})
    monkeypatch.setattr(c, "git_info", lambda: {"commit": "x", "dirty": False})
    monkeypatch.setattr(c, "reset_data", lambda: None)
    monkeypatch.setattr(c, "VIDEOS", {"V1": Path("v1.mp4"), "V2": Path("v2.mp4")})
    monkeypatch.setattr(c, "start_detector", fake_start_detector)
    monkeypatch.setattr(c, "stop_detector", lambda p, timeout=40: stopped.update(called=True) or {"graceful": True})
    monkeypatch.setattr(c, "psql", fake_psql)
    monkeypatch.setattr(soak, "CameraSim", Dummy)
    monkeypatch.setattr(soak, "Sampler", Dummy)
    monkeypatch.setattr(soak, "FaultSchedule", Dummy)
    monkeypatch.setattr(soak.stress, "wait_connected", lambda outbox, cams, timeout: 1.0)
    monkeypatch.setattr(soak.stress, "outbox_final",
                        lambda outbox: {"relnet": {"pending": 0, "dead": 0, "quarantine": 0, "event_ids": ["a"]}})
    monkeypatch.setattr(soak, "write_csv", lambda sd, rows: None)
    monkeypatch.setattr(soak, "summarize", lambda rows, skip_sec: {})
    host["interrupt_on_sleep"] = 1

    soak.main(["--hours", "1"])

    assert stopped["called"]
    res = json.loads((sd / "result.json").read_text(encoding="utf-8"))
    assert res["stop_reason"] == "operator_interrupt"
    assert res["delivery"] == {"events_sent_by_detector": 5, "db_events": 5, "db_distinct_event_ids": 5, "lost": 0,
                               "duplicates": 0, "pending_after_stop": 0, "dead_after_stop": 0}
