"""Dashboard bellek testi (FAZ 10-A.1): izole ppe-rel yığınında dashboard'u bellek probuyla (memboot.py) çalıştırır,
gerçek outbox/DurableSender kullanan sentetik kameralarla (eventgen.py, gerçekçi boyutta video kareleri) yük verir,
operatör ekranını taklit eder (/ws akışı + /api/state yoklaması + /api/ops/metrics) ve belleği ayrı katmanlarda
örnekler: cgroup (docker stats ile aynı + anon/file/kernel ayrımı), süreç RSS, glibc ayırıcı, Python blokları/GC.
Ürün koduna ve ana yığına (ppe-split) dokunmaz. GPU gerekmez.

  python tools/reliability/memtest.py --minutes 60 --label base
  python tools/reliability/memtest.py --minutes 60 --label arena2 --env MALLOC_ARENA_MAX=2
  python tools/reliability/memtest.py --minutes 30 --label trace --trace 8        (tracemalloc; yavaşlatır)

Çıktı: tools/reliability/output/<zaman>_mem_<etiket>/ (memprobe.jsonl, host.jsonl, result.json) — Git dışı."""
import argparse
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as c                                     # noqa: E402
from sampler import _mb                                # noqa: E402
from wsprobe import WSProbe                            # noqa: E402

CGROUP_KEYS = ("anon", "file", "kernel", "slab", "sock", "shmem", "active_file", "inactive_file", "pagetables")
SNAPSHOT_EVERY = 10


def override(out: Path, env: dict, trace: int, interval: float) -> Path:
    envs = {"MEMPROBE_INTERVAL": str(interval), "MEMPROBE_TRACE": str(trace), **env}
    lines = ["services:", "  dashboard:", '    command: ["python", "/memprobe/memboot.py"]', "    volumes:",
             f'      - "{(c.HERE / "memboot.py").as_posix()}:/memprobe/memboot.py:ro"', "    environment:"]
    lines += [f'      {k}: "{v}"' for k, v in envs.items()]
    p = out / "override.yml"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def compose(ov: Path, *args, timeout=600):
    cmd = ["docker", "compose", "-p", c.PROJECT, "-f", str(c.COMPOSE), "-f", str(ov), "--env-file", str(c.ENV_FILE),
           *args]
    return subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=timeout)


def bring_up(ov: Path, build: bool):
    c.env()
    compose(ov, "up", "-d", *(["--build"] if build else []), "--force-recreate", "dashboard", timeout=1200)
    c.wait_http(c.base_url() + "/health/ready", 180)
    e = c.env()
    r = subprocess.run(["docker", "exec", "-i", c.container("dashboard"), "python", "-m", "auth.cli", "create",
                        c.ADMIN_USER, "--role", "admin", "--password-stdin"], input=e["ADMIN_PASSWORD"] + "\n",
                       capture_output=True, text=True)
    if r.returncode != 0 and "zaten" not in (r.stdout + r.stderr) and "exists" not in (r.stdout + r.stderr):
        raise RuntimeError("yönetici oluşturulamadı: " + (r.stdout + r.stderr).strip()[:300])


def host_sample() -> dict:
    out = {}
    try:
        r = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{json .}}", c.container("dashboard")],
                           capture_output=True, text=True, timeout=20)
        d = json.loads(r.stdout.strip().splitlines()[0])
        out["docker_mem_mb"] = round(_mb(d["MemUsage"].split("/")[0].strip()), 2)
        out["docker_cpu"] = float(d["CPUPerc"].rstrip("%"))
        out["pids"] = int(d["PIDs"])
    except (subprocess.SubprocessError, ValueError, IndexError, KeyError) as exc:
        out["docker_error"] = type(exc).__name__
    try:
        r = subprocess.run(["docker", "exec", c.container("dashboard"), "cat", "/sys/fs/cgroup/memory.stat",
                            "/sys/fs/cgroup/memory.current"], capture_output=True, text=True, timeout=20)
        lines = r.stdout.splitlines()
        stat = dict(line.split() for line in lines if len(line.split()) == 2)
        out["cg"] = {k: round(int(stat[k]) / 2**20, 2) for k in CGROUP_KEYS if k in stat}
        out["cg"]["current"] = round(int(lines[-1]) / 2**20, 2)
    except (subprocess.SubprocessError, ValueError, IndexError) as exc:
        out["cg_error"] = type(exc).__name__
    try:
        out["pg_conns"] = int(c.psql("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()"))
    except (RuntimeError, ValueError, subprocess.SubprocessError):
        pass
    return out


