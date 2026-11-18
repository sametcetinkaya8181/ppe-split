"""Yedekleme / geri yükleme / migration provası betiklerinin ortak yardımcıları (yalnız Python standart kütüphanesi).

Tüm PostgreSQL komutları `docker exec <doğrulanmış container ID>` ile container İÇİNDE, yerel soket üzerinden
POSTGRES_USER olarak çalışır; host'a parola gerekmez, parola hiçbir komut satırına, dosyaya veya çıktıya yazılmaz.

FAZ 02-A — hedef kimliği (docs/productization/PHASE_02A_DATA_SAFETY_REPORT.md §4):
  - Ana küme: compose projesi `ppe-split` + servis `db` ETİKETLİ tek container; veri dizininde `ppe-split_pgdata`
    volume'u bağlı olmalı. Container adına veya `docker compose`'un çalışma klasörüne göre çözümüne güvenilmez.
  - Geçici küme: `ppe.ephemeral-db=1` etiketli, compose dışı, tmpfs'li, işaretli ve port yayınlamayan container.
  - Her iki durumda kimlik `docker inspect` meta verisiyle doğrulanmadan container'a HİÇBİR komut gitmez; geçici
    kümede ek olarak `cluster_name` işareti (ikinci kontrol) sorgulanır.
  - `ephemeral_cluster()` doğrulama işleri için kendi geçici kümesini (internal ağ, rastgele parola, tmpfs) kurar ve
    yalnız kendi oluşturduğunu ID ile siler."""
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from docker_identity import (EPHEMERAL_LABEL, EPHEMERAL_MARKER, OWNER_LABEL, PG_DATA, IdentityError,  # noqa: E402
                             main_db_problems, resolve_compose_service, verify_ephemeral)

ROOT = Path(__file__).resolve().parent.parent
PROJECT = "ppe-split"                            # docker-compose.yml `name:` ile aynı (sabit proje kimliği)
DB_SERVICE, DASHBOARD_SERVICE = "db", "dashboard"
DB_VOLUME = f"{PROJECT}_pgdata"
# docker-compose.yml `db` servisiyle aynı imaj (digest sabit): geçici kümeler üretimle aynı PostgreSQL sürümünde
PG_IMAGE = "postgres:17@sha256:b8faeff7712663c2773a190181e1246e37e364b887611c04658c28b44e2408fe"
# Her revizyonda (0001+) bulunan çekirdek tablolar: sayımları her yedekte zorunlu.
TABLES = ("cameras", "detector_runs", "incidents", "violation_events", "person_run_reports")
# FAZ 01: sonraki migration'larla gelen tablolar. Yalnız VARSA sayılır (0006'daki sistemler bozulmaz):
# 0004 kimlik/oturum/denetim, 0007 insan incelemesi (incident_reviews + yalnız-ekleme geçmişi).
OPTIONAL_TABLES = ("app_users", "user_sessions", "audit_log", "incident_reviews", "incident_review_history")
SNAPSHOT_NAME = re.compile(r"^[A-Za-z0-9_.-]+\.jpg$")
# Doğrulanmış hedef: kind "main" | "ephemeral"; container tam ID (ad DEĞİL)
_TARGET = {"container": None, "kind": None}


class OpsError(RuntimeError):
    pass


def set_target(container: str | None) -> None:
    """None: ana küme (ilk komutta etiketlerden çözülür ve doğrulanır). Ad/ID verilirse yalnız geçici doğrulama
    kümesi kabul edilir: önce META VERİ doğrulanır (container'a komut gitmeden), sonra cluster_name işareti."""
    _TARGET.update(container=None, kind=None)
    if container is None:
        return
    try:
        info = verify_ephemeral(container)
    except IdentityError as exc:
        raise OpsError(str(exc)) from None
    _TARGET.update(container=info["Id"], kind="ephemeral")
    try:
        rows = psql("SELECT current_setting('cluster_name')", "postgres")
    except OpsError:
        _TARGET.update(container=None, kind=None)
        raise
    name = rows[0][0] if rows and rows[0] else ""            # boş cluster_name: psql -At satır üretmez
    if name != EPHEMERAL_MARKER:
        _TARGET.update(container=None, kind=None)
        raise OpsError(f"hedef container geçici doğrulama kümesi değil (cluster_name={name!r})")


