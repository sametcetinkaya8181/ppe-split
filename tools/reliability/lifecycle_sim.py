"""FAZ 10-A kaynak yaşam döngüsü SİMÜLASYONU (gerçek dedektör + gerçek model + TCP/MPEG-TS kamera benzeticisi +
izole ppe-rel yığını). Gerçek RTSP kamera testi DEĞİLDİR (BLOCKED: kamera yok). Senaryolar:
  R1 geçici kopma (15 sn)          R2 kalıcı kayıp (150 sn; geri çekilme + operatör görünürlüğü)
  R3 donma (40 sn; varsayılan okuma zaman aşımı)   R4 başlangıçta erişilemeyen uç -> 70 sn sonra erişilebilir
Her senaryoda: DB'deki run durumları/bitiş nedenleri, aynı kamera kimliği, yeniden bağlanma süreleri, log'daki
geri çekilme aralıkları, dashboard'un operatöre gösterdiği durum, outbox <-> DB olay eşleşmesi.

  python tools/reliability/lifecycle_sim.py [--only R1,R2,R3,R4]"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as c                                     # noqa: E402
from camsim import CameraSim                           # noqa: E402

DOWN = {"camera_disconnected", "no_signal", "frozen", "stalled"}


def health(outbox: Path, cam: str) -> dict:
    try:
        return json.loads((outbox / f"{cam}.health.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def dash(sess, cam):
    try:
        cm = next((x for x in sess.get("/api/state").json()["cameras"] if x["id"] == cam), None)
    except Exception:                                      # noqa: BLE001
        return None
    if cm is None:
        return None
    h = cm["meta"].get("health") or {}
    return {"status": cm["meta"]["status"], "flags": h.get("flags") or [], "error": cm["meta"].get("error")}


def wait_frames(outbox, cam, n=40, timeout=150):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if (health(outbox, cam).get("counters") or {}).get("frames_processed", 0) >= n:
            return True
        time.sleep(1)
    return False


def backoff_lines(log: Path) -> list:
    out = []
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.search(r"kaynak kullanılamıyor \((\w+)\); ([\d.]+) sn sonra .*deneme (\d+)", line)
        if m:
            out.append({"reason": m.group(1), "wait_sec": float(m.group(2)), "attempt": int(m.group(3))})
    return out


def db_state(cam: str) -> dict:
    runs = [l.split("\t") for l in c.psql(
        f"SELECT status, coalesce(report_metadata->>'end_reason', '-'), loop_no FROM detector_runs "
        f"WHERE camera_id = '{cam}' ORDER BY started_at").splitlines() if l]
    ev = c.psql(f"SELECT count(*), count(DISTINCT event_id) FROM violation_events WHERE camera_id = '{cam}'").split()
    return {"runs": runs, "events": int(ev[0]), "distinct_events": int(ev[1]),
            "camera_rows": c.psql(f"SELECT count(*) FROM cameras WHERE id = '{cam}'")}


def delivery(outbox: Path, cam: str) -> dict:
    import stress
    ob = stress.outbox_final(outbox)
    ids = {i for v in ob.values() for i in v.pop("event_ids")}
    db_ids = set(c.psql(f"SELECT event_id FROM violation_events WHERE camera_id = '{cam}'").split())
    return {"outbox": {k: v for k, v in ob.items()}, "outbox_event_ids": len(ids), "db_event_ids": len(db_ids),
            "missing_in_db": len(ids - db_ids)}


def scenario(out: Path, name: str, cam: str, port: int, plan) -> dict:
    sd = out / name
    sd.mkdir(parents=True, exist_ok=True)
    outbox = sd / "outbox"
    c.reset_data()
    sim = CameraSim(c.VIDEOS["V2"], port)
    res = {"scenario": name, "simulation": "TCP/MPEG-TS kamera benzeticisi — gerçek RTSP DEĞİL", "started_utc": c.utc()}
    timeline, t0 = [], time.monotonic()
    sess = c.Session()

    def snap(tag):
        h = health(outbox, cam)
        timeline.append({"t": round(time.monotonic() - t0, 1), "tag": tag, "state": h.get("state"),
                         "flags": h.get("flags"), "run": ((h.get("run") or {}).get("run_id") or "")[:8],
                         "frames": (h.get("counters") or {}).get("frames_processed"),
                         "source_lost": (h.get("counters") or {}).get("source_lost"),
                         "reconnects": (h.get("counters") or {}).get("reconnect_attempts"), "dash": dash(sess, cam)})
        return timeline[-1]
    det = None
    try:
        det, extra = plan(sim, outbox, sd, cam, snap, res)
        res.update(extra)
    finally:
        if det is not None:
            res["stop"] = c.stop_detector(det)
        sim.cut()
    res["ended_utc"] = c.utc()
    res["db"] = db_state(cam)
    res["delivery"] = delivery(outbox, cam)
    res["backoff"] = backoff_lines(sd / "detector.log")
    res["timeline"] = timeline
    c.write_json(sd / "result.json", res)
    return res


def _start(sim_url, outbox, sd, cam):
    return c.start_detector([f"[{cam}]Ağ kamerası={sim_url}"], outbox, sd / "detector.log", stride=2)


def _until(snap, tag, cond, timeout):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        s = snap(tag)
        if cond(s):
            return round(time.monotonic() - t0, 1)
        time.sleep(1)
    return None


def plan_R1(sim, outbox, sd, cam, snap, res):
    sim.start()
    det = _start(sim.url, outbox, sd, cam)
    wait_frames(outbox, cam, 200)                          # V2'de ~4.7 sn'de olay (FAZ 8 E-01) -> olay üretilsin
    snap("connected")
    sim.cut()
    down = _until(snap, "cut", lambda s: bool(set(s["flags"] or []) & DOWN), 30)
    op = _until(snap, "cut", lambda s: s["dash"] and (s["dash"]["status"] in ("reconnecting", "waiting_for_detector")
                                                      or set(s["dash"]["flags"]) & DOWN), 30)
    time.sleep(max(0, 15 - (down or 0)))
    run_before = snap("before_restore")["run"]
    sim.start()
    rec = _until(snap, "restored", lambda s: s["state"] == "connected" and s["run"] != run_before
                 and not set(s["flags"] or []) & DOWN, 120)
    wait_frames(outbox, cam, (snap("x")["frames"] or 0) + 60)
    return det, {"detector_flagged_sec": down, "operator_saw_down_sec": op, "reconnected_after_restore_sec": rec}


def plan_R2(sim, outbox, sd, cam, snap, res):
    sim.start()
    det = _start(sim.url, outbox, sd, cam)
    wait_frames(outbox, cam, 60)
    sim.cut()
    t_cut = time.monotonic()
    views = []
    while time.monotonic() - t_cut < 150:
        s = snap("lost")
        if s["dash"]:
            views.append((round(time.monotonic() - t_cut), s["dash"]["status"], tuple(s["dash"]["flags"])))
        time.sleep(3)
    return det, {"operator_view_samples": sorted(set(views))[:6] + sorted(set(views))[-3:],
                 "loss_window_sec": 150}


def plan_R3(sim, outbox, sd, cam, snap, res):
    sim.start()
    det = _start(sim.url, outbox, sd, cam)
    wait_frames(outbox, cam, 60)
    sim.freeze()
    stalled = _until(snap, "frozen", lambda s: "stalled" in (s["flags"] or []), 30)
    lost = _until(snap, "frozen", lambda s: (s["source_lost"] or 0) >= 1, 40)
    time.sleep(max(0, 40 - (stalled or 0) - (lost or 0)))
    sim.unfreeze()
    rec = _until(snap, "unfrozen", lambda s: s["state"] == "connected" and not set(s["flags"] or []) & DOWN, 120)
    return det, {"stalled_flag_sec": stalled, "run_ended_after_stall_sec": lost, "recovered_after_unfreeze_sec": rec}


def plan_R4(sim, outbox, sd, cam, snap, res):
    det = _start(sim.url, outbox, sd, cam)                 # uçta dinleyen yok (yanlış uç / kamera kapalı)
    time.sleep(70)
    s = snap("unreachable")
    res["operator_view_while_unreachable"] = s["dash"]
    sim.start()                                            # kamera erişilebilir oldu
    rec = _until(snap, "reachable", lambda x: x["state"] == "connected" and (x["frames"] or 0) > 5, 120)
    wait_frames(outbox, cam, (snap("y")["frames"] or 0) + 30)
    return det, {"connected_after_reachable_sec": rec}


PLANS = {"R1": ("R1_transient_cut", "relr1", 9141, plan_R1), "R2": ("R2_permanent_loss", "relr2", 9142, plan_R2),
         "R3": ("R3_freeze_default_timeout", "relr3", 9143, plan_R3),
         "R4": ("R4_unreachable_then_reachable", "relr4", 9144, plan_R4)}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="R1,R2,R3,R4")
    a = ap.parse_args(argv)
    out = c.outdir("lifecycle_sim")
    results = {}
    for k in a.only.split(","):
        name, cam, port, plan = PLANS[k]
        print(f"[{c.utc()}] {name}…", flush=True)
        try:
            results[k] = scenario(out, name, cam, port, plan)
        except Exception as exc:                          # noqa: BLE001
            import traceback
            results[k] = {"error": repr(exc)[:400], "trace": traceback.format_exc()[-1500:]}
        c.write_json(out / "lifecycle.json", results)
        r = results[k]
        print(json.dumps({x: r.get(x) for x in ("scenario", "db", "backoff", "error")}, ensure_ascii=False)[:900],
              flush=True)
    print("çıktı:", out)


if __name__ == "__main__":
    main()
