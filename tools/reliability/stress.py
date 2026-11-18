"""Çoklu kamera yük testi (gerçek detector.py, gerçek YOLO/ByteTrack/PPEMonitor, gerçek outbox, izole dashboard +
PostgreSQL 17). Her senaryo: N kamera x VID_STRIDE; kaynaklar gerçek zaman hızında oynatılan yerel videolar (dosya
kaynağı canlı kamerayı taklit eder: kaynak zaman çizelgesinin gerisinde kalma = lag). Aynı videonun tekrar
kullanıldığı kameralar sonuçta 'reused' olarak işaretlenir.

  python tools/reliability/stress.py --matrix 1:2,2:2,3:2 --duration 120
  python tools/reliability/stress.py --matrix 3:2 --duration 3600 --name soak --interval 10   (soak)
"""
import argparse
import json
import sys
import time
from pathlib import Path

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as c                                     # noqa: E402
from sampler import Sampler, summarize, write_csv      # noqa: E402

SETS = {"hd": ["V1", "V2"], "mixed": ["V1", "V2", "V3"]}
MEM_GUARD_BYTES = 250 * 2**20                             # 10 sn boyunca bundan az boş RAM: senaryo durdurulur


def sources_for(n: int, set_name: str) -> list[tuple[str, str, str, bool]]:
    pool = SETS[set_name]
    out = []
    for i in range(n):
        v = pool[i % len(pool)]
        out.append((f"relc{i + 1}", v, str(c.VIDEOS[v]), i >= len(pool)))
    return out


def wait_connected(outbox: Path, cams: list[str], timeout=180) -> float:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        ok = 0
        for cid in cams:
            f = outbox / f"{cid}.health.json"
            try:
                h = json.loads(f.read_text(encoding="utf-8"))
                ok += h["state"] == "connected" and h["counters"]["frames_processed"] > 20
            except (OSError, ValueError, KeyError):
                pass
        if ok == len(cams):
            return time.monotonic() - t0
        time.sleep(1)
    raise TimeoutError("kameralar bağlanmadı")


def final_db(cams: list[str]) -> dict:
    ids = ",".join(f"'{x}'" for x in cams)
    q = (f"SELECT count(*), count(DISTINCT event_id) FROM violation_events WHERE camera_id IN ({ids}); "
         f"SELECT status, count(*) FROM detector_runs WHERE camera_id IN ({ids}) GROUP BY status ORDER BY 1; "
         f"SELECT count(*) FROM incidents WHERE camera_id IN ({ids});")
    lines = c.psql(q).splitlines()
    ev, distinct = map(int, lines[0].split("\t"))
    runs = {l.split("\t")[0]: int(l.split("\t")[1]) for l in lines[1:-1]}
    return {"events": ev, "distinct_event_ids": distinct, "runs_by_status": runs, "incidents": int(lines[-1])}


def outbox_final(outbox: Path) -> dict:
    sys.path.insert(0, str(c.DETECTOR))
    import outbox as ob
    res = {}
    for f in sorted(outbox.glob("*.sqlite3")):
        o = ob.Outbox(f, f.stem, lock_timeout=2)
        try:
            m = o.metrics()
            ids = [r[0] for r in o.db.execute("SELECT msg_id FROM outbox WHERE kind = 'event'")]
            res[f.stem] = {"pending": m["pending_count"], "dead": m["dead_letter_count"],
                           "quarantine": m["quarantine_count"], "wal_bytes": m["wal_bytes"],
                           "db_bytes": m["db_bytes"], "event_ids": ids}
        finally:
            o.close()
    return res


def log_summary(log: Path) -> dict:
    text = log.read_text(encoding="utf-8", errors="replace")
    return {"closed_lines": [l for l in text.splitlines() if "gönderici kapandı" in l],
            "errors": [l for l in text.splitlines() if "HATA" in l or "Traceback" in l][:20],
            "outbox_warnings": [l for l in text.splitlines() if "OUTBOX UYARISI" in l][:20],
            "starts": text.count(" START "), "ends": text.count(" END ")}