def resolve_main_db() -> dict:
    """Ana PostgreSQL container'ı: etiketler + pgdata volume'u + çalışıyor. Döner: inspect sözlüğü."""
    try:
        return resolve_compose_service(PROJECT, DB_SERVICE,
                                       check=lambda i: main_db_problems(i, PROJECT, DB_SERVICE, DB_VOLUME))
    except IdentityError as exc:
        raise OpsError(str(exc)) from None


def resolve_dashboard(require_running: bool = False) -> dict:
    try:
        return resolve_compose_service(PROJECT, DASHBOARD_SERVICE, require_running=require_running)
    except IdentityError as exc:
        raise OpsError(str(exc)) from None


def main_snapshots_dir() -> Path:
    """Ana dashboard container'ının /data bağlamasından snapshot klasörü (betiğin çalıştığı worktree'den DEĞİL)."""
    info = resolve_dashboard()
    binds = [m for m in info.get("Mounts") or [] if m.get("Destination") == "/data" and m.get("Type") == "bind"]
    if len(binds) != 1:
        raise OpsError("dashboard /data bağlaması bulunamadı; --snapshots-dir ile açıkça verin")
    return Path(binds[0]["Source"]) / "snapshots"


def target_info() -> dict:
    """Manifest/denetim kaydı için hedefin kimliği (gizli bilgi yok)."""
    c = _db_container()
    out = {"kind": _TARGET["kind"], "container_id": c[:12]}
    if _TARGET["kind"] == "main":
        out.update(project=PROJECT, service=DB_SERVICE, volume=DB_VOLUME)
    return out


def _db_container() -> str:
    if _TARGET["container"] is None:
        _TARGET.update(container=resolve_main_db()["Id"], kind="main")
    return _TARGET["container"]


def _run(cmd, *, stdin=None, stdout=subprocess.PIPE, check=True, text=True, what="") -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, cwd=ROOT, stdin=stdin, stdout=stdout, stderr=subprocess.PIPE, text=text)
    if check and r.returncode != 0:
        err = r.stderr if isinstance(r.stderr, str) else r.stderr.decode("utf-8", "replace")
        raise OpsError(f"komut başarısız ({what or ' '.join(cmd[:3])}…): {err.strip()[-400:]}")
    return r


def db_exec(*args, stdin=None, stdout=subprocess.PIPE, check=True, text=True, env=None) -> subprocess.CompletedProcess:
    """Doğrulanmış veritabanı container'ında komut (`docker exec <ID>`). POSTGRES_USER/POSTGRES_DB container
    ortamından okunur (parola gerekmez)."""
    c = _db_container()
    envs = [x for k, v in (env or {}).items() for x in ("-e", f"{k}={v}")]
    cmd = ["docker", "exec", *(["-i"] if stdin is not None else []), *envs, c, *args]
    return _run(cmd, stdin=stdin, stdout=stdout, check=check, text=text, what=f"docker exec {c[:12]} {args[0]}")


def stop_container(cid: str) -> None:
    _run(["docker", "stop", cid], what=f"docker stop {cid[:12]}")


def start_container(cid: str, check: bool = True) -> subprocess.CompletedProcess:
    return _run(["docker", "start", cid], check=check, what=f"docker start {cid[:12]}")