class Operator(threading.Thread):
    """Tarayıcıyı taklit eder: /api/state'i 0.7 sn'de bir (index.html ile aynı) ve /api/ops/metrics'i 30 sn'de bir."""

    def __init__(self, sess: c.Session, period: float):
        super().__init__(daemon=True)
        self.sess, self.period = sess, period
        self.stop_flag = threading.Event()
        self.stats = {"state_ok": 0, "state_err": 0, "metrics_ok": 0}
        self.after = 0

    def run(self):
        last_metrics = 0.0
        while not self.stop_flag.wait(self.period):
            try:
                r = self.sess.get(f"/api/state?after={self.after}")
                if r.ok:
                    self.after = r.json().get("last_alert_id", self.after)
                    self.stats["state_ok"] += 1
                else:
                    self.stats["state_err"] += 1
                if time.monotonic() - last_metrics >= 30:
                    last_metrics = time.monotonic()
                    if self.sess.get("/api/ops/metrics").ok:
                        self.stats["metrics_ok"] += 1
            except Exception:                           # noqa: BLE001 — ölçüm aracı; hata sayılır
                self.stats["state_err"] += 1


def start_cams(out: Path, n: int, a) -> list:
    e = c.env()
    procs = []
    for i in range(n):
        cam = f"relm{i + 1}"
        ob = out / "outbox"
        ob.mkdir(exist_ok=True)
        envf = ob / "detector.env"
        envf.write_text(f"INGEST_TOKEN={e['INGEST_TOKEN']}\n", encoding="utf-8")
        env = {**os.environ, "DASHBOARD_URL": c.base_url(), "INGEST_TOKEN": e["INGEST_TOKEN"],
               "DETECTOR_ENV_FILE": str(envf), "PYTHONUNBUFFERED": "1"}
        c.assert_env_isolated(env, context=cam)
        video = c.VIDEOS["V1" if i % 2 == 0 else "V2"]
        args = [str(c.PY), str(c.HERE / "eventgen.py"), "--camera", cam, "--outbox", str(ob), "--ledger",
                str(out / f"ledger_{cam}.jsonl"), "--duration", str(a.minutes * 60 + 30), "--run-seconds",
                str(a.run_seconds), "--fps", str(a.fps), "--persons", str(a.persons), "--period", str(a.period),
                "--frame-video", str(video), "--frame-width", "640"] + (["--snapshot"] if a.snapshot else [])
        f = open(out / f"{cam}.log", "a", encoding="utf-8")
        procs.append((cam, subprocess.Popen(args, cwd=c.DETECTOR, env=env, stdout=f, stderr=subprocess.STDOUT,
                                            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP), f))
    return procs


def stop_cams(procs) -> dict:
    for _, p, _ in procs:
        if p.poll() is None:
            try:
                os.kill(p.pid, signal.CTRL_BREAK_EVENT)
            except OSError:
                pass
    res = {}
    for cam, p, f in procs:
        try:
            p.wait(60)
        except subprocess.TimeoutExpired:
            c.kill_tree(p.pid)
        f.close()
        res[cam] = p.returncode
    return res


def slope(points) -> float | None:
    """En küçük kareler eğimi (birim/saat). points: [(sn, değer)]."""
    pts = [(x, y) for x, y in points if y is not None]
    if len(pts) < 3:
        return None
    mx = sum(x for x, _ in pts) / len(pts)
    my = sum(y for _, y in pts) / len(pts)
    den = sum((x - mx) ** 2 for x, _ in pts)
    return round(sum((x - mx) * (y - my) for x, y in pts) / den * 3600, 3) if den else None


