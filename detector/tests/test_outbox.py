"""FAZ 7: kalıcı outbox (SQLite WAL) + DurableSender. GPU/ağ gerekmez; süreç ölümü ve çoklu süreç senaryoları
gerçek alt süreçlerle yapılır. Dashboard tarafı idempotency: dashboard/tests/test_ingest_events.py."""
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import requests

import outbox as ob
import protocol as p
from tests.conftest import FakeResp, wait_until

HERE = Path(__file__).resolve().parent.parent
JPG = b"\xff\xd8JPEG\xff\xd9"


def ev(kind="start", run="run-A", cam="cam1", pid=1, **extra):
    missing = "" if kind == "end" else "Baretsiz"
    e = {"type": kind, "pid": pid, "t": 1.0, "missing": missing, "helmet_rate": 10, "vest_rate": 90,
         "box": [1, 2, 3, 4], "event_id": p.new_id(), "incident_id": p.new_id(), "run_id": run, "camera_id": cam,
         "occurred_at": p.utc_now_iso(), "missing_helmet": kind != "end", "missing_vest": False}
    e.update(extra)
    return e


def rep(run="run-A", cam="cam1"):
    return {"cam": cam, "report": [], "run_id": run, "camera_id": cam, "run_status": "finished",
            "started_at": p.utc_now_iso(), "finished_at": p.utc_now_iso()}


class Fake:
    """requests.Session yerine; yolları ayırır. script: sırayla yanıt (int | (int, headers) | istisna | callable)."""
    def __init__(self, script=(), default=200):
        self.headers, self.calls, self.script, self.default = {}, [], list(script), default
        self.lock = threading.Lock()

    def post(self, url, files=None, data=None, headers=None, timeout=None):
        path = "/" + url.split("/", 3)[3]
        call = {"path": path, "token": self.headers.get("X-Token"), "t": time.monotonic()}
        if files is not None:
            call["payload"] = json.loads(data["payload"])
        else:
            call["raw"] = data
            call["body"] = json.loads(data)
        with self.lock:
            self.calls.append(call)
            action = self.script.pop(0) if self.script else self.default
        if callable(action) and not isinstance(action, type):
            action = action(call)
        if isinstance(action, BaseException):
            raise action
        return FakeResp(*action) if isinstance(action, tuple) else FakeResp(action)

    def of(self, path):
        return [c for c in self.calls if c["path"] == path]

    def event_ids(self):
        return [e["event_id"] for c in self.of("/ingest/events") for e in c["body"]["events"]]


@pytest.fixture
def box(tmp_path):
    made = []

    def make(cam="cam1", **kw):
        o = ob.Outbox(ob.path_for(cam, tmp_path), cam, **kw)
        made.append(o)
        return o
    make.dir = tmp_path
    yield make
    for o in made:
        try:
            o.close()
        except Exception:
            pass


def sender(outbox, session, start=True, **kw):
    logs = []
    opts = dict(session=session, timeout=1.0, backoff_base=0.01, backoff_max=0.05, auth_retry=0.05, log=logs.append)
    opts.update(kw)
    s = ob.DurableSender(outbox.camera_id, "Giriş", "http://dashboard.test", "tok", outbox, **opts)
    s.logs = logs
    if start:
        s.start()
    return s


def pending(o):
    return o.metrics()["pending_count"]


# ================================================================ A. dayanıklılık
def test_commit_survives_reopen_with_identical_ids_and_bytes(box):
    o = box()
    evs = [ev("start"), ev("ongoing"), ev("end")]
    o.put_events(evs)
    o.put_report(rep())
    o.close()
    o2 = box()
    rows = o2.head(10, 10**9)
    assert [json.loads(r["body"]) for r in rows] == evs                  # içerik ve kimlikler aynı
    assert o2.head(10, 10**9)[0]["msg_id"] == evs[0]["event_id"] and pending(o2) == 4
    assert o2.db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert o2.db.execute("PRAGMA synchronous").fetchone()[0] == 2        # FULL


