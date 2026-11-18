"""FAZ 02-A.1: üretim yedeği öncesi SALT OKUNUR ön kontrol (bakım prosedürü adım 2-3, docs/BACKUP_RESTORE.md).

  python scripts/backup_preflight.py --out C:\\Users\\PC\\ppe-local-backups\\prod                 # DB kapalıyken
  python scripts/backup_preflight.py --out ... --stage db-up                                     # yalnız db açıldıktan sonra
  python scripts/backup_preflight.py --out ... --age-recipients <alıcı-dosyası> --require-encryption --json pf.json

Hiçbir container'a komut GÖNDERMEZ, hiçbir container'ı başlatmaz/durdurmaz, veritabanına bağlanmaz, dosya yazmaz
(yalnız --json verilirse o raporu yazar). Kullandığı kaynaklar: `docker ps`/`docker inspect`/`docker system df`
meta verisi, dosya sistemi boyutları, outbox `*.status.json` dosyaları (SQLite AÇILMAZ), süreç listesi, ACL.

Kontroller (FAIL varsa çıkış kodu 1 — bakım penceresine GEÇİLMEZ):
  disk          yedek hedefinin diskinde: tahmini yedek (pgdata volume + snapshot klasörü) × 2 ayrıldıktan sonra
                en az --min-free-gb kalmalı; Docker veri diskinde (VHDX) de en az --min-free-gb boş olmalı
  main-db       ppe-split/db etiketli TEK container + ppe-split_pgdata volume'u (docker_identity.main_db_problems);
                --stage pre-start: KAPALI olmalı; --stage db-up: çalışıyor ve healthy olmalı
  compose       ppe-split projesinde beklenen servisler (db, db-roles, migrate, dashboard) birer tane; fazlası UYARI
  dashboard     KAPALI olmalı (yedek --quiesce gerektirmeden tutarlı olur)
  detector      host'ta detector.py çalıştıran python süreci olmamalı
  outbox        üretim outbox'ında pending_count = 0 (dead/quarantine bilgi olarak)
  snapshots     dashboard /data bağlamasındaki snapshot klasörü var
  out-dir       yedek hedefi bir Git çalışma ağacının içinde OLMAMALI; ACL'de Everyone/Users/Authenticated Users/
                Guests izni OLMAMALI; OneDrive altındaysa UYARI
  encryption    age (veya yedek olarak gpg) mevcut mu; --age-recipients verilirse yalnız `age1…` açık anahtarları
                içermeli (özel anahtar `AGE-SECRET-KEY-…` saha makinesinde bulunursa FAIL); --require-encryption ile
                araç/alıcı yoksa FAIL
  tools         bu araçların çalıştığı worktree'nin commit SHA'sı ve temizliği (kayıt için; kirliyse UYARI)"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from docker_identity import (COMPOSE_PROJECT, IdentityError, inspect_container, labels,  # noqa: E402
                             main_db_problems, resolve_compose_service)
from ppe_ops import (DASHBOARD_SERVICE, DB_SERVICE, DB_VOLUME, PROJECT, ROOT, OpsError, capacity_problems,  # noqa: E402
                     dir_bytes, existing_parent, main_snapshots_dir, write_json)

EXPECTED_SERVICES = ("db", "db-roles", "migrate", "dashboard")
# Geniş erişimli yerel gruplar (dil bağımsız SID): Everyone, Users, Authenticated Users, Guests
BROAD_SIDS = {"S-1-1-0": "Everyone", "S-1-5-32-545": "Users", "S-1-5-11": "Authenticated Users",
              "S-1-5-32-546": "Guests"}
GIT_GPG = Path(r"C:\Program Files\Git\usr\bin\gpg.exe")
_UNITS = {"B": 1, "KB": 10**3, "MB": 10**6, "GB": 10**9, "TB": 10**12}


# ---------------------------------------------------------------- saf yardımcılar (birim testli)
def parse_docker_size(text: str) -> int | None:
    """`docker system df` boyutu ("134.7MB", "242.5kB", "0B") -> bayt (Docker ondalık birim kullanır)."""
    m = re.fullmatch(r"\s*([0-9.]+)\s*([kKMGT]?B)\s*", text or "")
    if not m:
        return None
    return int(float(m.group(1)) * _UNITS[m.group(2).upper()])


def broad_acl_problems(entries: list) -> list:
    """entries: [(sid, access_type)] -> geniş gruplara verilmiş Allow izinleri."""
    return [f"{BROAD_SIDS[sid]} ({sid}) erişimi var" for sid, kind in entries
            if sid in BROAD_SIDS and str(kind).lower() == "allow"]


def recipients_problems(text: str) -> list:
    """age alıcı dosyası yalnız açık anahtar (age1…) satırları içermeli; özel anahtar saha makinesinde olamaz."""
    out, keys = [], 0
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "AGE-SECRET-KEY-" in line.upper():
            return [f"satır {n}: ÖZEL ANAHTAR (AGE-SECRET-KEY) — saha makinesinde bulunmamalı"]
        if re.fullmatch(r"age1[02-9ac-hj-np-z]{58}", line):
            keys += 1
        else:
            out.append(f"satır {n}: geçerli bir age1… açık anahtarı değil")
    if keys == 0 and not out:
        out.append("alıcı dosyasında açık anahtar yok")
    return out


def inside_git_tree(path: Path) -> Path | None:
    p = existing_parent(path)
    for q in (p, *p.parents):
        if (q / ".git").exists():
            return q
    return None


def verdict(checks: list) -> str:
    return "FAIL" if any(c["status"] == "FAIL" for c in checks) else "PASS"


# ---------------------------------------------------------------- salt okunur toplayıcılar
def _ps(script: str) -> str:
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True,
                       text=True, timeout=60)
    if r.returncode != 0:
        raise OSError(r.stderr.strip()[-200:])
    return r.stdout


def detector_processes() -> list:
    if os.name == "nt":
        out = _ps("Get-CimInstance Win32_Process -Filter \"Name LIKE 'python%'\" | "
                  "Where-Object { $_.CommandLine -match 'detector\\.py' } | "
                  "ForEach-Object { $_.ProcessId }")
    else:
        out = subprocess.run(["pgrep", "-f", r"python.*detector\.py"], capture_output=True, text=True).stdout
    return [int(x) for x in out.split() if x.strip().isdigit()]


def acl_entries(path: Path) -> list:
    if os.name != "nt":
        return []
    # yol ortam değişkeniyle verilir (komut satırına gömülmez: tırnak/enjeksiyon yok); SID dil bağımsızdır
    r = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command",
         "(Get-Acl -LiteralPath $env:PPE_ACL_PATH).Access | ForEach-Object { "
         "$s = try { $_.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value } "
         "catch { [string]$_.IdentityReference }; \"$s|$($_.AccessControlType)\" }"],
        capture_output=True, text=True, timeout=60, env={**os.environ, "PPE_ACL_PATH": str(path)})
    if r.returncode != 0:
        raise OSError(r.stderr.strip()[-200:])
    return [tuple(line.strip().split("|", 1)) for line in r.stdout.splitlines() if "|" in line]


def volume_bytes(name: str) -> int | None:
    r = subprocess.run(["docker", "system", "df", "-v", "--format", "{{json .Volumes}}"], capture_output=True,
                       text=True, timeout=120)
    if r.returncode != 0:
        return None
    for v in json.loads(r.stdout or "[]") or []:
        if v.get("Name") == name:
            return parse_docker_size(v.get("Size", ""))
    return None


def project_containers(project: str) -> list:
    r = subprocess.run(["docker", "ps", "-a", "-q", "--no-trunc", "--filter", f"label={COMPOSE_PROJECT}={project}"],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise OSError(r.stderr.strip()[-200:])
    return [i for i in (inspect_container(x) for x in r.stdout.split()) if i]


def docker_data_dir() -> Path | None:
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "Docker" / "wsl"
    return base if base.is_dir() else None


# ---------------------------------------------------------------- kontroller
def run(args) -> list:
    checks = []

    def add(name, status, detail=""):
        checks.append({"name": name, "status": status, "detail": detail})

    # main-db / compose / dashboard
    db = None
    try:
        db = resolve_compose_service(PROJECT, DB_SERVICE, require_running=False,
                                     check=lambda i: main_db_problems(i, PROJECT, DB_SERVICE, DB_VOLUME))
        st = db.get("State") or {}
        state = (f"{st.get('Status')}, health {(st.get('Health') or {}).get('Status')}" if st.get("Running")
                 else f"{st.get('Status')} (FinishedAt {st.get('FinishedAt')})")
        add("main-db kimliği", "PASS", f"{db['Id'][:12]} {PROJECT}/{DB_SERVICE} + volume {DB_VOLUME}; {state}")
        if args.stage == "pre-start":
            add("main-db durumu (pre-start)", "PASS" if not st.get("Running") else "FAIL",
                "kapalı" if not st.get("Running") else "beklenmedik şekilde ÇALIŞIYOR — kim/ne başlattı, incele")
        else:
            healthy = st.get("Running") and (st.get("Health") or {}).get("Status") == "healthy"
            add("main-db durumu (db-up)", "PASS" if healthy else "FAIL", "çalışıyor ve healthy" if healthy else state)
    except IdentityError as exc:
        add("main-db kimliği", "FAIL", str(exc))
    try:
        cons = project_containers(PROJECT)
        svcs = [labels(i).get("com.docker.compose.service") for i in cons]
        missing = [s for s in EXPECTED_SERVICES if svcs.count(s) != 1]
        extra = sorted({s for s in svcs if s not in EXPECTED_SERVICES})
        add("compose servisleri", "FAIL" if missing else "PASS",
            f"{len(cons)} container; " + (f"eksik/çift: {missing}" if missing else "db, db-roles, migrate, dashboard"))
        if extra:
            add("compose fazla servis", "WARN", f"{extra} (ör. -p verilmeden çalışmış test servisleri)")
        running = [f"{labels(i).get('com.docker.compose.service')}" for i in cons if (i.get("State") or {}).get("Running")]
        allowed = set() if args.stage == "pre-start" else {DB_SERVICE}
        bad = [s for s in running if s not in allowed]
        add("çalışan ppe-split servisleri", "FAIL" if bad else "PASS",
            f"çalışan: {running or 'yok'}; izinli: {sorted(allowed) or 'yok'}")
    except OSError as exc:
        add("compose servisleri", "FAIL", f"docker okunamadı: {exc}")

    # snapshots + outbox (üretim worktree'si compose etiketinden)
    snaps_bytes = 0
    try:
        snaps = main_snapshots_dir()
        if snaps.is_dir():
            files = [p for p in snaps.iterdir() if p.is_file()]
            snaps_bytes = dir_bytes(snaps)
            add("snapshot klasörü", "PASS", f"{snaps} — {len(files)} dosya, {snaps_bytes / 2**20:.1f} MiB")
        else:
            add("snapshot klasörü", "FAIL", f"yok: {snaps}")
    except OpsError as exc:
        add("snapshot klasörü", "FAIL", str(exc))
    try:
        dash = resolve_compose_service(PROJECT, DASHBOARD_SERVICE, require_running=False)
        wd = labels(dash).get("com.docker.compose.project.working_dir")
        outbox = Path(wd) / "detector" / "outbox" if wd else None
        if outbox and outbox.is_dir():
            tot = {"pending_count": 0, "dead_letter_count": 0, "quarantine_count": 0}
            n = 0
            for f in sorted(outbox.glob("*.status.json")):
                d = json.loads(f.read_text(encoding="utf-8"))
                n += 1
                for k in tot:
                    tot[k] += int(d.get(k) or 0)
            add("outbox bekleyen", "PASS" if tot["pending_count"] == 0 else "FAIL",
                f"{outbox} — {n} kamera; pending {tot['pending_count']}, dead {tot['dead_letter_count']}, "
                f"quarantine {tot['quarantine_count']}")
        else:
            add("outbox bekleyen", "WARN", f"üretim outbox klasörü bulunamadı ({outbox})")
    except (IdentityError, OSError, ValueError) as exc:
        add("outbox bekleyen", "FAIL", f"okunamadı: {exc}")
    try:
        procs = detector_processes()
        add("dedektör süreçleri", "FAIL" if procs else "PASS", f"PID {procs}" if procs else "detector.py çalışmıyor")
    except (OSError, subprocess.SubprocessError) as exc:
        add("dedektör süreçleri", "FAIL", f"süreç listesi okunamadı: {exc}")

    # disk
    out = Path(args.out)
    vol = volume_bytes(DB_VOLUME)
    need = (vol or 512 * 2**20) + snaps_bytes
    base = existing_parent(out)
    free = shutil.disk_usage(base).free
    prob = capacity_problems(free, need, int(args.min_free_gb * 2**30))
    add("disk (yedek hedefi)", "FAIL" if prob else "PASS",
        (prob[0] if prob else f"boş {free / 2**30:.2f} GiB ({base.anchor}); tahmini yedek <= {need / 2**20:.1f} MiB "
         f"(pgdata volume {'?' if vol is None else f'{vol / 2**20:.1f} MiB'} + snapshot)"))
    ddir = docker_data_dir()
    if ddir:
        dfree = shutil.disk_usage(ddir).free
        ok = dfree >= args.min_free_gb * 2**30
        vhdx = sum(p.stat().st_size for p in ddir.rglob("*.vhdx"))
        add("disk (Docker VHDX)", "PASS" if ok else "FAIL",
            f"{ddir.anchor} boş {dfree / 2**30:.2f} GiB; VHDX toplam {vhdx / 2**30:.2f} GiB (dinamik büyür, "
            "silinen imajlar kendiliğinden geri vermez)")

    # out-dir
    git_root = inside_git_tree(out)
    add("yedek hedefi Git dışı", "FAIL" if git_root else "PASS",
        f"{out} bir Git çalışma ağacının içinde: {git_root}" if git_root else str(out))
    onedrive = [os.environ.get(k) for k in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial") if os.environ.get(k)]
    if any(str(out.resolve()).lower().startswith(str(Path(d).resolve()).lower()) for d in onedrive):
        add("yedek hedefi OneDrive dışı", "WARN", "OneDrive eşitlemesi düz yedeği buluta taşır")
    try:
        entries = acl_entries(base)
        prob = broad_acl_problems(entries)
        add(f"ACL ({base})", "FAIL" if prob else "PASS", "; ".join(prob) if prob else f"{len(entries)} giriş, geniş grup yok")
    except (OSError, subprocess.SubprocessError) as exc:
        add(f"ACL ({base})", "FAIL", f"okunamadı: {exc}")

    # encryption
    age = shutil.which("age") or (args.age if args.age and Path(args.age).is_file() else None)
    gpg = shutil.which("gpg") or (str(GIT_GPG) if GIT_GPG.is_file() else None)
    enc_ok = bool(age)
    add("şifreleme aracı", "PASS" if age else ("FAIL" if args.require_encryption else "WARN"),
        f"age: {age or 'YOK'}; gpg (yedek seçenek): {gpg or 'YOK'}")
    if args.age_recipients:
        rp = Path(args.age_recipients)
        if rp.is_file():
            txt = rp.read_text(encoding="utf-8", errors="replace")
            prob = recipients_problems(txt)
            enc_ok &= not prob
            add("age alıcı dosyası", "FAIL" if prob else "PASS",
                "; ".join(prob) if prob else f"SHA-256 {hashlib.sha256(rp.read_bytes()).hexdigest()} (kurulum "
                "kaydındaki özetle karşılaştırın)")
        else:
            enc_ok = False
            add("age alıcı dosyası", "FAIL", f"yok: {rp}")
    elif args.require_encryption:
        enc_ok = False
        add("age alıcı dosyası", "FAIL", "--require-encryption verildi ama --age-recipients yok")
    add("şifreli yedek hazır", "PASS" if enc_ok and args.age_recipients else "WARN",
        "evet" if enc_ok and args.age_recipients else "hayır — düz yedek yalnız ACL'li klasörde kalır (risk kaydı)")

    # tools
    try:
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True).stdout
        add("araç kaynağı", "WARN" if dirty.strip() else "PASS",
            f"{ROOT} @ {sha[:12]}" + (f" (kirli: {len(dirty.splitlines())} yol)" if dirty.strip() else " (temiz)"))
    except OSError as exc:
        add("araç kaynağı", "WARN", str(exc))
    return checks


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True, help="yedeğin yazılacağı proje dışı kök klasör")
    ap.add_argument("--stage", choices=("pre-start", "db-up"), default="pre-start")
    ap.add_argument("--min-free-gb", type=float, default=5.0)
    ap.add_argument("--age", help="age.exe yolu (PATH'te değilse)")
    ap.add_argument("--age-recipients", help="age alıcı (AÇIK anahtar) dosyası")
    ap.add_argument("--require-encryption", action="store_true")
    ap.add_argument("--json", help="sonuçları JSON olarak da yaz")
    a = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):         # Windows konsolu (cp1254): yazdırılamayan karakter çökertmesin
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    checks = run(a)
    for c in checks:
        print(f"[{c['status']}] {c['name']}" + (f" — {c['detail']}" if c["detail"] else ""))
    v = verdict(checks)
    print("ÖN KONTROL:", v)
    if a.json:
        write_json(Path(a.json), {"stage": a.stage, "out": a.out, "verdict": v, "checks": checks})
    return 0 if v == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