def run_scenario(n: int, stride: int, duration: float, set_name: str, out: Path, interval: float) -> dict:
    srcs = sources_for(n, set_name)
    cams = [s[0] for s in srcs]
    sd = out / f"cams{n}_stride{stride}"
    res0 = {"mem_available_mb_at_start": round(psutil.virtual_memory().available / 2**20),
            "swap_used_mb_at_start": round(psutil.swap_memory().used / 2**20)}
    sd.mkdir(parents=True, exist_ok=True)
    outbox = sd / "outbox"
    c.reset_data()
    log = sd / "detector.log"
    started = c.utc()
    p = c.start_detector([f"[{cid}]{v}={path}" for cid, v, path, _ in srcs], outbox, log, stride=stride)
    sampler = None
    try:
        warm = wait_connected(outbox, cams)
        sampler = Sampler(sd, outbox, interval=interval, root_pid=p.pid)
        sampler.start()
        t0 = time.monotonic()
        low = 0
        guard = None
        while time.monotonic() - t0 < duration:
            if p.poll() is not None:
                raise RuntimeError(f"dedektör beklenmedik biçimde çıktı ({p.returncode})")
            vm, sw = psutil.virtual_memory(), psutil.swap_memory()
            low = low + 1 if (vm.available < MEM_GUARD_BYTES or sw.percent > 90) else 0
            if low >= 10:                                 # kullanıcının makinesi aşırı sayfalamaya girmesin
                guard = {"aborted": "memory_guard", "available_mb": round(vm.available / 2**20),
                         "swap_percent": sw.percent, "at_sec": round(time.monotonic() - t0, 1)}
                break
            time.sleep(1)
        measured = round(time.monotonic() - t0, 1)
    finally:
        if sampler:
            sampler.stop()
        stop = c.stop_detector(p)
    ended = c.utc()
    rows = sampler.rows if sampler else []
    write_csv(sd, rows)
    ob = outbox_final(outbox)
    db = final_db(cams)
    sent_ids = {i for v in ob.values() for i in v.pop("event_ids")}
    db_ids = set(c.psql("SELECT event_id FROM violation_events WHERE camera_id IN (%s)" %
                        ",".join(f"'{x}'" for x in cams)).split()) if db["events"] else set()
    res = {"cameras": n, "vid_stride": stride, "sources": [{"camera_id": cid, "video": v, "reused_video": r}
                                                            for cid, v, _, r in srcs],
           "started_utc": started, "ended_utc": ended, "warmup_sec": round(warm, 1), "measured_sec": measured,
           "memory_guard": guard, **res0,
           "stop": stop, "summary": summarize(rows, skip_sec=5), "outbox_final": ob, "db": db,
           "delivery": {"outbox_event_ids": len(sent_ids), "db_event_ids": len(db_ids),
                        "missing_in_db": len(sent_ids - db_ids), "unexpected_in_db": len(db_ids - sent_ids)},
           "log": log_summary(log)}
    c.write_json(sd / "result.json", res)
    return res


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--matrix", default="1:2,2:2,3:2")
    ap.add_argument("--duration", type=float, default=120)
    ap.add_argument("--set", default="hd", choices=sorted(SETS))
    ap.add_argument("--interval", type=float, default=5.0)
    ap.add_argument("--name", default="stress")
    ap.add_argument("--no-up", action="store_true")
    a = ap.parse_args(argv)
    if not a.no_up:
        c.up(build=False)
    out = c.outdir(a.name)
    results = []
    for item in a.matrix.split(","):
        n, stride = map(int, item.split(":"))
        print(f"[{c.utc()}] senaryo {n} kamera, stride {stride} ({a.duration:.0f} sn)…", flush=True)
        try:
            r = run_scenario(n, stride, a.duration, a.set, out, a.interval)
        except Exception as exc:                          # noqa: BLE001
            r = {"cameras": n, "vid_stride": stride, "error": repr(exc)[:500]}
        results.append(r)
        c.write_json(out / "matrix.json", results)
        s = r.get("summary", {}).get("cameras", {})
        print(json.dumps({k: {"fps": v.get("proc_fps_p50"), "lag_p95": v.get("lag_sec_p95"),
                              "inf_p95": v.get("inf_ms_p95_median")} for k, v in s.items()}), flush=True)
        time.sleep(10)
    print("çıktı:", out)


if __name__ == "__main__":
    main()
