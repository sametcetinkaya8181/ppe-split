"""FAZ 9 arıza enjeksiyonu (YALNIZ izole ppe-rel yığını + geçici outbox dizinleri). Her senaryo: sentetik kamera
(gerçek PPEMonitor/IncidentTracker/DurableSender/SQLite outbox) veya gerçek dedektör çalışırken arıza uygulanır,
kaldırılır, kuyruk boşalır; sonra defter (outbox'a commit edilmiş kimlikler) PostgreSQL ile karşılaştırılır.

  python tools/reliability/failures.py [--only A,B,...]
Senaryolar: A PostgreSQL durdur/başlat, B dashboard restart (+K WebSocket kopması), C süreç öldürme + replay,
E ağ zaman aşımı + ACK kaybı, F 503, G 401 (gerçek yanlış token, yeniden başlatmadan düzeltme) + 403, H SQLite kilitli,
I outbox kapasitesi (disk dolu benzetimi), J bozuk/büyük payload, D kamera/akış kopması (gerçek dedektör, ffmpeg ağ
akışı), L container restart (gerçek dedektör çalışırken), M gerçek dedektör zorla öldürme + yeniden başlatma."""
import argparse
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as c                                     # noqa: E402
from faultproxy import FaultProxy                      # noqa: E402
from wsprobe import WSProbe                            # noqa: E402

EVENTGEN = c.HERE / "eventgen.py"