KILL_SCRIPT = r"""
import os, sys, json
sys.path.insert(0, sys.argv[1])
import outbox as ob, protocol as p
o = ob.Outbox(ob.path_for("cam1", __import__("pathlib").Path(sys.argv[2])), "cam1")
ids = []
for i in range(int(sys.argv[3])):
    e = {"type": "start" if i % 3 == 0 else "ongoing", "pid": i, "event_id": p.new_id(), "run_id": "r", "camera_id": "cam1"}
    o.put_events([e]); ids.append(e["event_id"])
print(json.dumps(ids), flush=True)
os._exit(9)          # temizlik/kapatma YOK: çökme benzetimi (WAL checkpoint edilmez)
"""


def test_process_kill_after_commit_replays_same_event_ids(box):
    r = subprocess.run([sys.executable, "-c", KILL_SCRIPT, str(HERE), str(box.dir), "25"], capture_output=True,
                       text=True, timeout=60)
    assert r.returncode == 9, r.stderr
    ids = json.loads(r.stdout)
    assert Path(box.dir, "cam1.sqlite3-wal").exists()                    # checkpoint edilmemiş WAL kaldı
    o = box()                                                            # yeni süreç: bütünlük + kurtarma
    assert [r["msg_id"] for r in o.head(100, 10**9)] == ids


def test_hard_kill_with_taskkill_or_sigkill(box):
    """Gerçek süreç sonlandırma (Windows TerminateProcess / POSIX SIGKILL) yazım döngüsünün ortasında."""
    script = KILL_SCRIPT.replace("os._exit(9)", "import time; time.sleep(60)")
    proc = subprocess.Popen([sys.executable, "-c", script, str(HERE), str(box.dir), "40"], stdout=subprocess.PIPE,
                            text=True)
    ids = json.loads(proc.stdout.readline())
    proc.kill()
    proc.wait(30)
    o = box()
    assert [r["msg_id"] for r in o.head(100, 10**9)] == ids


def test_lost_ack_resends_identical_body(box):
    o = box()
    o.put_events([ev("start"), ev("end")])
    sess = Fake([requests.Timeout("yanıt kayboldu"), 200])
    s = sender(o, sess)
    assert wait_until(lambda: pending(o) == 0)
    s.close(1)
    raws = [c["raw"] for c in sess.of("/ingest/events")]
    assert len(raws) == 2 and raws[0] == raws[1]                         # birebir aynı bayt dizisi


def test_disk_full_is_visible_and_recovers_from_memory(box):
    o = box()
    o.db.execute("PRAGMA max_page_count = %d" % o.db.execute("PRAGMA page_count").fetchone()[0])
    sess = Fake()
    s = sender(o, sess, start=False)
    big = ev("start", snapshot_b64="A" * 200_000)
    s.submit(None, {}, [big])                                            # disk dolu
    assert len(s.unpersisted) == 1 and o.metrics()["disk_write_errors"] >= 1
    assert any("bellekte bekletiliyor" in m for m in s.logs)
    o.db.execute("PRAGMA max_page_count = 1073741823")                   # yer açıldı
    s.start()
    assert wait_until(lambda: big["event_id"] in sess.event_ids())
    s.close(1)
    assert s.snapshot_stats()["memory_fallback"] == 0 and s.counters["lost_unpersisted"] == 0


def test_same_camera_second_process_is_locked_out(box):
    o = box()
    o.put_events([ev()])
    with pytest.raises(ob.OutboxLocked):
        ob.Outbox(ob.path_for("cam1", box.dir), "cam1", lock_timeout=0.2)
    code = ("import sys; sys.path.insert(0, sys.argv[1]); import outbox as ob, pathlib\n"
            "try:\n ob.Outbox(ob.path_for('cam1', pathlib.Path(sys.argv[2])), 'cam1', lock_timeout=0.3)\n"
            "except ob.OutboxLocked: print('LOCKED')")
    r = subprocess.run([sys.executable, "-c", code, str(HERE), str(box.dir)], capture_output=True, text=True,
                       timeout=60)
    assert r.stdout.strip() == "LOCKED", r.stderr


def test_corrupt_file_is_never_deleted_or_reset(tmp_path):
    f = ob.path_for("cam1", tmp_path)
    f.write_bytes(b"bu bir sqlite dosyasi degil" * 100)
    before = f.read_bytes()
    with pytest.raises(ob.OutboxCorrupt):
        ob.Outbox(f, "cam1")
    assert f.read_bytes() == before                                      # sessiz silme/sıfırlama yok


