"""Zaman serisi örnekleyici (yük ve soak testleri). Her örnek samples.jsonl'e bir satır; sonda samples.csv ve özet.
Kaynaklar: dedektör süreç ağacı (psutil), nvidia-smi, health/status dosyaları (OUTBOX_DIR), izole dashboard
(/api/state gecikmesi + /api/ops/metrics), docker stats, PostgreSQL bağlantı sayısı, WebSocket probu, disk.
Görüntü/gizli bilgi kaydedilmez."""
import csv
import json
import subprocess
import threading
import time
from pathlib import Path

import psutil

import common as c
from wsprobe import WSProbe


def nvidia() -> dict:
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu,"
                            "power.draw,clocks.sm", "--format=csv,noheader,nounits"], capture_output=True, text=True,
                           timeout=10)
        u, used, total, temp, power, sm = [x.strip() for x in r.stdout.strip().splitlines()[0].split(",")]
        f = lambda v: float(v) if v not in ("[N/A]", "N/A", "") else None   # noqa: E731
        return {"gpu_util": f(u), "gpu_mem_used_mb": f(used), "gpu_mem_total_mb": f(total), "gpu_temp_c": f(temp),
                "gpu_power_w": f(power), "gpu_sm_mhz": f(sm)}
    except Exception as exc:                                  # noqa: BLE001
        return {"gpu_error": type(exc).__name__}


def docker_stats() -> dict:
    try:
        r = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{json .}}", c.container("dashboard"),
                            c.container("db")], capture_output=True, text=True, timeout=20)
        out = {}
        for line in r.stdout.strip().splitlines():
            d = json.loads(line)
            key = "dash" if "dashboard" in d["Name"] else "db"
            out[f"{key}_cpu"] = float(d["CPUPerc"].rstrip("%") or 0)
            out[f"{key}_mem_mb"] = _mb(d["MemUsage"].split("/")[0].strip())
            out[f"{key}_pids"] = int(d["PIDs"])
        return out
    except Exception as exc:                                  # noqa: BLE001
        return {"docker_error": type(exc).__name__}


def _mb(s: str) -> float:
    units = {"KiB": 1 / 1024, "MiB": 1, "GiB": 1024, "kB": 1 / 1000, "MB": 1, "GB": 1000, "B": 1 / 2**20}
    for u in sorted(units, key=len, reverse=True):
        if s.endswith(u):
            return round(float(s[:-len(u)]) * units[u], 1)
    return float(s)


_PROCS: dict = {}                                   # pid -> psutil.Process (cpu_percent önceki çağrıya göre ölçer)


def _proc(pid: int) -> psutil.Process:
    p = _PROCS.get(pid)
    if p is None:
        p = _PROCS[pid] = psutil.Process(pid)
        p.cpu_percent(None)
    return p


def proc_tree(root_pid: int | None) -> dict:
    if not root_pid:
        return {}
    try:
        root = _proc(root_pid)
        procs = [root] + [_proc(ch.pid) for ch in root.children(recursive=True)]
    except psutil.Error:
        return {"det_alive": False}
    rss = cpu = handles = threads = 0
    per = []
    for p in procs:
        try:
            with p.oneshot():
                r = p.memory_info().rss / 2**20
                cp = p.cpu_percent(None)
                h = p.num_handles()
                th = p.num_threads()
        except psutil.Error:
            continue
        rss += r
        cpu += cp
        handles += h
        threads += th
        per.append({"pid": p.pid, "rss_mb": round(r, 1), "cpu": cp, "handles": h, "threads": th})
    return {"det_alive": True, "det_procs": len(per), "det_rss_mb": round(rss, 1), "det_cpu": round(cpu, 1),
            "det_handles": handles, "det_threads": threads, "det_per_proc": per}


def dir_bytes(d: Path) -> int:
    return sum(f.stat().st_size for f in d.glob("*") if f.is_file()) if d.exists() else 0


