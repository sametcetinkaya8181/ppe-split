"""FAZ 02-A: Docker container kimlik doğrulaması (yalnız Python standart kütüphanesi).

Kural: bir container'a `docker exec`, `docker stop` vb. ile HİÇBİR komut gönderilmeden ÖNCE kimliği yalnız Docker
meta verisinden (`docker inspect`: etiketler, bağlı volume'lar, tmpfs, komut satırı, yayınlanan portlar) doğrulanır.
Container ADINA veya ad benzerliğine göre seçim yapılmaz; hedefin içindeki veritabanına "kim olduğunu sormak" için
bağlanılmaz (FAZ 01'de başka bir projenin PostgreSQL'ine giden salt okunur sorgu bu yüzden mümkündü).

İki hedef türü vardır:
  - ana küme   : compose projesi + servis etiketi + beklenen adlandırılmış veri volume'u (ör. ppe-split_pgdata)
  - geçici küme: bu araçların oluşturduğu, `ppe.ephemeral-db=1` etiketli, compose projesine ait OLMAYAN, verisi
                 yalnız tmpfs'te, komut satırında `cluster_name=ppe-ephemeral-test` olan ve port yayınlamayan container

Saf fonksiyonlar (`*_problems`) birim testlidir; docker çağıran yardımcılar ince sarmalayıcılardır."""
import json
import subprocess

COMPOSE_PROJECT = "com.docker.compose.project"
COMPOSE_SERVICE = "com.docker.compose.service"
EPHEMERAL_LABEL = "ppe.ephemeral-db"          # değer "1"
OWNER_LABEL = "ppe.ephemeral-owner"           # oluşturan koşunun rastgele kimliği (temizlik yalnız buna göre)
PG_DATA = "/var/lib/postgresql/data"
EPHEMERAL_MARKER = "ppe-ephemeral-test"       # dashboard/tests/db_guard.py ile aynı


class IdentityError(RuntimeError):
    """Hedef container beklenen kimliği taşımıyor; ona hiçbir komut gönderilmedi."""


def labels(info: dict) -> dict:
    return (info.get("Config") or {}).get("Labels") or {}


def _short(info: dict) -> str:
    return (info.get("Id") or "?")[:12]


def running_problems(info: dict) -> list:
    return [] if (info.get("State") or {}).get("Running") else [f"container {_short(info)} çalışmıyor"]


def compose_member_problems(info: dict, project: str, service: str) -> list:
    """Container gerçekten `project` compose projesinin `service` servisi mi (yalnız etiketler)."""
    lb, out = labels(info), []
    if lb.get(COMPOSE_PROJECT) != project:
        out.append(f"compose projesi {lb.get(COMPOSE_PROJECT)!r} (beklenen {project!r})")
    if lb.get(COMPOSE_SERVICE) != service:
        out.append(f"compose servisi {lb.get(COMPOSE_SERVICE)!r} (beklenen {service!r})")
    return out


def main_db_problems(info: dict, project: str, service: str = "db", volume: str | None = None) -> list:
    """Ana PostgreSQL: compose etiketleri + veri dizinine bağlı TEK adlandırılmış volume `<project>_pgdata`."""
    out = compose_member_problems(info, project, service)
    volume = volume or f"{project}_pgdata"
    data = [m for m in info.get("Mounts") or [] if m.get("Destination") == PG_DATA]
    if not (len(data) == 1 and data[0].get("Type") == "volume" and data[0].get("Name") == volume):
        got = [(m.get("Type"), m.get("Name")) for m in data]
        out.append(f"veri dizini beklenen volume'a bağlı değil (beklenen {volume!r}, gelen {got})")
    if labels(info).get(EPHEMERAL_LABEL):
        out.append("geçici küme etiketi taşıyor; ana küme olamaz")
    return out


def ephemeral_problems(info: dict) -> list:
    """Geçici doğrulama kümesi: etiket + compose dışı + yalnız tmpfs veri + işaretli komut + port yok."""
    lb, out = labels(info), []
    if lb.get(EPHEMERAL_LABEL) != "1":
        out.append(f"'{EPHEMERAL_LABEL}=1' etiketi yok")
    if COMPOSE_PROJECT in lb:
        out.append(f"bir compose projesine ait ({lb.get(COMPOSE_PROJECT)!r})")
    persistent = [m.get("Destination") for m in info.get("Mounts") or [] if m.get("Type") in ("volume", "bind")]
    if persistent:
        out.append(f"kalıcı volume/bind bağlı: {persistent}")
    host = info.get("HostConfig") or {}
    if PG_DATA not in (host.get("Tmpfs") or {}):
        out.append("veri dizini tmpfs değil")
    cmd = (info.get("Config") or {}).get("Cmd") or []
    if f"cluster_name={EPHEMERAL_MARKER}" not in cmd:
        out.append("komut satırında geçici küme işareti yok")
    if any(host.get("PortBindings") or {}):
        out.append("host'a port yayınlıyor")
    return out


# ---------------------------------------------------------------- docker sarmalayıcıları (yalnız meta veri)
def inspect_container(ref: str) -> dict | None:
    r = subprocess.run(["docker", "inspect", "--type", "container", ref], capture_output=True, text=True)
    if r.returncode != 0:
        return None
    data = json.loads(r.stdout or "[]")
    return data[0] if data else None


def find_by_labels(project: str, service: str) -> list:
    """Etiketleri eşleşen container ID'leri (durmuş olanlar dahil). Ada BAKILMAZ."""
    r = subprocess.run(["docker", "ps", "-a", "-q", "--no-trunc", "--filter", f"label={COMPOSE_PROJECT}={project}",
                        "--filter", f"label={COMPOSE_SERVICE}={service}"], capture_output=True, text=True)
    if r.returncode != 0:
        raise IdentityError(f"docker ps başarısız: {r.stderr.strip()[-200:]}")
    return r.stdout.split()


def resolve_compose_service(project: str, service: str, *, require_running: bool = True, check=None) -> dict:
    """`project`/`service` etiketli TEK container'ı bulur ve doğrular. Döner: inspect sözlüğü.
    check: ek saf doğrulama fonksiyonu (info -> sorun listesi), ör. main_db_problems."""
    ids = find_by_labels(project, service)
    if len(ids) != 1:
        raise IdentityError(f"{project}/{service} etiketli {len(ids)} container var (tam olarak 1 beklenir)")
    info = inspect_container(ids[0])
    if info is None:
        raise IdentityError(f"{project}/{service} container'ı incelenemedi")
    problems = compose_member_problems(info, project, service) if check is None else check(info)
    if require_running:
        problems += running_problems(info)
    if problems:
        raise IdentityError(f"{project}/{service} kimliği doğrulanamadı: " + "; ".join(problems))
    return info


def verify_ephemeral(ref: str) -> dict:
    """Ad/ID verilen container'ın geçici küme olduğunu META VERİDEN doğrular; değilse IdentityError."""
    info = inspect_container(ref)
    if info is None:
        raise IdentityError("container bulunamadı")
    problems = ephemeral_problems(info) + running_problems(info)
    if problems:
        raise IdentityError(f"hedef geçici doğrulama kümesi değil ({_short(info)}): " + "; ".join(problems))
    return info
