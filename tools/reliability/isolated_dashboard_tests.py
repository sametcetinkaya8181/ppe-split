"""FAZ 10-A.6: dashboard pytest paketini TAMAMEN izole, geçici bir PostgreSQL 17 kümesinde çalıştırır.

  python tools/reliability/isolated_dashboard_tests.py [pytest argümanları...]

FAZ 01: önerilen test yolu budur (compose `tests` profili de artık ayrı bir `test-db` kümesi kullanır). Ana kümeye
hiç bağlanmaz:
  - ayrı Docker ağı `ppe-review-test-net` (yayınlanan port YOK) + geçici `postgres:17` container'ı
    (`ppe-review-test-db`, volume'suz; silinince veri kalmaz),
  - parola her koşuda rastgele üretilir, yalnız bu süreçte ve container ortamında durur (dosyaya yazılmaz),
  - küme `cluster_name=ppe-ephemeral-test` ile başlatılır; testler bu işaret olmadan bağlanmayı reddeder
    (dashboard/tests/db_guard.py, FAIL-CLOSED),
  - testler mevcut test imajında (`ppe-dashboard:faz9-rel`; ana `ppe-dashboard:latest` imajı yeniden
    OLUŞTURULMAZ), kod salt okunur bağlanır,
  - başlatmadan önce alt süreç ortamı üretim gizli değerlerine karşı denetlenir (evaluation.isolation),
  - sonunda container'lar ve ağ silinir (yalnız bu aracın oluşturdukları)."""
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "detector"))
from evaluation.isolation import assert_env_isolated, production_secrets   # noqa: E402

NET, DB = "ppe-review-test-net", "ppe-review-test-db"
PG_IMAGE = "postgres:17@sha256:b8faeff7712663c2773a190181e1246e37e364b887611c04658c28b44e2408fe"
TEST_IMAGE = os.getenv("REVIEW_TEST_IMAGE", "ppe-dashboard:faz9-rel")
OWNED_LABEL = "ppe.review-test=1"
EPHEMERAL_MARKER = "ppe-ephemeral-test"          # dashboard/tests/db_guard.py ile aynı