# ---------------------------------------------------------------- yardımcılar
class Gen:
    """Sentetik kamera süreci."""

    def __init__(self, sd: Path, cam: str, *, url: str, duration: float, token=None, extra=None, run_seconds=20,
                 persons=3, period=6.0, snapshot=True, tag="gen"):
        self.sd, self.cam = sd, cam
        self.outbox = sd / "outbox"
        env = c.detector_env(self.outbox, token=token, url=url, extra=extra)
        self.log = sd / f"{tag}.log"
        cmd = [str(c.PY), str(EVENTGEN), "--camera", cam, "--outbox", str(self.outbox), "--ledger",
               str(sd / "ledger.jsonl"), "--duration", str(duration), "--run-seconds", str(run_seconds),
               "--persons", str(persons), "--period", str(period)] + (["--snapshot"] if snapshot else [])
        self.p = subprocess.Popen(cmd, cwd=c.HERE, env=env, stdout=open(self.log, "a", encoding="utf-8"),
                                  stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)

    def wait(self, timeout=120):
        return self.p.wait(timeout)

    def kill(self):
        c.kill_tree(self.p.pid)
        self.p.wait(10)

    def status(self) -> dict:
        try:
            return json.loads((self.outbox / f"{self.cam}.status.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def final(self) -> dict:
        lines = [l for l in self.log.read_text(encoding="utf-8", errors="replace").splitlines() if l.startswith("{")]
        return json.loads(lines[-1]) if lines else {}


class Timeline(threading.Thread):
    """Outbox status.json'u 0.5 sn'de bir okur: bekleyen sayısı, durum, uyarılar."""

    def __init__(self, gen: Gen):
        super().__init__(daemon=True)
        self.gen, self.rows, self.stop_flag, self.t0 = gen, [], False, time.monotonic()

    def run(self):
        while not self.stop_flag:
            s = self.gen.status()
            if s:
                self.rows.append({"t": round(time.monotonic() - self.t0, 2), "pending": s.get("pending_count"),
                                  "state": s.get("state"), "warnings": s.get("warnings"),
                                  "oldest": s.get("oldest_pending_age_sec"), "retries": s.get("retries"),
                                  "evicted": s.get("evicted_ongoing"), "dropped": s.get("dropped_ongoing_full"),
                                  "mem": s.get("memory_fallback"), "auth_pauses": s.get("auth_pauses")})
            time.sleep(0.5)

    def now(self):
        return round(time.monotonic() - self.t0, 2)

    def stop(self):
        self.stop_flag = True
        self.join(3)

    def recovery_after(self, t_clear: float) -> float | None:
        """Arıza kaldırıldıktan sonra kuyruğun ilk kez 0'a indiği süre."""
        for r in self.rows:
            if r["t"] >= t_clear and r["pending"] == 0:
                return round(r["t"] - t_clear, 2)
        return None

    def summary(self) -> dict:
        p = [r["pending"] for r in self.rows if r["pending"] is not None]
        return {"pending_max": max(p, default=None), "states": sorted({r["state"] for r in self.rows if r["state"]}),
                "warnings_seen": sorted({w for r in self.rows for w in (r["warnings"] or [])}),
                "oldest_max_sec": max((r["oldest"] or 0 for r in self.rows), default=None),
                "evicted_max": max((r["evicted"] or 0 for r in self.rows), default=0),
                "dropped_max": max((r["dropped"] or 0 for r in self.rows), default=0),
                "memory_fallback_max": max((r["mem"] or 0 for r in self.rows), default=0)}


def verify(sd: Path, cam: str) -> dict:
    led = [json.loads(l) for l in (sd / "ledger.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    ev = [r for r in led if r["kind"] == "event"]
    durable = {r["event_id"]: r["type"] for r in ev if r.get("durable", True)}
    nondurable = {r["event_id"]: r["type"] for r in ev if not r.get("durable", True)}
    rows = c.psql(f"SELECT event_id, event_type FROM violation_events WHERE camera_id = '{cam}'")
    db = {}
    for line in rows.splitlines():
        eid, typ = line.split("\t")
        db[eid] = typ
    dup = c.psql(f"SELECT count(*) - count(DISTINCT event_id) FROM violation_events WHERE camera_id = '{cam}'")
    reports = {r["run_id"]: r["status"] for r in led if r["kind"] == "report"}
    runs = dict(tuple(l.split("\t")) for l in c.psql(
        f"SELECT id, status FROM detector_runs WHERE camera_id = '{cam}'").splitlines() if l)
    missing = {e: t for e, t in durable.items() if e not in db}
    return {"ledger_events": len(ev), "ledger_durable": len(durable), "ledger_non_durable": len(nondurable),
            "db_events": len(db), "duplicate_rows": int(dup),
            "missing_durable": len(missing), "missing_by_type": _count(missing.values()),
            "missing_non_durable": sum(1 for e in nondurable if e not in db),
            "db_not_in_ledger": len(set(db) - set(durable) - set(nondurable)),
            "reports_in_ledger": len(reports),
            "reports_missing_in_db": sum(1 for r in reports if r not in runs),
            "runs_by_status": _count(runs.values()),
            "report_status_mismatch": sum(1 for r, st in reports.items() if r in runs and runs[r] != st)}


def _count(xs) -> dict:
    out = {}
    for x in xs:
        out[x] = out.get(x, 0) + 1
    return out


def ops_metrics() -> dict:
    try:
        return c.Session().get("/api/ops/metrics").json()
    except Exception as exc:                              # noqa: BLE001
        return {"error": type(exc).__name__}


def wait_ready(timeout=120) -> float:
    return round(c.wait_http(c.base_url() + "/health/ready", timeout), 2)


def probe_status(path="/health/ready") -> int | str:
    try:
        return requests.get(c.base_url() + path, timeout=3).status_code
    except requests.RequestException as exc:
        return type(exc).__name__


def gen_scenario(name: str, out: Path, *, duration=60, fault=None, clear_at=None, url=None, gen_kw=None,
                 after=None) -> dict:
    """Ortak akış: üretici başlat -> fault() -> clear() -> üretici bitsin -> kuyruk boşalsın -> doğrula."""
    sd = out / name
    sd.mkdir(parents=True, exist_ok=True)
    cam = f"relf{name.lower()}"[:40]
    c.reset_data()
    g = Gen(sd, cam, url=url or c.base_url(), duration=duration, **(gen_kw or {}))
    tl = Timeline(g)
    tl.start()
    notes = {"started_utc": c.utc()}
    try:
        if fault:
            notes.update(fault(g, tl) or {})
        g.wait(duration + 120)
    finally:
        tl.stop()
    notes["ended_utc"] = c.utc()
    if after:
        notes.update(after(g, tl) or {})
    res = {"scenario": name, **notes, "timeline": tl.summary(), "final": g.final(), "verify": verify(sd, cam)}
    if "t_clear" in notes:
        res["recovery_sec_after_clear"] = tl.recovery_after(notes["t_clear"])
    c.write_json(sd / "timeline.json", tl.rows)
    c.write_json(sd / "result.json", res)
    return res


# ---------------------------------------------------------------- senaryolar
def scen_A(out):
    def fault(g, tl):
        time.sleep(10)
        n = {"t_fault": tl.now()}
        subprocess.run(["docker", "stop", c.container("db")], capture_output=True, timeout=60)
        time.sleep(3)
        n["during"] = {"healthz": probe_status("/healthz"), "ready": probe_status("/health/ready")}
        time.sleep(22)
        subprocess.run(["docker", "start", c.container("db")], capture_output=True, timeout=60)
        n["t_db_started"] = tl.now()
        n["db_ready_after_start_sec"] = wait_ready()
        n["t_clear"] = tl.now()
        return n
    return gen_scenario("A_pg_stop_start", out, duration=70, fault=fault)


def scen_B_K(out):
    sess = c.Session()
    probe = WSProbe(c.base_url().replace("http", "ws") + "/ws", lambda: sess.cookie, c.base_url())
    probe.start()

    def fault(g, tl):
        time.sleep(12)
        n = {"t_fault": tl.now(), "ws_before": dict(probe.stats, per_cam={})}
        t = time.monotonic()
        subprocess.run(["docker", "restart", c.container("dashboard")], capture_output=True, timeout=120)
        n["restart_cmd_sec"] = round(time.monotonic() - t, 2)
        n["ready_after_restart_sec"] = wait_ready()
        n["t_clear"] = tl.now()
        t2 = time.monotonic()
        while probe.stats["connects"] < 2 and time.monotonic() - t2 < 30:
            time.sleep(0.2)
        n["ws_reconnect_after_ready_sec"] = round(time.monotonic() - t2, 2) if probe.stats["connects"] >= 2 else None
        f0 = probe.stats["frames"]
        time.sleep(3)
        n["ws_frames_3s_after_reconnect"] = probe.stats["frames"] - f0
        return n

    res = gen_scenario("B_dashboard_restart_K_ws", out, duration=60, fault=fault)
    probe.stop_flag = True
    res["ws_probe"] = {k: v for k, v in probe.stats.items() if k != "per_cam"}
    c.write_json(out / "B_dashboard_restart_K_ws" / "result.json", res)
    return res


def scen_C(out):
    """Sentetik kamera DB'ye ulaşamazken zorla öldürülür (outbox'ta bekleyen var), sonra aynı outbox ile başlar."""
    px = FaultProxy(c.base_url()).start()
    sd = out / "C_kill_replay"
    sd.mkdir(parents=True, exist_ok=True)
    cam = "relfc"
    c.reset_data()
    g = Gen(sd, cam, url=px.url, duration=60, tag="gen1")
    time.sleep(6)
    px.set("status:503")
    time.sleep(14)
    pend_before_kill = g.status().get("pending_count")
    t_kill = time.monotonic()
    g.kill()
    px.set("pass")
    g2 = Gen(sd, cam, url=px.url, duration=15, tag="gen2")
    rec = None
    while time.monotonic() - t_kill < 60:
        s = g2.status()
        if s and s.get("pending_count") == 0 and s.get("events_sent", 0) > 0:
            rec = round(time.monotonic() - t_kill, 2)
            break
        time.sleep(0.25)
    g2.wait(120)
    px.stop()
    res = {"scenario": "C_kill_replay", "pending_at_kill": pend_before_kill, "recovery_sec_after_restart": rec,
           "final_after_restart": g2.final(), "verify": verify(sd, cam)}
    c.write_json(sd / "result.json", res)
    return res


def proxy_scenario(name, out, mode, *, start=10, length=20, duration=60, gen_kw=None):
    px = FaultProxy(c.base_url()).start()

    def fault(g, tl):
        time.sleep(start)
        px.set(mode)
        n = {"t_fault": tl.now(), "mode": mode}
        time.sleep(length)
        px.set("pass")
        n["t_clear"] = tl.now()
        return n
    m0 = ops_metrics()
    res = gen_scenario(name, out, duration=duration, fault=fault, url=px.url, gen_kw=gen_kw)
    m1 = ops_metrics()
    res["proxy_counts"] = {f"{k[0]}|{k[1]}": v for k, v in px.counts.items()}
    try:
        res["dashboard_duplicates_ignored_delta"] = (m1["counters"].get("events_duplicate_ignored", 0)
                                                     - m0["counters"].get("events_duplicate_ignored", 0))
    except KeyError:
        pass
    px.stop()
    c.write_json(out / name / "result.json", res)
    return res


def scen_E(out):
    return [proxy_scenario("E1_network_timeout", out, "hang"),
            proxy_scenario("E2_ack_loss", out, "ack_loss", length=15)]


def scen_F(out):
    return proxy_scenario("F_http_503", out, "status:503")


def scen_G(out):
    e = c.env()
    sd = out / "G1_wrong_token_fixed_live"
    sd.mkdir(parents=True, exist_ok=True)

    def fault(g, tl):
        n = {"t_fault": 0.0}
        time.sleep(15)
        (g.outbox / "detector.env").write_text(f"INGEST_TOKEN={e['INGEST_TOKEN']}\n", encoding="utf-8")
        n["t_clear"] = tl.now()                            # token dosyası düzeltildi (yeniden başlatma yok)
        return n
    r1 = gen_scenario("G1_wrong_token_fixed_live", out, duration=50, fault=fault,
                      gen_kw={"token": "YANLIS-token-" + "x" * 8})
    r2 = proxy_scenario("G2_http_403", out, "status:403", length=15)
    return [r1, r2]


def scen_H(out):
    sd = out / "H_sqlite_locked"
    sd.mkdir(parents=True, exist_ok=True)
    cam = "relfh"
    c.reset_data()
    g = Gen(sd, cam, url=c.base_url(), duration=30, tag="gen1")
    time.sleep(5)
    g2 = Gen(sd, cam, url=c.base_url(), duration=10, tag="gen2_same_camera")
    rc2 = g2.wait(60)
    g.wait(120)
    log2 = (sd / "gen2_same_camera.log").read_text(encoding="utf-8", errors="replace")
    res = {"scenario": "H_sqlite_locked", "second_process_returncode": rc2,
           "second_process_locked": "OutboxLocked" in log2 or "başka bir süreç" in log2,
           "first_final": g.final(), "verify": verify(sd, cam)}
    c.write_json(sd / "result.json", res)
    return res


def scen_I(out):
    return proxy_scenario("I_outbox_capacity", out, "status:503", start=5, length=45, duration=70,
                          gen_kw={"extra": {"OUTBOX_MAX_ITEMS": "40"}, "persons": 4, "period": 4.0})


def scen_J(out):
    e = c.env()
    url = c.base_url()
    hdr = {"X-Token": e["INGEST_TOKEN"], "Content-Type": "application/json"}
    c.reset_data()
    m0 = ops_metrics()
    import uuid
    good = {"type": "start", "pid": 1, "t": 1.0, "missing": "Baretsiz", "helmet_rate": 0, "vest_rate": 100,
            "box": [1, 2, 30, 40], "event_id": str(uuid.uuid4()), "run_id": str(uuid.uuid4()), "camera_id": "relfj",
            "incident_id": str(uuid.uuid4()), "occurred_at": c.utc(), "missing_helmet": True, "missing_vest": False}
    cases = {
        "invalid_json": (b"{bozuk", hdr),
        "events_not_list": (json.dumps({"cam": "relfj", "events": "x"}).encode(), hdr),
        "wrong_types": (json.dumps({"cam": "relfj", "events": [dict(good, pid="bir")]}).encode(), hdr),
        "bad_camera_id": (json.dumps({"cam": "relfj", "events": [dict(good, camera_id="../etc")]}).encode(), hdr),
        "bad_snapshot_b64": (json.dumps({"cam": "relfj", "events": [dict(good, snapshot_b64="!!!")]}).encode(), hdr),
        "too_many_events": (json.dumps({"cam": "relfj", "events": [dict(good, event_id=str(uuid.uuid4()))
                                                                     for _ in range(201)]}).encode(), hdr),
        "oversize_body": (b'{"cam":"relfj","events":[' + b" " * (9 * 1024 * 1024) + b"]}", hdr),
        "no_token": (json.dumps({"cam": "relfj", "events": [good]}).encode(), {"Content-Type": "application/json"}),
        "valid_control": (json.dumps({"cam": "relfj", "events": [good]}).encode(), hdr),
    }
    res = {}
    for k, (body, h) in cases.items():
        try:
            r = requests.post(url + "/ingest/events", data=body, headers=h, timeout=30)
            res[k] = r.status_code
        except requests.RequestException as exc:
            res[k] = type(exc).__name__
    res["healthz_after"] = probe_status("/healthz")
    res["db_rows_relfj"] = int(c.psql("SELECT count(*) FROM violation_events WHERE camera_id = 'relfj'"))
    m1 = ops_metrics()
    try:
        res["tracked_status_delta"] = {k: m1["http"]["tracked_status"][k] - m0["http"]["tracked_status"].get(k, 0)
                                       for k in m1["http"]["tracked_status"]}
    except KeyError:
        pass
    out_j = out / "J_bad_payloads"
    out_j.mkdir(parents=True, exist_ok=True)
    res = {"scenario": "J_bad_payloads", "status_codes": res}
    c.write_json(out_j / "result.json", res)
    return res


def scen_K2(out):
    """Kare akmayan bir anda (kamera yok) istemci WebSocket'i kapatır: sunucu döngüsü kapanmalı (active -> 0).
    FAZ 9 öncesi kodda döngü yeni kare gelene kadar sürerdi (15 sn'de bir oturum sorgusu)."""
    c.reset_data()
    sess = c.Session()
    m = lambda: sess.get("/api/ops/metrics").json()["websocket"]   # noqa: E731
    before = m()
    probe = WSProbe(c.base_url().replace("http", "ws") + "/ws", lambda: sess.cookie, c.base_url())
    probe.start()
    t = time.monotonic()
    while not probe.stats["connected"] and time.monotonic() - t < 10:
        time.sleep(0.1)
    during = m()
    t_close = time.monotonic()
    probe.close_now()
    closed_after = None
    while time.monotonic() - t_close < 20:
        if m()["active"] <= before["active"]:
            closed_after = round(time.monotonic() - t_close, 2)
            break
        time.sleep(0.2)
    res = {"scenario": "K2_ws_client_close_idle", "active_before": before["active"],
           "active_while_connected": during["active"], "server_closed_after_sec": closed_after, "final": m()}
    (out / "K2_ws_idle_close").mkdir(parents=True, exist_ok=True)
    c.write_json(out / "K2_ws_idle_close" / "result.json", res)
    return res


# ---------------------------------------------------------------- gerçek dedektör senaryoları (GPU)
def _health(outbox: Path, cam: str) -> dict:
    try:
        return json.loads((outbox / f"{cam}.health.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _dash_cam(sess, cam):
    try:
        st = sess.get("/api/state").json()["cameras"]
        cm = next((x for x in st if x["id"] == cam), None)
        if cm is None:
            return None
        h = cm["meta"].get("health") or {}
        return {"status": cm["meta"]["status"], "flags": h.get("flags") or [], "people": cm["summary"].get("people")}
    except Exception:                                      # noqa: BLE001
        return None


DOWN_FLAGS = {"camera_disconnected", "no_signal", "frozen", "stalled"}


def stream_scenario(out, name, mode, *, outage=25, extra_env=None):
    """Gerçek dedektör + ağ kamerası benzeticisi. mode='cut' (bağlantı koptu) veya 'freeze' (soket açık, veri yok)."""
    from camsim import CameraSim
    sd = out / name
    sd.mkdir(parents=True, exist_ok=True)
    cam = {"D1_stream_cut": "relfd1", "D2_stream_freeze": "relfd2", "D3_stream_freeze_rw_timeout": "relfd3"}[name]
    port = {"relfd1": 9131, "relfd2": 9132, "relfd3": 9133}[cam]
    outbox = sd / "outbox"
    c.reset_data()
    sim = CameraSim(c.VIDEOS["V1"], port).start()
    det = c.start_detector([f"[{cam}]Ağ akışı={sim.url}"], outbox, sd / "detector.log", stride=2, extra=extra_env)
    timeline, t0 = [], time.monotonic()
    sess = c.Session()

    def snap(tag):
        h = _health(outbox, cam)
        timeline.append({"t": round(time.monotonic() - t0, 2), "tag": tag, "state": h.get("state"),
                         "flags": h.get("flags"), "run": (h.get("run") or {}).get("run_id"),
                         "health_updated": h.get("updated_at"),
                         "frames": (h.get("counters") or {}).get("frames_processed"),
                         "reconnects": (h.get("counters") or {}).get("reconnect_attempts"),
                         "open_failures": (h.get("counters") or {}).get("source_open_failures"),
                         "no_signal_frames": (h.get("counters") or {}).get("no_signal_frames"),
                         "dash": _dash_cam(sess, cam)})
    res = {"scenario": name, "mode": mode, "source": "TCP/MPEG-TS kamera benzeticisi (gerçek RTSP kamera: SKIP)",
           "extra_env": extra_env or {}}
    try:
        while time.monotonic() - t0 < 120 and _health(outbox, cam).get("counters", {}).get("frames_processed", 0) < 50:
            time.sleep(1)
        snap("connected")
        res["accepts_before"] = sim.accepts
        t_cut = time.monotonic()
        (sim.cut if mode == "cut" else sim.freeze)()
        res["fault_at_utc"] = c.utc()
        det_down = dash_down = None
        while time.monotonic() - t_cut < outage:
            snap("fault")
            last = timeline[-1]
            if det_down is None and set(last["flags"] or []) & DOWN_FLAGS:
                det_down = round(time.monotonic() - t_cut, 2)
            d = last["dash"]
            if dash_down is None and d and (d["status"] == "waiting_for_detector" or set(d["flags"]) & DOWN_FLAGS):
                dash_down = {"sec": round(time.monotonic() - t_cut, 2), **d}
            time.sleep(1)
        res["detector_flagged_down_sec"] = det_down
        res["operator_view_down"] = dash_down
        run_before = timeline[-1]["run"]
        frames_before = timeline[-1]["frames"] or 0
        t_up = time.monotonic()
        (sim.restore if mode == "cut" else sim.unfreeze)()
        rec = None
        while time.monotonic() - t_up < 90:
            snap("restored")
            last = timeline[-1]
            if last["state"] == "connected" and (last["frames"] or 0) > frames_before + 10                     and not set(last["flags"] or []) & DOWN_FLAGS:
                rec = round(time.monotonic() - t_up, 2)
                res["new_run_after_restore"] = last["run"] != run_before
                break
            time.sleep(1)
        res["recovered_after_restore_sec"] = rec
        time.sleep(4)
        snap("end")
    finally:
        res["stop"] = c.stop_detector(det)
        sim.cut()
    runs = c.psql(f"SELECT status, loop_no, report_metadata IS NOT NULL FROM detector_runs WHERE camera_id = '{cam}' "
                  f"ORDER BY started_at")
    res["runs"] = [l.split("	") for l in runs.splitlines()]
    res["log_errors"] = [l for l in (sd / "detector.log").read_text(encoding="utf-8", errors="replace").splitlines()
                         if "HATA" in l][:10]
    res["timeline"] = timeline
    c.write_json(sd / "result.json", res)
    return res


def scen_D(out):
    return [stream_scenario(out, "D1_stream_cut", "cut"),
            stream_scenario(out, "D2_stream_freeze", "freeze", outage=45),
            stream_scenario(out, "D3_stream_freeze_rw_timeout", "freeze", outage=45,
                            extra_env={"OPENCV_FFMPEG_CAPTURE_OPTIONS": "rw_timeout;5000000"})]


def scen_L(out):
    """Gerçek dedektör (V2, stride 2) çalışırken önce dashboard, sonra PostgreSQL container'ı yeniden başlatılır."""
    sd = out / "L_container_restart_real_detector"
    sd.mkdir(parents=True, exist_ok=True)
    cam = "relfl"
    outbox = sd / "outbox"
    c.reset_data()
    det = c.start_detector([f"[{cam}]Giriş=" + str(c.VIDEOS["V2"])], outbox, sd / "detector.log", stride=2)
    res = {"scenario": "L_container_restart_real_detector"}
    try:
        t0 = time.monotonic()
        while time.monotonic() - t0 < 120 and _health(outbox, cam).get("counters", {}).get("frames_processed", 0) < 50:
            time.sleep(1)
        time.sleep(5)
        t = time.monotonic()
        subprocess.run(["docker", "restart", c.container("dashboard")], capture_output=True, timeout=120)
        res["dashboard_ready_sec"] = round(time.monotonic() - t + wait_ready(), 2)
        time.sleep(10)
        t = time.monotonic()
        subprocess.run(["docker", "restart", c.container("db")], capture_output=True, timeout=120)
        res["db_restart_cmd_sec"] = round(time.monotonic() - t, 2)
        res["ready_after_db_restart_sec"] = wait_ready()
        time.sleep(30)                                    # V2 24 sn: en az bir döngü + olaylar
        res["health_end"] = {k: _health(outbox, cam).get(k) for k in ("state", "flags", "counters")}
    finally:
        res["stop"] = c.stop_detector(det)
    import stress
    ob = stress.outbox_final(outbox)
    ids = {i for v in ob.values() for i in v.pop("event_ids")}
    db_ids = set(c.psql(f"SELECT event_id FROM violation_events WHERE camera_id = '{cam}'").split())
    res.update({"outbox_final": ob, "outbox_event_ids": len(ids), "db_event_ids": len(db_ids),
                "missing_in_db": len(ids - db_ids), "duplicates": int(c.psql(
                    f"SELECT count(*) - count(DISTINCT event_id) FROM violation_events WHERE camera_id = '{cam}'")),
                "runs": c.psql(f"SELECT status, count(*) FROM detector_runs WHERE camera_id = '{cam}' GROUP BY 1")})
    res["log"] = stress.log_summary(sd / "detector.log")
    c.write_json(sd / "result.json", res)
    return res


def scen_M(out):
    """Gerçek dedektör zorla öldürülür (taskkill /T /F; rapor gönderilemez), aynı outbox ile yeniden başlatılır."""
    sd = out / "M_real_detector_kill"
    sd.mkdir(parents=True, exist_ok=True)
    cam = "relfm"
    outbox = sd / "outbox"
    c.reset_data()
    px = FaultProxy(c.base_url()).start()
    det = c.start_detector([f"[{cam}]Giriş=" + str(c.VIDEOS["V2"])], outbox, sd / "detector1.log", stride=2,
                           url=px.url)
    res = {"scenario": "M_real_detector_kill"}
    t0 = time.monotonic()
    while time.monotonic() - t0 < 120 and _health(outbox, cam).get("counters", {}).get("frames_processed", 0) < 30:
        time.sleep(1)
    px.set("status:503")                                  # teslim edilemesin: öldürmede outbox'ta bekleyen olsun
    time.sleep(25)
    res["health_before_kill"] = {k: _health(outbox, cam).get(k) for k in ("state", "counters")}
    c.kill_tree(det.pid)
    det.wait(10)
    res["killed_utc"] = c.utc()
    px.set("pass")
    import stress
    res["outbox_after_kill"] = {k: {kk: vv for kk, vv in v.items() if kk != "event_ids"}
                                for k, v in stress.outbox_final(outbox).items()}
    det2 = c.start_detector([f"[{cam}]Giriş=" + str(c.VIDEOS["V2"])], outbox, sd / "detector2.log", stride=2,
                            url=px.url)
    time.sleep(40)
    res["stop2"] = c.stop_detector(det2)
    px.stop()
    ob = stress.outbox_final(outbox)
    ids = {i for v in ob.values() for i in v.pop("event_ids")}
    db_ids = set(c.psql(f"SELECT event_id FROM violation_events WHERE camera_id = '{cam}'").split())
    res.update({"outbox_final": ob, "outbox_event_ids": len(ids), "db_event_ids": len(db_ids),
                "missing_in_db": len(ids - db_ids), "duplicates": int(c.psql(
                    f"SELECT count(*) - count(DISTINCT event_id) FROM violation_events WHERE camera_id = '{cam}'")),
                "runs": [l.split("\t") for l in c.psql(
                    f"SELECT id, status, report_metadata IS NOT NULL FROM detector_runs WHERE camera_id = '{cam}' "
                    f"ORDER BY started_at").splitlines()],
                "recovery_log": [l for l in (sd / "detector2.log").read_text(encoding="utf-8", errors="replace")
                                 .splitlines() if "kurtarma" in l or "kapandı" in l]})
    c.write_json(sd / "result.json", res)
    return res


ALL = {"A": scen_A, "B": scen_B_K, "K2": scen_K2, "C": scen_C, "E": scen_E, "F": scen_F, "G": scen_G, "H": scen_H, "I": scen_I,
       "J": scen_J, "D": scen_D, "L": scen_L, "M": scen_M}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=",".join(ALL))
    a = ap.parse_args(argv)
    out = c.outdir("failures")
    results = {}
    for k in a.only.split(","):
        print(f"[{c.utc()}] senaryo {k}…", flush=True)
        try:
            results[k] = ALL[k](out)
        except Exception as exc:                          # noqa: BLE001
            import traceback
            results[k] = {"error": repr(exc)[:500], "trace": traceback.format_exc()[-2000:]}
            try:                                          # yığını bir sonraki senaryo için ayağa kaldır
                subprocess.run(["docker", "start", c.container("db"), c.container("dashboard")], capture_output=True)
                wait_ready()
            except Exception:                             # noqa: BLE001
                pass
        c.write_json(out / "failures.json", results)
        r = results[k]
        brief = r if isinstance(r, list) else [r]
        for x in brief:
            print(json.dumps({"scenario": x.get("scenario"), "verify": x.get("verify"),
                              "recovery": x.get("recovery_sec_after_clear"), "error": x.get("error")},
                             ensure_ascii=False)[:600], flush=True)
    print("çıktı:", out)


if __name__ == "__main__":
    main()
