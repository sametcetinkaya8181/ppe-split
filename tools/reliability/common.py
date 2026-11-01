"""FAZ 9 güvenilirlik araçlarının ortak yardımcıları: izole yığın (ppe-rel), oturum, psql, dedektör süreci başlatma/
zarif durdurma, zaman damgası. Ana yığına (ppe-split) ve ana veritabanına hiçbir komut göndermez.

Gizli bilgiler: test yığınının parolaları/token'ı her kurulumda rastgele üretilir ve yalnız Git dışındaki
tools/reliability/output/rel.env dosyasında durur (yalnız bu test yığını içindir)."""
import json
import os
import secrets
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
OUT = Path(os.getenv("REL_OUTPUT", HERE / "output"))
COMPOSE = HERE / "docker-compose.reliability.yml"
ENV_FILE = OUT / "rel.env"
PROJECT = "ppe-rel"
DETECTOR = ROOT / "detector"
PY = DETECTOR / ".venv" / "Scripts" / "python.exe"
if not PY.exists():
    PY = Path(sys.executable)
VIDEOS = {"V1": DETECTOR / "videos" / "16533594_1920_1080_25fps.mp4",
          "V2": DETECTOR / "videos" / "5434223-hd_1920_1080_24fps.mp4",
          "V3": DETECTOR / "videos" / "8965526-uhd_2160_3840_25fps.mp4"}
ADMIN_USER = "reladmin"


def utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def outdir(name: str) -> Path:
    d = OUT / f"{stamp()}_{name}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


# ---------------------------------------------------------------- yığın
def env() -> dict:
    """rel.env (yoksa üretilir). Değerler yalnız bu izole test yığınına aittir."""
    OUT.mkdir(parents=True, exist_ok=True)
    if not ENV_FILE.exists():
        vals = {"POSTGRES_DB": "ppe_rel", "POSTGRES_USER": "ppe_rel_admin",
                "POSTGRES_PASSWORD": secrets.token_urlsafe(24), "PPE_APP_PASSWORD": secrets.token_urlsafe(24),
                "PPE_MIGRATOR_PASSWORD": secrets.token_urlsafe(24), "INGEST_TOKEN": secrets.token_urlsafe(32),
                "ADMIN_PASSWORD": secrets.token_urlsafe(18) + "-Aa1", "REL_PORT": "8100"}
        ENV_FILE.write_text("".join(f"{k}={v}\n" for k, v in vals.items()), encoding="utf-8")
    out = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k] = v
    return out


def base_url() -> str:
    return f"http://127.0.0.1:{env()['REL_PORT']}"


def compose(*args, check=True, capture=True, timeout=600) -> subprocess.CompletedProcess:
    cmd = ["docker", "compose", "-p", PROJECT, "-f", str(COMPOSE), "--env-file", str(ENV_FILE), *args]
    return subprocess.run(cmd, check=check, capture_output=capture, text=True, timeout=timeout)


def container(service: str) -> str:
    return f"{PROJECT}-{service}-1"


def psql(sql: str, timeout=30) -> str:
    e = env()
    r = subprocess.run(["docker", "exec", "-i", container("db"), "psql", "-U", e["POSTGRES_USER"], "-d",
                        e["POSTGRES_DB"], "-tAX", "-F", "\t", "-c", sql], capture_output=True, text=True,
                       timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"psql hata: {r.stderr.strip()[:300]}")
    return r.stdout.strip()


def wait_http(url: str, timeout=120, ok=(200,)) -> float:
    import requests
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        try:
            if requests.get(url, timeout=2).status_code in ok:
                return time.monotonic() - t0
        except requests.RequestException:
            pass
        time.sleep(0.25)
    raise TimeoutError(url)


def up(build=True) -> None:
    e = env()
    args = ["up", "-d"] + (["--build"] if build else [])
    compose(*args, timeout=1200)
    wait_http(base_url() + "/health/ready", 180)
    r = subprocess.run(["docker", "exec", "-i", container("dashboard"), "python", "-m", "auth.cli", "create",
                        ADMIN_USER, "--role", "admin", "--password-stdin"], input=e["ADMIN_PASSWORD"] + "\n",
                       capture_output=True, text=True)
    if r.returncode != 0 and "zaten" not in (r.stdout + r.stderr) and "exists" not in (r.stdout + r.stderr):
        print("UYARI: yönetici oluşturulamadı:", (r.stdout + r.stderr).strip()[:300])


