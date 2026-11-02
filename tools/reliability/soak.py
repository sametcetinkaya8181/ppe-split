"""Uzun süreli soak (FAZ 10-A): gerçek detector.py, 4 kamera (3 yerel dosya + 1 TCP/MPEG-TS ağ kamerası benzeticisi),
izole ppe-rel yığını. Ağ kamerasına periyodik arıza: her `--fault-every` dakikada `--cut-sec` kopma, yarı periyotta
`--freeze-sec` donma. Zaman serisi sampler ile; sonda teslim bütünlüğü (gönderici sayaçları <-> PostgreSQL), run
durumları, yeniden bağlanma sayaçları. Gerçek RTSP kamera DEĞİLDİR.

  python tools/reliability/soak.py --hours 4
  python tools/reliability/soak.py --hours 24        (hazır; 24 saat gerçekten koşmadan sonuç yazılmaz)"""
import argparse
import json
import re
import sys
import threading
import time
from pathlib import Path

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as c                                     # noqa: E402
import stress                                          # noqa: E402
from camsim import CameraSim                           # noqa: E402
from sampler import Sampler, summarize, write_csv      # noqa: E402

CAMS = [("relc1", "V1"), ("relc2", "V2"), ("relc3", "V1")]
NET = ("relnet", "V2", 9150)


class FaultSchedule(threading.Thread):
    def __init__(self, sim: CameraSim, every_min: float, cut_sec: float, freeze_sec: float, log: list):
        super().__init__(daemon=True)
        self.sim, self.every, self.cut, self.freeze, self.log = sim, every_min * 60, cut_sec, freeze_sec, log
        self.stop_flag = threading.Event()

    def _do(self, kind, sec, on, off):
        self.log.append({"utc": c.utc(), "fault": kind, "sec": sec})
        on()
        if self.stop_flag.wait(sec):
            off()
            return
        off()
        self.log.append({"utc": c.utc(), "fault": f"{kind}_cleared"})

    def run(self):
        half = self.every / 2
        while not self.stop_flag.wait(half):
            self._do("cut", self.cut, self.sim.cut, self.sim.restore)
            if self.stop_flag.wait(max(0.0, half - self.cut)):
                return
            self._do("freeze", self.freeze, self.sim.freeze, self.sim.unfreeze)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=4)
    ap.add_argument("--interval", type=float, default=30)
    ap.add_argument("--fault-every", type=float, default=40, help="dakika")
    ap.add_argument("--cut-sec", type=float, default=60)
    ap.add_argument("--freeze-sec", type=float, default=45)
    ap.add_argument("--stride", type=int, default=2)
    a = ap.parse_args(argv)
    out = c.outdir(f"soak{a.hours:g}h")
    sd = out / f"cams4_stride{a.stride}"
    sd.mkdir(parents=True, exist_ok=True)
    outbox = sd / "outbox"
    c.reset_data()
    sim = CameraSim(c.VIDEOS[NET[1]], NET[2]).start()
    sources = [f"[{cid}]{v}={c.VIDEOS[v]}" for cid, v in CAMS] + [f"[{NET[0]}]Ağ kamerası={sim.url}"]
    cams = [x[0] for x in CAMS] + [NET[0]]
    res = {"hours_planned": a.hours, "cameras": len(cams), "vid_stride": a.stride, "sources": [
        {"camera_id": cid, "video": v, "kind": "file", "reused_video": i >= 2} for i, (cid, v) in enumerate(CAMS)] + [
        {"camera_id": NET[0], "video": NET[1], "kind": "tcp_mpegts_simulator", "reused_video": True}],
        "fault_plan": {"every_min": a.fault_every, "cut_sec": a.cut_sec, "freeze_sec": a.freeze_sec},
        "mem_available_mb_at_start": round(psutil.virtual_memory().available / 2**20),
        "swap_used_mb_at_start": round(psutil.swap_memory().used / 2**20), "started_utc": c.utc()}
    p = c.start_detector(sources, outbox, sd / "detector.log", stride=a.stride)
    faults, sampler, fs = [], None, None
    try:
        res["warmup_sec"] = round(stress.wait_connected(outbox, cams, timeout=300), 1)
        sampler = Sampler(sd, outbox, interval=a.interval, root_pid=p.pid)
        sampler.start()
        fs = FaultSchedule(sim, a.fault_every, a.cut_sec, a.freeze_sec, faults)
        fs.start()
        res["measure_start_utc"] = c.utc()
        t0, low = time.monotonic(), 0
        while time.monotonic() - t0 < a.hours * 3600:
            if p.poll() is not None:
                res["detector_exit"] = p.returncode
                break
            vm, sw = psutil.virtual_memory(), psutil.swap_memory()
            low = low + 1 if (vm.available < stress.MEM_GUARD_BYTES or sw.percent > 90) else 0
            if low >= 10:
                res["memory_guard"] = {"available_mb": round(vm.available / 2**20), "swap_percent": sw.percent}
                break
            time.sleep(5)
        res["measured_sec"] = round(time.monotonic() - t0, 1)
        res["measure_end_utc"] = c.utc()
    finally:
        if fs:
            fs.stop_flag.set()
        if sampler:
            sampler.stop()
        res["stop"] = c.stop_detector(p, timeout=60)
        sim.cut()
    res["ended_utc"] = c.utc()
    rows = sampler.rows if sampler else []
    write_csv(sd, rows)
    res["faults"] = faults
    res["summary"] = summarize(rows, skip_sec=60)
    ob = stress.outbox_final(outbox)
    for v in ob.values():
        v["event_ids_in_retention"] = len(v.pop("event_ids"))
    res["outbox_final"] = ob
    log = (sd / "detector.log").read_text(encoding="utf-8", errors="replace")
    sent = sum(int(m) for m in re.findall(r"gönderici kapandı: (\d+) olay", log))
    ids = ",".join(f"'{x}'" for x in cams)
    db_ev, db_distinct = map(int, c.psql(f"SELECT count(*), count(DISTINCT event_id) FROM violation_events "
                                         f"WHERE camera_id IN ({ids})").split())
    runs = {}
    for line in c.psql(f"SELECT camera_id, status, coalesce(report_metadata->>'end_reason','-'), count(*) "
                       f"FROM detector_runs WHERE camera_id IN ({ids}) GROUP BY 1, 2, 3 ORDER BY 1, 2").splitlines():
        cam, st, er, n = line.split("\t")
        runs.setdefault(cam, []).append([st, er, int(n)])
    res["delivery"] = {"events_sent_by_detector": sent, "db_events": db_ev, "db_distinct_event_ids": db_distinct,
                       "lost": sent - db_ev, "duplicates": db_ev - db_distinct,
                       "pending_after_stop": sum(v["pending"] for v in ob.values()),
                       "dead_after_stop": sum(v["dead"] for v in ob.values())}
    res["runs_by_camera"] = runs
    res["log"] = stress.log_summary(sd / "detector.log")
    c.write_json(sd / "result.json", res)
    print(json.dumps({k: res.get(k) for k in ("started_utc", "measure_start_utc", "measure_end_utc", "measured_sec",
                                              "delivery", "memory_guard", "detector_exit")}, ensure_ascii=False))
    print("çıktı:", out)


if __name__ == "__main__":
    main()