@contextmanager
def ephemeral_cluster(prefix: str = "ppe-verify"):
    """Kendi geçici PostgreSQL kümesini kurar: internal ağ (dışarı/host erişimi yok), tmpfs veri, rastgele parola
    (yalnız container ortamında), cluster_name işareti, `ppe.ephemeral-db=1` + sahip etiketi. Hedefi buna ayarlar;
    çıkışta yalnız kendi container'ını ve ağını ID ile siler. Döner: {"container", "network", "user", "name"}."""
    owner = secrets.token_hex(6)
    name, net, user = f"{prefix}-{owner}", f"{prefix}-{owner}-net", "ppe_verify"
    env = {"POSTGRES_USER": user, "POSTGRES_PASSWORD": secrets.token_urlsafe(24), "POSTGRES_DB": "postgres"}
    lbl = ["--label", f"{EPHEMERAL_LABEL}=1", "--label", f"{OWNER_LABEL}={owner}"]
    cid = None
    _run(["docker", "network", "create", "--internal", *lbl, net], what="docker network create")
    try:
        r = subprocess.run(["docker", "run", "-d", "--name", name, "--network", net, *lbl, "--tmpfs", PG_DATA,
                            *[x for k in env for x in ("-e", k)], PG_IMAGE, "postgres", "-c",
                            f"cluster_name={EPHEMERAL_MARKER}", "-c", "fsync=off"],
                           capture_output=True, text=True, env={**os.environ, **env})
        if r.returncode != 0:
            raise OpsError(f"geçici küme başlatılamadı: {r.stderr.strip()[-300:]}")
        cid = r.stdout.strip()
        # TCP hazır olunca gerçek sunucu açıktır (initdb sırasındaki geçici sunucu yalnız soketten dinler)
        for _ in range(90):
            if subprocess.run(["docker", "exec", cid, "pg_isready", "-q", "-h", "127.0.0.1", "-U", user],
                              capture_output=True).returncode == 0:
                break
            time.sleep(1)
        else:
            raise OpsError("geçici küme hazır olmadı")
        set_target(cid)
        # password: yalnız bellekte; aynı internal ağdaki tek seferlik container'lara ortam değişkeniyle verilir
        yield {"container": cid, "network": net, "user": user, "name": name, "owner": owner,
               "password": env["POSTGRES_PASSWORD"]}
    finally:
        _TARGET.update(container=None, kind=None)
        if cid:
            subprocess.run(["docker", "rm", "-f", cid], capture_output=True)
        subprocess.run(["docker", "network", "rm", net], capture_output=True)


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


def table_digests(database: str | None = None, tables=TABLES) -> dict:
    """Satır içeriği özeti (md5, satır metni sırasıyla): migration öncesi/sonrası birebir karşılaştırma.
    Yalnız özet karşılaştırılır; satır içeriği dışarı yazılmaz."""
    return {t: psql(f"SELECT md5(coalesce(string_agg(x::text, E'\\n' ORDER BY x::text), '')) FROM {t} x",
                    database)[0][0] for t in tables}


def verify_sums(src: Path) -> list:
    """SHA256SUMS doğrulaması; döner: bozuk/eksik göreli yollar."""
    bad, root = [], src.resolve()
    for line in (src / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        digest, rel = line.split("  ", 1)
        p = (src / rel).resolve()
        if root not in p.parents or not p.is_file() or sha256(p) != digest:
            bad.append(rel)
    return bad


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def existing_parent(path: Path) -> Path:
    """Yolun var olan en yakın üst klasörü (henüz oluşturulmamış yedek kökünün diskini ölçmek için)."""
    p = Path(path).resolve()
    while not p.exists() and p != p.parent:
        p = p.parent
    return p


def capacity_problems(free: int, need: int, min_free: int, factor: float = 2.0) -> list:
    """Saf (birim testli), FAZ 02-A.1: yedek yazıldıktan sonra diskte en az `min_free` bayt kalmalı. `need` tahmini
    yedek boyutudur; `factor` düz yedek + şifreli kopya için ayrılan payı verir. Sorun yoksa boş liste."""
    reserve = int(max(need, 0) * factor)
    if free - reserve < min_free:
        return [f"boş alan yetersiz: boş {free / 2**30:.2f} GiB; ayrılacak ~{reserve / 2**30:.3f} GiB "
                f"(tahmin {need / 2**20:.1f} MiB × {factor:g}) + en az {min_free / 2**30:.2f} GiB kalmalı"]
    return []


def dir_bytes(path: Path) -> int:
    """Klasördeki normal dosyaların toplam boyutu (sembolik bağlar izlenmez)."""
    return sum(p.stat().st_size for p in Path(path).rglob("*") if p.is_file() and not p.is_symlink())


def write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fail(msg: str) -> None:
    print(f"HATA: {msg}", file=sys.stderr, flush=True)
    sys.exit(1)