def sh(*args, check=True, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(list(args), check=check, text=True, capture_output=True, **kw)


def cleanup() -> None:
    """Yalnız bu aracın etiketli container'larını ve ağını siler."""
    ids = sh("docker", "ps", "-aq", "--filter", f"label={OWNED_LABEL}", check=False).stdout.split()
    if ids:
        sh("docker", "rm", "-f", *ids, check=False)
    if sh("docker", "network", "ls", "-q", "--filter", f"name=^{NET}$", check=False).stdout.strip():
        sh("docker", "network", "rm", NET, check=False)


DERIVED = "ppe-dashboard:review-test"


def ensure_test_image() -> str:
    """TEST_IMAGE + requirements-dev.txt (pytest/httpx) = ayrı etiket. Ana/rel etiketleri değişmez."""
    if sh("docker", "run", "--rm", DERIVED, "python", "-c", "import pytest, httpx", check=False).returncode == 0:
        return DERIVED
    dockerfile = (f"FROM {TEST_IMAGE}\nUSER root\nCOPY requirements.txt requirements-dev.txt /tmp/req/\n"
                  "RUN pip install --no-cache-dir -q --root-user-action=ignore --disable-pip-version-check "
                  "-r /tmp/req/requirements-dev.txt\nUSER 10001\n")
    sh("docker", "build", "-q", "-t", DERIVED, "-f", "-", str(ROOT / "dashboard"), input=dockerfile)
    return DERIVED


def _start_fixture(image: str, env: dict, module: str, ports: dict, health: str) -> bool:
    """Sentetik veri fixture'ı: yalnız 127.0.0.1'e yayınlanır; DB yalnız internal ağdadır."""
    import urllib.request
    name = "ppe-review-ui-fixture"
    sh("docker", "run", "-d", "--name", name, "--label", OWNED_LABEL, "-w", "/app",
       *[x for h, c in ports.items() for x in ("-p", f"127.0.0.1:{h}:{c}")],
       "-v", f"{ROOT / 'dashboard'}:/app:ro", *[x for k in env for x in ("-e", k)], image,
       "python", "-m", module, env={**os.environ, **env})
    sh("docker", "network", "connect", NET, name)
    for _ in range(180):
        try:
            urllib.request.urlopen(health, timeout=2)
            return True
        except OSError:
            time.sleep(1)
    print(sh("docker", "logs", "--tail", "30", name, check=False).stdout, file=sys.stderr)
    return False


def run_ui(image: str, test_env: dict, shots: str) -> int:
    """Tarayıcı testi (FAZ 6-10A.6): tests/ui/serve_fixture.py (:8001-8003) + node + Edge ile ui_check.mjs.
    FAZ 01: UI_PASSWORD her koşuda rastgele üretilir; yalnız fixture container'ı ve node sürecinin ortamında durur."""
    env = {**test_env, "UI_PASSWORD": secrets.token_urlsafe(18) + "-Aa1"}
    if not _start_fixture(image, env, "tests.ui.serve_fixture", {p: p for p in (8001, 8002, 8003)},
                          "http://127.0.0.1:8003/healthz"):
        return 3
    return subprocess.run(["node", str(ROOT / "dashboard" / "tests" / "ui" / "ui_check.mjs"), shots],
                          env={**os.environ, "UI_PASSWORD": env["UI_PASSWORD"]}).returncode


def run_ui_live(image: str, test_env: dict, shots: str) -> int:
    """FAZ 10-A.7 canlı izleme/rol görünürlüğü tarayıcı testi: tests/ui/serve_live_fixture.py (127.0.0.1:18011) +
    headless Chrome/Edge (CDP) ile ui_check_10a7.py. websocket-client gerekir: UI_PYTHON ile o paketi içeren
    yorumlayıcı verilebilir (varsayılan: bu süreç). Parola geçici dosyada durur ve koşu sonunda silinir."""
    import tempfile
    env = {**test_env, "UI_PASSWORD": secrets.token_urlsafe(18) + "-Aa1"}
    if not _start_fixture(image, env, "tests.ui.serve_live_fixture", {18011: 8011}, "http://127.0.0.1:18011/healthz"):
        return 3
    fd, pwf = tempfile.mkstemp(prefix="ppe-ui-", suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(env["UI_PASSWORD"])
        py = os.getenv("UI_PYTHON") or sys.executable
        return subprocess.run([py, str(ROOT / "dashboard" / "tests" / "ui" / "ui_check_10a7.py"), shots],
                              env={**os.environ, "UI_PASSWORD_FILE": pwf, "UI_BASE": "http://127.0.0.1:18011"}
                              ).returncode
    finally:
        os.unlink(pwf)


def main(argv) -> int:
    pw = secrets.token_urlsafe(24)
    secrets_prod = production_secrets()
    db_env = {"POSTGRES_USER": "review_admin", "POSTGRES_PASSWORD": pw, "POSTGRES_DB": "postgres"}
    test_env = {"PYTHONDONTWRITEBYTECODE": "1", "DATA_DIR": "/tmp/ppe-test-data",
                "ALLOWED_HOSTS": "127.0.0.1,localhost,::1,testserver", "PASSWORD_HASH_PROFILE": "test",
                "TEST_DATABASE_URL": f"postgresql+psycopg://review_admin:{pw}@{DB}:5432/ppe_test"}
    assert_env_isolated(db_env, secrets_prod, context="izole test DB")
    assert_env_isolated(test_env, secrets_prod, context="izole testler")
    cleanup()
    sh("docker", "network", "create", "--internal", "--label", OWNED_LABEL, NET)
    try:
        sh("docker", "run", "-d", "--name", DB, "--network", NET, "--label", OWNED_LABEL, "--tmpfs",
           "/var/lib/postgresql/data", *[x for k in db_env for x in ("-e", k)], PG_IMAGE,
           "postgres", "-c", f"cluster_name={EPHEMERAL_MARKER}", "-c", "fsync=off",
           env={**os.environ, **db_env})
        for _ in range(60):
            if sh("docker", "exec", DB, "pg_isready", "-U", "review_admin", check=False).returncode == 0:
                break
            time.sleep(1)
        else:
            print("BLOCKED: izole PostgreSQL hazır olmadı", file=sys.stderr)
            return 3
        version = sh("docker", "exec", DB, "postgres", "--version").stdout.strip()
        print(f"izole küme: {DB} ({version}), ağ {NET} (internal, port yayını yok), imaj {TEST_IMAGE}", flush=True)
        image = ensure_test_image()
        if argv[:1] == ["--ui"]:
            return run_ui(image, test_env, argv[1] if len(argv) > 1 else "ui-shots")
        if argv[:1] == ["--ui-live"]:
            return run_ui_live(image, test_env, argv[1] if len(argv) > 1 else "ui-shots-10a7")
        pytest_args = argv or ["-q", "-p", "no:cacheprovider"]
        # kod salt okunur; pytest önbelleği kapalı, bytecode yazılmaz. internal ağ: dışarı erişim yok
        cmd = ["docker", "run", "--rm", "--label", OWNED_LABEL, "--network", NET, "-w", "/app",
               "-v", f"{ROOT / 'dashboard'}:/app:ro", *[x for k in test_env for x in ("-e", k)], image,
               "pytest", *pytest_args]
        r = subprocess.run(cmd, env={**os.environ, **test_env})
        return r.returncode
    finally:
        cleanup()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