def summarize(probe: list, host: list, skip_sec: float) -> dict:
    pr = [r for r in probe if "t" in r and r["t"] >= skip_sec]
    hs = [r for r in host if r["elapsed"] >= skip_sec]

    def series(rows, key, fn):
        return [(r[key], fn(r)) for r in rows]

    def g(path):
        def fn(r):
            v = r
            for k in path:
                v = v.get(k) if isinstance(v, dict) else None
            return v
        return fn
    metrics = {
        "docker_mem_mb": (hs, "elapsed", g(["docker_mem_mb"]), 1),
        "cgroup_anon_mb": (hs, "elapsed", g(["cg", "anon"]), 1),
        "cgroup_file_mb": (hs, "elapsed", g(["cg", "file"]), 1),
        "cgroup_kernel_mb": (hs, "elapsed", g(["cg", "kernel"]), 1),
        "rss_anon_mb": (pr, "t", g(["RssAnon"]), 1 / 1024),
        "rss_mb": (pr, "t", g(["VmRSS"]), 1 / 1024),
        "malloc_arena_mb": (pr, "t", g(["malloc", "arena_kb"]), 1 / 1024),
        "malloc_in_use_mb": (pr, "t", g(["malloc", "in_use_kb"]), 1 / 1024),
        "malloc_free_mb": (pr, "t", g(["malloc", "free_kb"]), 1 / 1024),
        "malloc_mmap_mb": (pr, "t", g(["malloc", "mmap_kb"]), 1 / 1024),
        "py_blocks": (pr, "t", g(["py_blocks"]), 1),
        "threads": (pr, "t", g(["Threads"]), 1),
        "fds": (pr, "t", g(["fds"]), 1),
        "session_subclasses": (pr, "t", g(["app", "session_subclasses"]), 1),
        "sa_compiled_cache": (pr, "t", g(["app", "sa_compiled_cache"]), 1),
        "auth_cache": (pr, "t", g(["app", "auth_cache"]), 1),
    }
    out = {}
    for name, (rows, tkey, fn, scale) in metrics.items():
        pts = [(r[tkey], (fn(r) * scale) if fn(r) is not None else None) for r in rows]
        vals = [v for _, v in pts if v is not None]
        if not vals:
            continue
        n = len(vals)
        q = max(1, n // 10)
        out[name] = {"first10pct_median": round(sorted(vals[:q])[q // 2], 3),
                     "last10pct_median": round(sorted(vals[-q:])[q // 2], 3),
                     "min": round(min(vals), 3), "max": round(max(vals), 3), "slope_per_hour": slope(pts), "n": n}
    return out


def delivery(out: Path, cams: list) -> dict:
    sent = 0
    ids = set()
    for cam in cams:
        led = out / f"ledger_{cam}.jsonl"
        if led.exists():
            for line in led.read_text(encoding="utf-8").splitlines():
                rec = json.loads(line)
                if rec.get("kind") == "event":
                    ids.add(rec["event_id"])
    sent = len(ids)
    names = ",".join(f"'{x}'" for x in cams)
    n, d = map(int, c.psql(f"SELECT count(*), count(DISTINCT event_id) FROM violation_events "
                           f"WHERE camera_id IN ({names})").split())
    return {"ledger_events": sent, "db_events": n, "db_distinct": d, "lost": sent - d, "duplicates": n - d}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=60)
    ap.add_argument("--label", default="base")
    ap.add_argument("--cams", type=int, default=4)
    ap.add_argument("--fps", type=float, default=12)
    ap.add_argument("--run-seconds", type=float, default=20)
    ap.add_argument("--persons", type=int, default=3)
    ap.add_argument("--period", type=float, default=8)
    ap.add_argument("--snapshot", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument("--interval", type=float, default=30)
    ap.add_argument("--state-period", type=float, default=0.7)
    ap.add_argument("--trace", type=int, default=0, help="tracemalloc çerçeve sayısı (0 = kapalı)")
    ap.add_argument("--env", action="append", default=[], help="dashboard ortam değişkeni KEY=VAL (ör. MALLOC_ARENA_MAX=2)")
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--skip-min", type=float, default=10, help="özet eğimlerinde ısınma (dakika)")
    a = ap.parse_args(argv)
    env = dict(kv.split("=", 1) for kv in a.env)
    for k in env:
        if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", k):
            raise SystemExit(f"geçersiz ortam değişkeni adı: {k}")
    out = c.outdir(f"mem_{a.label}")
    ov = override(out, env, a.trace, a.interval)
    res = {"label": a.label, "args": vars(a), "dashboard_env": env, "started_utc": c.utc()}
    bring_up(ov, a.build)
    c.reset_data()
    res["image"] = subprocess.run(["docker", "inspect", c.container("dashboard"), "--format", "{{.Image}}"],
                                  capture_output=True, text=True).stdout.strip()
    sess = c.Session()
    ws = WSProbe(c.base_url().replace("http", "ws") + "/ws", lambda: sess.cookie, c.base_url())
    ws.start()
    op = Operator(sess, a.state_period)
    op.start()
    procs = start_cams(out, a.cams, a)
    host, t0 = [], time.monotonic()
    hf = open(out / "host.jsonl", "a", encoding="utf-8")
    res["measure_start_utc"] = c.utc()
    try:
        while time.monotonic() - t0 < a.minutes * 60:
            if any(p.poll() is not None for _, p, _ in procs):
                res["camera_exit"] = {cam: p.returncode for cam, p, _ in procs if p.poll() is not None}
                break
            rec = {"elapsed": round(time.monotonic() - t0, 1), "utc": c.utc(), **host_sample(),
                   "ws": {k: v for k, v in ws.snapshot_and_reset_window().items() if k != "per_cam"},
                   "operator": dict(op.stats)}
            host.append(rec)
            hf.write(json.dumps(rec) + "\n")
            hf.flush()
            time.sleep(max(0.0, a.interval - (time.monotonic() - t0 - rec["elapsed"])))
    finally:
        res["measure_end_utc"] = c.utc()
        res["measured_sec"] = round(time.monotonic() - t0, 1)
        res["camera_stop"] = stop_cams(procs)
        op.stop_flag.set()
        ws.close_now()
        hf.close()
    logs = subprocess.run(["docker", "logs", c.container("dashboard")], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    probe = []
    with open(out / "memprobe.jsonl", "w", encoding="utf-8") as f:
        for line in (logs.stdout + logs.stderr).splitlines():
            if line.startswith("MEMPROBE "):
                f.write(line[9:] + "\n")
                try:
                    probe.append(json.loads(line[9:]))
                except ValueError:
                    pass
    res["tracebacks_in_log"] = (logs.stdout + logs.stderr).count("Traceback")
    res["summary"] = summarize(probe, host, a.skip_min * 60)
    deep = [r for r in probe if "deep" in r]
    res["deep_first"] = deep[0]["deep"] if deep else None
    res["deep_last"] = deep[-1]["deep"] if deep else None
    res["app_last"] = probe[-1].get("app") if probe else None
    res["operator"] = op.stats
    res["ws_total"] = {k: ws.stats.get(k) for k in ("connects", "disconnects", "connect_failures", "pongs")}
    res["delivery"] = delivery(out, [cam for cam, _, _ in procs])
    res["ended_utc"] = c.utc()
    c.write_json(out / "result.json", res)
    print(json.dumps({k: res[k] for k in ("label", "measured_sec", "delivery")}, ensure_ascii=False))
    print(json.dumps(res["summary"], ensure_ascii=False, indent=1))
    print("çıktı:", out)


if __name__ == "__main__":
    main()