def test_corrupt_wal_does_not_crash_or_delete(box):
    """WAL çerçeveleri sağlama toplamlıdır: bozulma noktasından SONRAKİ işlemler kurtarılamaz (belgelenmiş sınır);
    dosya açılır, önceki satırlar sağlamdır, hiçbir dosya silinmez."""
    r = subprocess.run([sys.executable, "-c", KILL_SCRIPT, str(HERE), str(box.dir), "30"], capture_output=True,
                       text=True, timeout=60)
    ids = json.loads(r.stdout)
    wal = Path(box.dir, "cam1.sqlite3-wal")
    data = bytearray(wal.read_bytes())
    mid = len(data) // 2
    data[mid:mid + 64] = b"\x00" * 64
    wal.write_bytes(bytes(data))
    o = box()
    got = [r["msg_id"] for r in o.head(100, 10**9)]
    assert got == ids[:len(got)] and len(got) < len(ids)                 # önek korunur, sonrası kaybolur
    assert Path(box.dir, "cam1.sqlite3").exists()


def test_checksum_mismatch_row_goes_dead_not_sent(box):
    o = box()
    e = ev()
    o.put_events([e])
    o.db.execute("UPDATE outbox SET body = replace(body, 'Baretsiz', 'Xaretsiz')")
    sess = Fake()
    s = sender(o, sess)
    assert wait_until(lambda: o.metrics()["dead_letter_count"] == 1)
    s.close(1)
    assert sess.of("/ingest/events") == []


def test_camera_id_mismatch_refused(box):
    box("cam1").close()
    os.replace(ob.path_for("cam1", box.dir), ob.path_for("cam2", box.dir))
    with pytest.raises(ob.OutboxError, match="başka kameraya"):
        ob.Outbox(ob.path_for("cam2", box.dir), "cam2")


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL")
def test_new_outbox_dir_acl_restricted_to_owner(tmp_path):
    d = tmp_path / "yeni_outbox"
    o = ob.Outbox(ob.path_for("cam1", d), "cam1")
    o.close()
    acl = subprocess.run(["icacls", str(d)], capture_output=True, text=True).stdout
    assert "BUILTIN\\Users" not in acl and "Everyone" not in acl and "(I)" not in acl, acl   # miras kesildi
    assert "NT AUTHORITY\\SYSTEM" in acl and os.environ.get("USERNAME", "") in acl


# ================================================================ B. yeniden deneme / sınıflandırma
def test_503_and_network_errors_retry_same_ids_with_exponential_backoff(box):
    o = box()
    e = ev()
    o.put_events([e])
    sess = Fake([503, 503, requests.ConnectionError("x"), 503, 200])
    s = sender(o, sess, backoff_base=0.02, backoff_max=1.0)
    assert wait_until(lambda: pending(o) == 0, timeout=10)
    s.close(1)
    calls = sess.of("/ingest/events")
    assert [c["body"]["events"][0]["event_id"] for c in calls] == [e["event_id"]] * 5
    gaps = [b["t"] - a["t"] for a, b in zip(calls, calls[1:])]
    assert gaps[-1] > gaps[0]                                            # geri çekilme büyüyor
    assert s.counters["retries"] == 4


def test_retry_after_header_respected(box):
    o = box()
    o.put_events([ev()])
    sess = Fake([(429, {"Retry-After": "0.3"}), 200])
    s = sender(o, sess)
    assert wait_until(lambda: pending(o) == 0, timeout=5)
    s.close(1)
    c = sess.of("/ingest/events")
    assert c[1]["t"] - c[0]["t"] >= 0.28


def test_401_pauses_without_drop_then_token_fix_replays(box):
    o = box()
    evs = [ev("start"), ev("end")]
    o.put_events(evs)
    token = {"v": "eski"}
    sess = Fake([lambda c: 200 if c["token"] == "yeni" else 401] * 50)
    s = sender(o, sess, token_provider=lambda: token["v"])
    assert wait_until(lambda: s.state == ob.DurableSender.STATE_AUTH)
    time.sleep(0.2)
    assert pending(o) == 2 and o.metrics()["dead_letter_count"] == 0     # düşürme yok
    assert any("DURAKLATILDI" in m for m in s.logs)
    token["v"] = "yeni"                                                  # operatör detector/.env'i düzeltti
    assert wait_until(lambda: pending(o) == 0)
    s.close(1)
    assert sess.event_ids()[-2:] == [e["event_id"] for e in evs] and s.state == "ok"


