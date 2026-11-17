"""Yedekleme / geri yükleme betiklerinin ortak yardımcıları (yalnızca Python standart kütüphanesi).

Tüm PostgreSQL komutları `docker compose exec db` ile container İÇİNDE, yerel soket üzerinden POSTGRES_USER
olarak çalışır; host'a parola gerekmez, parola hiçbir komut satırına, dosyaya veya çıktıya yazılmaz.
Yalnızca bu projenin compose servisleri kullanılır (proje adı: ppe-split)."""
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Her revizyonda (0001+) bulunan çekirdek tablolar: sayımları her yedekte zorunlu.
TABLES = ("cameras", "detector_runs", "incidents", "violation_events", "person_run_reports")
# FAZ 01: sonraki migration'larla gelen tablolar. Yalnız VARSA sayılır (0006'daki sistemler bozulmaz):
# 0004 kimlik/oturum/denetim, 0007 insan incelemesi (incident_reviews + yalnız-ekleme geçmişi).
OPTIONAL_TABLES = ("app_users", "user_sessions", "audit_log", "incident_reviews", "incident_review_history")
SNAPSHOT_NAME = re.compile(r"^[A-Za-z0-9_.-]+\.jpg$")
EPHEMERAL_MARKER = "ppe-ephemeral-test"          # dashboard/tests/db_guard.py ile aynı
# Hedef: varsayılan bu projenin compose `db` servisi. FAZ 01: doğrulama işleri `cluster_name=ppe-ephemeral-test`
# işaretli GEÇİCİ bir container'a yönlendirilebilir (set_target); o zaman ana kümeye hiç komut gitmez.
_TARGET = {"container": None}


class OpsError(RuntimeError):
    pass


def set_target(container: str | None) -> None:
    """None: compose `db` (ana küme). Ad verilirse yalnız işaretli geçici küme kabul edilir (FAIL-CLOSED)."""
    _TARGET["container"] = container
    if container is not None:
        try:
            rows = psql("SELECT current_setting('cluster_name')", "postgres")
        except OpsError:
            _TARGET["container"] = None
            raise
        name = rows[0][0] if rows and rows[0] else ""       # boş cluster_name: psql -At satır üretmez
        if name != EPHEMERAL_MARKER:
            _TARGET["container"] = None
            raise OpsError(f"hedef container geçici doğrulama kümesi değil (cluster_name={name!r})")


def compose(*args, stdin=None, stdout=subprocess.PIPE, check=True, text=True) -> subprocess.CompletedProcess:
    cmd = ["docker", "compose", *args]
    r = subprocess.run(cmd, cwd=ROOT, stdin=stdin, stdout=stdout, stderr=subprocess.PIPE, text=text)
    if check and r.returncode != 0:
        err = r.stderr if isinstance(r.stderr, str) else r.stderr.decode("utf-8", "replace")
        raise OpsError(f"komut başarısız ({' '.join(args[:3])}…): {err.strip()[-400:]}")
    return r


def db_exec(*args, stdin=None, stdout=subprocess.PIPE, check=True, text=True, env=None) -> subprocess.CompletedProcess:
    """Veritabanı container'ında komut: hedef ayarlıysa `docker exec <geçici>`, değilse `docker compose exec db`.
    Komut container içinde çalışır; POSTGRES_USER/POSTGRES_DB container ortamından okunur (parola gerekmez)."""
    envs = [x for k, v in (env or {}).items() for x in ("-e", f"{k}={v}")]
    c = _TARGET["container"]
    if c is None:
        return compose("exec", "-T", *envs, "db", *args, stdin=stdin, stdout=stdout, check=check, text=text)
    cmd = ["docker", "exec", *(["-i"] if stdin is not None else []), *envs, c, *args]
    r = subprocess.run(cmd, cwd=ROOT, stdin=stdin, stdout=stdout, stderr=subprocess.PIPE, text=text)
    if check and r.returncode != 0:
        err = r.stderr if isinstance(r.stderr, str) else r.stderr.decode("utf-8", "replace")
        raise OpsError(f"komut başarısız (docker exec {c} {' '.join(args[:2])}…): {err.strip()[-400:]}")
    return r


def psql(sql: str, database: str | None = None) -> list[list[str]]:
    """Container içinde psql -At; satırlar '|' ile ayrılır. database None -> POSTGRES_DB."""
    db = f"'{database}'" if database else '"$POSTGRES_DB"'
    script = f'psql -v ON_ERROR_STOP=1 -AtX -U "$POSTGRES_USER" -d {db} -c "$PPE_SQL"'
    r = db_exec("sh", "-c", script, env={"PPE_SQL": sql})
    return [line.split("|") for line in r.stdout.splitlines() if line]


def main_db_name() -> str:
    return db_exec("sh", "-c", 'printf %s "$POSTGRES_DB"').stdout


def present_tables(database: str | None = None) -> tuple:
    """Sayılacak tablolar: çekirdek + bu veritabanında GERÇEKTEN bulunan isteğe bağlı tablolar (salt okuma)."""
    names = ",".join(f"'{t}'" for t in OPTIONAL_TABLES)
    have = {r[0] for r in psql("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' "
                               f"AND table_name IN ({names})", database)}
    return TABLES + tuple(t for t in OPTIONAL_TABLES if t in have)


def compare_counts(expected: dict, actual: dict) -> tuple:
    """Manifest'teki HER anahtar aynı olmalı (eski yedeklerde olmayan tablolar karşılaştırılmaz).
    Döner: (eşit_mi, farklı_anahtarlar, manifestte_olmayan_ek_tablolar)."""
    diff = sorted(k for k in expected if actual.get(k) != expected[k])
    return not diff, diff, sorted(set(actual) - set(expected))


def db_facts(database: str | None = None) -> dict:
    """Sayımlar, Alembic revision ve snapshot referansları (salt okuma)."""
    counts = {t: int(psql(f"SELECT count(*) FROM {t}", database)[0][0]) for t in present_tables(database)}
    rev = [r[0] for r in psql("SELECT version_num FROM alembic_version ORDER BY 1", database)]
    refs = sorted(r[0] for r in psql("SELECT snapshot_path FROM violation_events WHERE snapshot_path IS NOT NULL "
                                     "ORDER BY 1", database))
    return {"counts": counts, "alembic_revision": rev, "snapshot_refs": refs}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fail(msg: str) -> None:
    print(f"HATA: {msg}", file=sys.stderr, flush=True)
    sys.exit(1)