class Sampler(threading.Thread):
    def __init__(self, out: Path, outbox_dir: Path, *, interval=5.0, root_pid=None, ws=True, docker_every=3):
        super().__init__(daemon=True, name="sampler")
        self.out, self.outbox_dir, self.interval = out, outbox_dir, interval
        self.root_pid, self.docker_every = root_pid, docker_every
        self.stop_flag = False
        self.rows = []
        self.sess = c.Session()
        self.ws = WSProbe(c.base_url().replace("http", "ws") + "/ws", lambda: self.sess.cookie, c.base_url()) \
            if ws else None
        self.t0 = time.monotonic()
        self.f = open(out / "samples.jsonl", "a", encoding="utf-8")

    def run(self):
        if self.ws:
            self.ws.start()
        i = 0
        while not self.stop_flag:
            t = time.monotonic()
            try:
                self.rows.append(self.sample(i))
                self.f.write(json.dumps(self.rows[-1], default=str) + "\n")
                self.f.flush()
            except Exception as exc:                          # noqa: BLE001 — örnekleyici ölmesin
                self.f.write(json.dumps({"ts": c.utc(), "sampler_error": repr(exc)[:300]}) + "\n")
            i += 1
            time.sleep(max(0.0, self.interval - (time.monotonic() - t)))

    def stop(self):
        self.stop_flag = True
        self.join(self.interval * 3 + 30)
        if self.ws:
            self.ws.stop_flag = True
        self.f.close()

    def sample(self, i: int) -> dict:
        row = {"ts": c.utc(), "elapsed_sec": round(time.monotonic() - self.t0, 1)}
        row.update(proc_tree(self.root_pid))
        row.update(nvidia())
        row["sys_cpu"] = psutil.cpu_percent(None)
        row["sys_mem_used_mb"] = round(psutil.virtual_memory().used / 2**20)
        cams = {}
        for f in sorted(self.outbox_dir.glob("*.health.json")):
            try:
                h = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            tm = h["timings_ms"]
            cams[h["camera_id"]] = {
                "state": h["state"], "proc_fps": h["proc_fps"], "source_fps": h["run"]["source_fps"],
                "lag_sec": h["lag_sec"]["current"], "lag_p95": h["lag_sec"]["p95"],
                "startup_max": (h.get("startup_sec") or {}).get("max"),
                "frame_p50": tm["frame_ms"]["p50"], "frame_p95": tm["frame_ms"]["p95"],
                "frame_p99": tm["frame_ms"]["p99"], "inf_p50": tm["inference_ms"]["p50"],
                "inf_p95": tm["inference_ms"]["p95"], "inf_p99": tm["inference_ms"]["p99"],
                "track_p95": tm["bytetrack_ms"]["p95"], "monitor_p95": tm["monitor_ms"]["p95"],
                "frames": h["counters"]["frames_processed"], "reconnects": h["counters"]["reconnect_attempts"],
                "flags": h["flags"], "cov": h["coverage"]["current"],
                "unevaluated_tf": h["coverage"]["track_frames_unevaluated"], "tf": h["coverage"]["track_frames"],
                "never_eval_tracks": h["coverage"]["tracks_closed_never_evaluated"],
                "tracks_seen": h["coverage"]["tracks_seen"], "proc_rss_mb": h["resources"].get("rss_mb"),
                "proc_handles": h["resources"].get("handles"), "torch_reserved_mb":
                    h["resources"].get("torch_reserved_mb")}
        for f in sorted(self.outbox_dir.glob("*.status.json")):
            try:
                s = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            cams.setdefault(s["camera_id"], {}).update(
                {"ob_pending": s.get("pending_count"), "ob_oldest_sec": s.get("oldest_pending_age_sec"),
                 "ob_dead": s.get("dead_letter_count"), "ob_quar": s.get("quarantine_count"),
                 "ob_wal_bytes": s.get("wal_bytes"), "ob_db_bytes": s.get("db_bytes"), "ob_state": s.get("state"),
                 "ob_warn": s.get("warnings"), "ob_events_sent": s.get("events_sent"),
                 "ob_retries": s.get("retries"), "ob_replayed": s.get("replay_success_count")})
        row["cams"] = cams
        row["outbox_dir_bytes"] = dir_bytes(self.outbox_dir)
        t = time.perf_counter()
        try:
            r = self.sess.get("/api/state")
            row["state_ms"], row["state_status"] = round((time.perf_counter() - t) * 1e3, 1), r.status_code
        except Exception as exc:                              # noqa: BLE001
            row["state_ms"], row["state_status"] = None, type(exc).__name__
        try:
            m = self.sess.get("/api/ops/metrics").json()
            ev = m["db_write"].get("events", {})
            rep = m["db_write"].get("report", {})
            row["dash"] = {"db_events_p50": ev.get("p50"), "db_events_p95": ev.get("p95"),
                           "db_events_p99": ev.get("p99"), "db_events_err": ev.get("error"),
                           "db_report_p95": rep.get("p95"), "ws_active": m["websocket"]["active"],
                           "ws_frames_sent": m["websocket"]["frames_sent"],
                           "status": m["http"]["tracked_status"], "pool": (m["postgres"] or {}).get("pool"),
                           "pg_ready": (m["postgres"] or {}).get("ready"),
                           "ingest": m["ingest"]["by_route"], "events_inserted": m["ingest"].get("events_inserted"),
                           "frames_received": m["ingest"].get("frames_received"),
                           "state_p95": (m["http"]["routes"].get("/api/state", {}).get("latency_ms") or {}).get("p95"),
                           "frame_ingest_p95": (m["http"]["routes"].get("/ingest/frame", {}).get("latency_ms")
                                                or {}).get("p95"),
                           "snap_files": m["snapshot_storage"].get("files"),
                           "snap_bytes": m["snapshot_storage"].get("bytes")}
        except Exception as exc:                              # noqa: BLE001
            row["dash_error"] = type(exc).__name__
        if i % self.docker_every == 0:
            row.update(docker_stats())
            try:
                pg = c.psql("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database(); "
                            "SELECT pg_database_size(current_database()); SELECT count(*) FROM violation_events;")
                n, size, evs = pg.split()
                row["pg_conns"], row["pg_db_bytes"], row["pg_events"] = int(n), int(size), int(evs)
            except Exception as exc:                          # noqa: BLE001
                row["pg_error"] = type(exc).__name__
        if self.ws:
            row["ws"] = self.ws.snapshot_and_reset_window()
        return row


