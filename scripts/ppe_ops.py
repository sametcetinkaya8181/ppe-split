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
TABLES = ("cameras", "detector_runs", "incidents", "violation_events", "person_run_reports")
SNAPSHOT_NAME = re.compile(r"^[A-Za-z0-9_.-]+\.jpg$")


class OpsError(RuntimeError):
    pass


def compose(*args, stdin=None, stdout=subprocess.PIPE, check=True, text=True) -> subprocess.CompletedProcess:
    cmd = ["docker", "compose", *args]
    r = subprocess.run(cmd, cwd=ROOT, stdin=stdin, stdout=stdout, stderr=subprocess.PIPE, text=text)
    if check and r.returncode != 0:
        err = r.stderr if isinstance(r.stderr, str) else r.stderr.decode("utf-8", "replace")
        raise OpsError(f"komut başarısız ({' '.join(args[:3])}…): {err.strip()[-400:]}")
    return r


def psql(sql: str, database: str | None = None) -> list[list[str]]:
    """Container içinde psql -At; satırlar '|' ile ayrılır. database None -> POSTGRES_DB."""
    db = f"'{database}'" if database else '"$POSTGRES_DB"'
    script = f'psql -v ON_ERROR_STOP=1 -AtX -U "$POSTGRES_USER" -d {db} -c "$PPE_SQL"'
    r = compose("exec", "-T", "-e", f"PPE_SQL={sql}", "db", "sh", "-c", script)
    return [line.split("|") for line in r.stdout.splitlines() if line]


def main_db_name() -> str:
    return compose("exec", "-T", "db", "sh", "-c", 'printf %s "$POSTGRES_DB"').stdout


def db_facts(database: str | None = None) -> dict:
    """Sayımlar, Alembic revision ve snapshot referansları (salt okuma)."""
    counts = {t: int(psql(f"SELECT count(*) FROM {t}", database)[0][0]) for t in TABLES}
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
