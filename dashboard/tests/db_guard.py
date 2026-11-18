"""FAZ 01: test veritabanı hedef koruması (FAIL-CLOSED).

Testler ve test yardımcıları (conftest, lan_server, perf_history) `DROP DATABASE` / `CREATE ROLE` çalıştırır. Bu modül
bu işlemlerden ÖNCE hedefin açıkça test için oluşturulmuş GEÇİCİ bir PostgreSQL kümesi olduğunu doğrular; herhangi
bir koşul sağlanmazsa UnsafeTestTarget fırlatılır ve hiçbir yıkıcı komut çalışmaz. Atlatma (bypass) değişkeni YOKTUR.

Koşullar:
  1. Ortam: üretim DB kimlik bilgisi değişkenleri (DATABASE_URL, MIGRATE_DATABASE_URL, ADMIN_DATABASE_URL,
     POSTGRES_PASSWORD, PPE_APP_PASSWORD, PPE_MIGRATOR_PASSWORD) süreç ortamında bulunmamalı; ortamdaki hiçbir değer
     (erişilebiliyorsa) kökteki/dedektördeki üretim .env gizli değerlerine eşit olmamalı.
  2. Adres: TEST_DATABASE_URL ayrıştırılabilir olmalı; veritabanı adı '_test' ile bitmeli; host üretim compose
     servis adı ('db') olmamalı ve izinli geçici küme adlarından biri olmalı (FAZ 02-A: TEST_HOSTS — localhost,
     127.0.0.1, host.docker.internal veya başka projelerin PostgreSQL servisleri reddedilir; reddedilen adrese
     BAĞLANTI DENENMEZ); parola üretim gizli değerlerine eşit olmamalı.
  3. Sunucu kimliği: kümenin `cluster_name` ayarı EPHEMERAL_MARKER olmalı (geçici küme `-c cluster_name=...` ile
     başlatılır; üretim kümesinde bu ayar yoktur).
  4. Küme içeriği: 'postgres', şablonlar ve adı '_test' ile biten veritabanları dışında veritabanı olmamalı
     (ör. üretimdeki 'ppe' varsa reddedilir).
Hata iletileri yalnız anahtar/koşul adlarını içerir; gizli değer yazılmaz."""
import os
from pathlib import Path

EPHEMERAL_MARKER = "ppe-ephemeral-test"
FORBIDDEN_ENV = ("DATABASE_URL", "MIGRATE_DATABASE_URL", "ADMIN_DATABASE_URL", "POSTGRES_PASSWORD",
                 "PPE_APP_PASSWORD", "PPE_MIGRATOR_PASSWORD")
PRODUCTION_HOSTS = frozenset({"db"})                 # docker-compose.yml ana PostgreSQL servisi
# FAZ 02-A: yalnız bu adlara bağlanılır — compose `test-db` (profil test, internal testnet) ve izole koşucunun
# (tools/reliability/isolated_dashboard_tests.py) internal ağdaki kümesi. Yeni bir koşucu eklenirse buraya eklenir.
TEST_HOSTS = frozenset({"test-db", "ppe-review-test-db"})
SECRET_KEYS = ("INGEST_TOKEN", "INGEST_TOKEN_NEXT", "POSTGRES_PASSWORD", "PPE_APP_PASSWORD", "PPE_MIGRATOR_PASSWORD")
MIN_SECRET_LEN = 12
_ROOT = Path(__file__).resolve().parents[2]
PROD_ENV_FILES = (_ROOT / ".env", _ROOT / "detector" / ".env")


class UnsafeTestTarget(RuntimeError):
    """Test hedefi güvenli değil; yıkıcı işlem yapılmadı."""


def _read_env(path: Path) -> dict:
    out = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            out[k.strip()] = v
    return out


def production_secrets(files=PROD_ENV_FILES) -> list:
    """Karşılaştırma için üretim gizli değerleri (yalnız bellekte; container içinde dosyalar yoktur -> boş)."""
    return [v for p in files for k, v in _read_env(Path(p)).items() if k in SECRET_KEYS and len(v) >= MIN_SECRET_LEN]


def check_environment(environ, secrets: list) -> None:
    present = sorted(k for k in FORBIDDEN_ENV if environ.get(k))
    if present:
        raise UnsafeTestTarget(f"test ortamı üretim DB kimlik bilgisi değişkenleri taşıyor: {', '.join(present)}")
    leaked = sorted(k for k, v in environ.items() if v and v in secrets)
    if leaked:
        raise UnsafeTestTarget(f"test ortamında üretim gizli değeri var (anahtarlar): {', '.join(leaked)}")


def check_url(url: str, secrets: list):
    from sqlalchemy.engine import make_url
    from sqlalchemy.exc import ArgumentError
    if not url:
        raise UnsafeTestTarget("TEST_DATABASE_URL tanımlı değil")
    try:
        u = make_url(url)
    except ArgumentError:
        raise UnsafeTestTarget("TEST_DATABASE_URL ayrıştırılamadı") from None
    if not u.drivername.startswith("postgresql"):
        raise UnsafeTestTarget("TEST_DATABASE_URL PostgreSQL olmalı")
    if not (u.database or "").endswith("_test"):
        raise UnsafeTestTarget(f"test veritabanı adı '_test' ile bitmeli (gelen: {u.database!r})")
    if (u.host or "").lower() in PRODUCTION_HOSTS:
        raise UnsafeTestTarget(f"test hedefi üretim PostgreSQL servisi ({u.host!r})")
    if (u.host or "").lower() not in TEST_HOSTS:
        raise UnsafeTestTarget(f"test hedefi izinli geçici küme adlarından biri değil ({u.host!r}; izinli: "
                               f"{', '.join(sorted(TEST_HOSTS))}); bağlantı denenmedi")
    if u.password and u.password in secrets:
        raise UnsafeTestTarget("TEST_DATABASE_URL parolası üretim gizli değerine eşit")
    return u


def check_server(conn) -> None:
    """conn: hedef kümeye (herhangi bir veritabanı) açık SQLAlchemy bağlantısı."""
    from sqlalchemy import text
    name = conn.execute(text("SELECT current_setting('cluster_name')")).scalar()
    if name != EPHEMERAL_MARKER:
        raise UnsafeTestTarget(f"küme geçici test kümesi olarak işaretli değil (cluster_name={name!r}; beklenen "
                                f"{EPHEMERAL_MARKER!r})")
    dbs = [r[0] for r in conn.execute(text("SELECT datname FROM pg_database WHERE NOT datistemplate"))]
    foreign = sorted(d for d in dbs if d != "postgres" and not d.endswith("_test"))
    if foreign:
        raise UnsafeTestTarget(f"kümede test dışı veritabanı var: {', '.join(foreign)}")


def assert_safe_test_target(url: str | None = None, environ=None, secrets: list | None = None):
    """Tüm koşulları sırayla denetler; döner: doğrulanmış sqlalchemy URL. Sunucuya yalnız 'postgres' veritabanı
    üzerinden salt okunur sorgu ile bağlanır."""
    from sqlalchemy import create_engine
    environ = os.environ if environ is None else environ
    url = environ.get("TEST_DATABASE_URL", "") if url is None else url
    secrets = production_secrets() if secrets is None else secrets
    check_environment(environ, secrets)
    u = check_url(url, secrets)
    eng = create_engine(u.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with eng.connect() as c:
            check_server(c)
    finally:
        eng.dispose()
    return u