def reset_data() -> None:
    """Yalnız izole test DB'sindeki olay tablolarını boşaltır (senaryolar arası temiz başlangıç)."""
    psql("TRUNCATE violation_events, incidents, person_run_reports, detector_runs, cameras RESTART IDENTITY")


# ---------------------------------------------------------------- oturum
class Session:
    def __init__(self):
        import requests
        self.s = requests.Session()
        self.url = base_url()
        self.login()

    def login(self):
        e = env()
        r = self.s.post(self.url + "/api/auth/login", json={"username": ADMIN_USER, "password": e["ADMIN_PASSWORD"]},
                        headers={"Origin": self.url}, timeout=10)
        r.raise_for_status()
        self.s.headers["X-CSRF-Token"] = r.json()["csrf_token"]

    @property
    def cookie(self) -> str:
        return "; ".join(f"{k}={v}" for k, v in self.s.cookies.items())

    def get(self, path, **kw):
        r = self.s.get(self.url + path, timeout=kw.pop("timeout", 10), **kw)
        if r.status_code == 401:
            self.login()
            r = self.s.get(self.url + path, timeout=10, **kw)
        return r


# ---------------------------------------------------------------- dedektör süreci
LAUNCHER = HERE / "detector_launcher.py"


def detector_env(outbox_dir: Path, *, stride=2, token=None, url=None, extra=None) -> dict:
    e = env()
    outbox_dir.mkdir(parents=True, exist_ok=True)
    envf = outbox_dir / "detector.env"
    envf.write_text(f"INGEST_TOKEN={token if token is not None else e['INGEST_TOKEN']}\n", encoding="utf-8")
    out = {k: v for k, v in os.environ.items() if not k.startswith(("VIDEO_SOURCE", "YOUTUBE_", "SOURCE_"))}
    out.update({"DASHBOARD_URL": url or base_url(), "INGEST_TOKEN": token if token is not None else e["INGEST_TOKEN"],
                "DETECTOR_ENV_FILE": str(envf), "OUTBOX_DIR": str(outbox_dir), "VID_STRIDE": str(stride),
                "LOOP": "1", "DEVICE": "0", "IMGSZ": "640", "STREAM_WIDTH": "640", "START_DELAY": "2",
                "PYTHONUNBUFFERED": "1", "YOLO_OFFLINE": "1", "HEALTH_EVERY": "2"})
    out.update(extra or {})
    return out


def start_detector(sources: list[str], outbox_dir: Path, log: Path, **kw) -> subprocess.Popen:
    """Gerçek detector.py (main) — ayrı süreç grubu; CTRL_BREAK ile zarif durdurulabilir (Ctrl+C yolu)."""
    f = open(log, "a", encoding="utf-8")
    return subprocess.Popen([str(PY), str(LAUNCHER), *sources], cwd=DETECTOR, env=detector_env(outbox_dir, **kw),
                            stdout=f, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)


def stop_detector(p: subprocess.Popen, timeout=40) -> dict:
    """Zarif durdurma (Ctrl+C eşdeğeri: run 'interrupted', outbox boşaltılır); süre aşılırsa süreç ağacı öldürülür."""
    t0 = time.monotonic()
    if p.poll() is None:
        try:
            os.kill(p.pid, signal.CTRL_BREAK_EVENT)
        except OSError:
            pass
        try:
            p.wait(timeout)
        except subprocess.TimeoutExpired:
            kill_tree(p.pid)
            p.wait(10)
            return {"graceful": False, "sec": round(time.monotonic() - t0, 1), "returncode": p.returncode}
    return {"graceful": True, "sec": round(time.monotonic() - t0, 1), "returncode": p.returncode}


def kill_tree(pid: int) -> None:
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)


def child_pids(pid: int) -> list[int]:
    import psutil
    try:
        return [c.pid for c in psutil.Process(pid).children(recursive=True)]
    except psutil.Error:
        return []