FLAT = ("ts", "elapsed_sec", "det_procs", "det_rss_mb", "det_cpu", "det_handles", "det_threads", "gpu_util",
        "gpu_mem_used_mb", "gpu_temp_c", "gpu_power_w", "sys_cpu", "sys_mem_used_mb", "state_ms", "outbox_dir_bytes",
        "dash_cpu", "dash_mem_mb", "dash_pids", "db_cpu", "db_mem_mb", "pg_conns", "pg_db_bytes", "pg_events")


def write_csv(out: Path, rows: list) -> None:
    cams = sorted({k for r in rows for k in (r.get("cams") or {})})
    ckeys = ("proc_fps", "lag_sec", "frame_p95", "inf_p95", "ob_pending", "ob_wal_bytes", "proc_rss_mb",
             "proc_handles")
    with open(out / "samples.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(list(FLAT) + ["dash_db_events_p95", "ws_active", "ws_frames"] +
                   [f"{cam}.{k}" for cam in cams for k in ckeys])
        for r in rows:
            d = r.get("dash") or {}
            ws = r.get("ws") or {}
            w.writerow([r.get(k) for k in FLAT] + [d.get("db_events_p95"), d.get("ws_active"), ws.get("frames")] +
                       [(r.get("cams") or {}).get(cam, {}).get(k) for cam in cams for k in ckeys])


def slope_per_hour(xs, ys) -> float | None:
    pts = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pts) < 3:
        return None
    mx = sum(p[0] for p in pts) / len(pts)
    my = sum(p[1] for p in pts) / len(pts)
    den = sum((p[0] - mx) ** 2 for p in pts)
    return round(sum((p[0] - mx) * (p[1] - my) for p in pts) / den * 3600, 3) if den else None


def _pct(vals, p):
    xs = sorted(v for v in vals if v is not None)
    if not xs:
        return None
    k = (len(xs) - 1) * p / 100
    f = int(k)
    return round(xs[f] + (xs[min(f + 1, len(xs) - 1)] - xs[f]) * (k - f), 3)