def test_403_also_pauses(box):
    o = box()
    o.put_events([ev()])
    sess = Fake(default=403)
    s = sender(o, sess)
    assert wait_until(lambda: s.state == ob.DurableSender.STATE_AUTH)
    s.close(0.2)
    assert s.snapshot_stats()["pending_count"] == 1


def test_404_old_dashboard_pauses_not_drops(box):
    o = box()
    o.put_events([ev()])
    s = sender(o, Fake(default=404))
    assert wait_until(lambda: s.state == ob.DurableSender.STATE_INCOMPATIBLE)
    s.close(0.2)
    assert s.snapshot_stats()["pending_count"] == 1 and s.snapshot_stats()["dead_letter_count"] == 0


def test_422_in_batch_isolated_to_dead_letter_others_delivered(box):
    o = box()
    good1, bad, good2 = ev(pid=1), ev(pid=2), ev(pid=3)
    o.put_events([good1, bad, good2])
    sess = Fake([lambda c: 422 if any(e["event_id"] == bad["event_id"] for e in c["body"]["events"]) else 200] * 20)
    s = sender(o, sess)
    assert wait_until(lambda: pending(o) == 0)
    s.close(1)
    m = s.snapshot_stats()
    assert m["dead_letter_count"] == 1 and len(sess.calls) <= 6         # sonsuz döngü yok
    assert set(sess.event_ids()) >= {good1["event_id"], good2["event_id"]}


def test_409_goes_to_quarantine_and_requeue_works(box):
    o = box()
    e = ev()
    o.put_events([e])
    s = sender(o, Fake([409]))
    assert wait_until(lambda: o.metrics()["quarantine_count"] == 1)
    s.close(0.2)
    o2 = box()
    (f,) = o2.list_failed()
    assert f["state"] == "quarantined" and f["reason"] == "HTTP 409" and f["msg_id"] == e["event_id"]
    assert o2.requeue(f["seq"]) == 1 and pending(o2) == 1
    assert json.loads(o2.head(1, 10**9)[0]["body"]) == e                 # gövde/kimlik değişmedi


# ================================================================ C. sıralama / bariyer / eşzamanlılık
def test_fifo_order_across_batches_and_restart(box):
    o = box()
    evs = [ev(kind, pid=7) for _ in range(15) for kind in ("start", "ongoing", "ongoing", "end")]
    o.put_events(evs[:30])
    o.close()
    o2 = box()
    o2.put_events(evs[30:])
    sess = Fake()
    s = sender(o2, sess, batch_max=7)
    assert wait_until(lambda: pending(o2) == 0)
    s.close(1)
    assert sess.event_ids() == [e["event_id"] for e in evs]


def test_report_barrier_waits_for_prior_events(box):
    o = box()
    e = ev("start")
    o.put_events([e])
    o.put_report(rep())
    gate = threading.Event()
    sess = Fake([lambda c: 200 if gate.is_set() else 503] * 100)
    s = sender(o, sess)
    time.sleep(0.3)
    assert sess.of("/ingest/report") == []                               # olay teslim edilmeden rapor yok
    gate.set()
    assert wait_until(lambda: pending(o) == 0)
    s.close(1)
    paths = [c["path"] for c in sess.calls if c.get("path") != "/ingest/frame"]
    assert paths.index("/ingest/report") > max(i for i, x in enumerate(paths) if x == "/ingest/events")


def test_report_after_dead_event_sent_with_integrity_warning(box):
    o = box()
    o.put_events([ev()])
    o.put_report(rep())
    sess = Fake([422, 200])
    s = sender(o, sess)
    assert wait_until(lambda: pending(o) == 0)
    s.close(1)
    assert len(sess.of("/ingest/report")) == 1 and any("veri bütünlüğü eksik" in m for m in s.logs)


def test_same_pid_different_runs_keep_own_ids(box):
    o = box()
    a, b = ev("start", run="run-1", pid=3), ev("start", run="run-2", pid=3)
    o.put_events([a, b])
    rows = o.head(10, 10**9)
    assert [json.loads(r["body"])["run_id"] for r in rows] == ["run-1", "run-2"] and rows[0]["run_id"] == "run-1"


def test_duplicate_submission_is_ignored(box):
    o = box()
    e = ev()
    o.put_events([e])
    o.put_events([e])
    o.put_report(rep())
    o.put_report(rep())
    assert pending(o) == 2


def test_concurrent_producers_single_process(box):
    o = box()
    per, n = 100, 4

    def produce():
        for _ in range(per):
            o.put_events([ev("ongoing")])
    ts = [threading.Thread(target=produce) for _ in range(n)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert pending(o) == per * n
    assert o.db.execute("SELECT count(DISTINCT seq) FROM outbox").fetchone()[0] == per * n


PROC_SCRIPT = r"""
import sys, pathlib
sys.path.insert(0, sys.argv[1])
import outbox as ob, protocol as p
cam = sys.argv[3]
o = ob.Outbox(ob.path_for(cam, pathlib.Path(sys.argv[2])), cam)
for i in range(300):
    o.put_events([{"type": "ongoing", "pid": i, "event_id": p.new_id(), "run_id": "r-" + cam, "camera_id": cam}])
print(o.metrics()["pending_count"]); o.close()
"""


def test_parallel_camera_processes_do_not_interfere(tmp_path):
    procs = [subprocess.Popen([sys.executable, "-c", PROC_SCRIPT, str(HERE), str(tmp_path), f"cam{i}"],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for i in range(1, 5)]
    outs = [pr.communicate(timeout=120) for pr in procs]
    assert all(pr.returncode == 0 for pr in procs), [o[1][-300:] for o in outs]
    assert [o[0].strip() for o in outs] == ["300"] * 4
    for i in range(1, 5):
        o = ob.Outbox(ob.path_for(f"cam{i}", tmp_path), f"cam{i}")
        assert {json.loads(r["body"])["camera_id"] for r in o.head(500, 10**9)} == {f"cam{i}"}
        o.close()


def test_shutdown_with_server_down_keeps_queue_on_disk(box):
    o = box()
    evs = [ev("start"), ev("end")]
    s = sender(o, Fake(default=503))
    s.submit(JPG, {"run_id": "run-A"}, evs)
    s.submit_report(rep())
    res = s.close(0.3)
    assert res["pending"] == 3 and any("diskte" in m for m in s.logs)
    o2 = box()
    order = [r[0] for r in o2.db.execute("SELECT msg_id FROM outbox WHERE state = 'pending' ORDER BY seq")]
    assert order == [e["event_id"] for e in evs] + ["report:run-A"]
    assert [r["msg_id"] for r in o2.head(10, 10**9)] == [e["event_id"] for e in evs]   # rapor bariyerin arkasında


# ================================================================ D. kapasite / metrikler
def test_capacity_thins_ongoing_but_never_critical(box):
    o = box(max_items=5, critical_reserve=0.4)
    o.put_events([ev("start")] + [ev("ongoing") for _ in range(4)])
    o.put_events([ev("ongoing")])                                        # en eski ongoing atılır
    assert pending(o) == 5 and o.metrics()["evicted_ongoing"] == 1
    o.put_events([ev("end"), ev("start")])                               # kritik: yedek alana
    assert pending(o) == 7 and o.metrics()["high_watermark"] is True
    with pytest.raises(ob.OutboxFull):
        o.put_events([ev("end")])                                        # yedek de doldu: GÖRÜNÜR hata
    kinds = [r["event_type"] for r in o.head(50, 10**9)]
    assert kinds.count("start") == 2 and kinds.count("end") == 1


def test_outbox_full_critical_goes_to_memory_not_silently_lost(box):
    o = box(max_items=1, critical_reserve=0.0)
    s = sender(o, Fake(default=503), start=False)
    s.submit(None, {}, [ev("start")])
    s.submit(None, {}, [ev("end")])
    assert pending(o) == 1 and len(s.unpersisted) == 1
    assert any("bellekte bekletiliyor" in m for m in s.logs)


def test_metrics_fields(box):
    o = box()
    o.put_events([ev()])
    m = o.metrics()
    for k in ("pending_count", "pending_bytes", "oldest_pending_age_sec", "pending_retry_attempts", "delivered_count",
              "dead_letter_count", "quarantine_count", "disk_write_errors", "evicted_ongoing", "high_watermark"):
        assert k in m, k
    assert m["pending_count"] == 1 and m["pending_bytes"] > 0


def test_status_file_written_without_secrets(box):
    o = box()
    st = box.dir / "cam1.status.json"
    s = sender(o, Fake(), status_path=st, status_every=0.0)
    s.submit(None, {}, [ev()])
    assert wait_until(lambda: pending(o) == 0)
    s.close(1)
    data = json.loads(st.read_text(encoding="utf-8"))
    assert data["camera_id"] == "cam1" and "tok" not in st.read_text(encoding="utf-8")


# ================================================================ operatör komutları
def _cli(d, *args, env=None):
    return subprocess.run([sys.executable, "outbox.py", "--dir", str(d), *args], cwd=HERE, capture_output=True,
                          text=True, timeout=120, env={**os.environ, **(env or {})})


def test_cli_check_list_requeue_status(box):
    o = box()
    e = ev()
    o.put_events([e, ev("end")])
    o.mark_failed(o.head(1, 10**9)[0]["seq"], "dead", "HTTP 422", 422)
    r = _cli(box.dir, "status")
    assert "çalışan dedektör kullanıyor" in r.stdout                      # kilitliyken status.json'a düşer
    o.close()
    assert _cli(box.dir, "check", "cam1").stdout.startswith("ok")
    lf = _cli(box.dir, "list-failed", "cam1").stdout
    assert e["event_id"] in lf and "HTTP 422" in lf and "snapshot_b64" not in lf
    assert "--seq N veya --all" in _cli(box.dir, "requeue", "cam1").stderr
    assert "yeniden kuyruğa alındı: 1" in _cli(box.dir, "requeue", "cam1", "--all").stdout
    assert '"pending_count": 2' in _cli(box.dir, "status").stdout


def test_cli_drain_delivers_pending_without_video(box):
    import http.server
    got = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            got.append((self.path, self.headers.get("X-Token"), self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):
            pass
    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    o = box()
    evs = [ev("start"), ev("end")]
    o.put_events(evs)
    o.put_report(rep())
    o.close()
    r = _cli(box.dir, "drain", "cam1", "--timeout", "20",
             env={"DASHBOARD_URL": f"http://127.0.0.1:{srv.server_port}", "INGEST_TOKEN": "test-tok",
                  "DETECTOR_ENV_FILE": str(box.dir / "yok.env")})
    srv.shutdown()
    assert r.returncode == 0 and "bekleyen: 0" in r.stdout, r.stdout + r.stderr
    assert [g[0] for g in got] == ["/ingest/events", "/ingest/report"] and {g[1] for g in got} == {"test-tok"}
    assert [x["event_id"] for x in json.loads(got[0][2])["events"]] == [e["event_id"] for e in evs]


# ================================================================ E. kareler ve snapshot
def test_frames_latest_wins_not_persisted_and_flow_during_event_backoff(box):
    o = box()
    sess = Fake([lambda c: 200 if c["path"] == "/ingest/frame" else 503] * 200)
    s = sender(o, sess)
    s.submit(JPG, {"run_id": "run-A"}, [ev()])
    for i in range(5):
        s.submit(JPG + bytes([i]), {"run_id": "run-A", "i": i})
        time.sleep(0.05)
    assert wait_until(lambda: len(sess.of("/ingest/frame")) >= 2)
    s.close(0.1)
    assert all(c["payload"]["events"] == [] for c in sess.of("/ingest/frame"))
    o2 = box()
    assert all(r["kind"] == "event" for r in o2.head(100, 10**9))       # outbox'ta kare yok


def test_snapshot_body_erased_after_delivery(box):
    o = box(delivered_retention=3600)
    e = ev("start", snapshot_b64="QUJD" * 1000)
    o.put_events([e])
    s = sender(o, Fake())
    assert wait_until(lambda: pending(o) == 0)
    s.close(1)
    o2 = box()
    assert o2.db.execute("SELECT state, body FROM outbox").fetchall() == [("delivered", "")]