def summarize(rows: list, skip_sec: float = 0.0) -> dict:
    rows = [r for r in rows if "elapsed_sec" in r and r["elapsed_sec"] >= skip_sec]
    if not rows:
        return {"samples": 0}
    t = [r["elapsed_sec"] for r in rows]
    out = {"samples": len(rows), "window_sec": round(t[-1] - t[0], 1), "first_ts": rows[0]["ts"],
           "last_ts": rows[-1]["ts"]}
    for k in ("det_rss_mb", "det_cpu", "det_handles", "det_threads", "gpu_util", "gpu_mem_used_mb", "sys_cpu",
              "state_ms", "dash_mem_mb", "dash_cpu", "db_mem_mb", "pg_conns", "outbox_dir_bytes", "gpu_temp_c",
              "sys_mem_used_mb", "dash_pids", "pg_db_bytes"):
        vals = [r.get(k) for r in rows]
        if any(v is not None for v in vals):
            out[k] = {"p50": _pct(vals, 50), "p95": _pct(vals, 95), "max": max(v for v in vals if v is not None),
                      "first": next((v for v in vals if v is not None), None),
                      "last": next((v for v in reversed(vals) if v is not None), None),
                      "slope_per_hour": slope_per_hour(t, vals)}
    cams = sorted({k for r in rows for k in (r.get("cams") or {})})
    out["cameras"] = {}
    for cam in cams:
        cr = [(r["elapsed_sec"], r["cams"].get(cam, {})) for r in rows if cam in (r.get("cams") or {})]
        g = lambda k: [x.get(k) for _, x in cr]                 # noqa: E731
        frames = [f for f in g("frames") if f is not None]
        out["cameras"][cam] = {
            "proc_fps_p50": _pct(g("proc_fps"), 50), "proc_fps_p05": _pct(g("proc_fps"), 5),
            "source_fps": next((v for v in g("source_fps") if v), None),
            "lag_sec_p50": _pct(g("lag_sec"), 50), "lag_sec_p95": _pct(g("lag_sec"), 95),
            "lag_sec_max": max((v for v in g("lag_sec") if v is not None), default=None),
            "lag_slope_per_hour": slope_per_hour([x for x, _ in cr], g("lag_sec")),
            "startup_sec_max": max((v for v in g("startup_max") if v is not None), default=None),
            "frame_ms_p95_median": _pct(g("frame_p95"), 50), "frame_ms_p99_max": max(
                (v for v in g("frame_p99") if v is not None), default=None),
            "inf_ms_p50_median": _pct(g("inf_p50"), 50), "inf_ms_p95_median": _pct(g("inf_p95"), 50),
            "inf_ms_p99_max": max((v for v in g("inf_p99") if v is not None), default=None),
            "bytetrack_ms_p95_median": _pct(g("track_p95"), 50), "monitor_ms_p95_median": _pct(g("monitor_p95"), 50),
            "frames_processed": (frames[-1] - frames[0]) if len(frames) > 1 else None,
            "outbox_pending_max": max((v for v in g("ob_pending") if v is not None), default=None),
            "outbox_oldest_sec_max": max((v for v in g("ob_oldest_sec") if v is not None), default=None),
            "outbox_dead_max": max((v for v in g("ob_dead") if v is not None), default=None),
            "wal_bytes_max": max((v for v in g("ob_wal_bytes") if v is not None), default=None),
            "reconnects_last": next((v for v in reversed(g("reconnects")) if v is not None), None),
            "flags_seen": sorted({f for fl in g("flags") if fl for f in fl}),
            "proc_rss_slope_mb_per_hour": slope_per_hour([x for x, _ in cr], g("proc_rss_mb")),
            "proc_handles_slope_per_hour": slope_per_hour([x for x, _ in cr], g("proc_handles")),
            "unevaluated_track_frames_last": next((v for v in reversed(g("unevaluated_tf")) if v is not None), None),
            "track_frames_last": next((v for v in reversed(g("tf")) if v is not None), None),
            "never_evaluated_tracks_last": next((v for v in reversed(g("never_eval_tracks")) if v is not None), None),
            "tracks_seen_last": next((v for v in reversed(g("tracks_seen")) if v is not None), None)}
    d = [r.get("dash") or {} for r in rows]
    out["dashboard"] = {"db_events_p95_last": next((x.get("db_events_p95") for x in reversed(d)
                                                    if x.get("db_events_p95") is not None), None),
                        "db_events_p99_last": next((x.get("db_events_p99") for x in reversed(d)
                                                    if x.get("db_events_p99") is not None), None),
                        "state_p95_last": next((x.get("state_p95") for x in reversed(d)
                                                if x.get("state_p95") is not None), None),
                        "frame_ingest_p95_last": next((x.get("frame_ingest_p95") for x in reversed(d)
                                                       if x.get("frame_ingest_p95") is not None), None),
                        "tracked_status_last": next((x.get("status") for x in reversed(d) if x.get("status")), None),
                        "ws_active_max": max((x.get("ws_active") or 0 for x in d), default=None),
                        "pool_checked_out_max": max(((x.get("pool") or {}).get("checked_out") or 0 for x in d),
                                                    default=None)}
    ws = [r.get("ws") or {} for r in rows]
    if any(ws):
        out["websocket_probe"] = {"connects": ws[-1].get("connects"), "disconnects": ws[-1].get("disconnects"),
                                  "connect_failures": ws[-1].get("connect_failures"),
                                  "frames_total": ws[-1].get("frames"),
                                  "max_gap_sec": max((w.get("max_gap_sec") or 0 for w in ws), default=None)}
    return out
