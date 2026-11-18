"""PPE yedeği: PostgreSQL (pg_dump -Fc) + data/snapshots + manifest (revision, sayımlar, SHA-256).

Kullanım (proje kökünde):
    python scripts/backup.py                 # canlı sistem çalışırken
    python scripts/backup.py --quiesce       # yedek süresince dashboard durdurulur (önerilen; tutarlı kopya)
    python scripts/backup.py --out <proje-dışı-yedek-klasörü> --operator "<ad>"   # bakım penceresi (dashboard KAPALI)
    # --quiesce yalnız dashboard ÇALIŞIRKEN anlamlıdır; dashboard zaten kapalıysa --quiesce reddedilir (FAIL-CLOSED).

FAZ 02-A.1: yazmadan önce hedef diskte yer denetlenir (`--min-free-gb`, varsayılan 2): tahmini yedek boyutu
(veritabanı boyutu + snapshot klasörü) × 2 ayrıldıktan sonra bu kadar boş alan kalmayacaksa yedek ALINMAZ ve
klasör oluşturulmaz. Bakım öncesi salt okunur ön kontrol: scripts/backup_preflight.py.

FAZ 02-A: hedef ana PostgreSQL, `ppe-split` compose projesinin `db` servisi ETİKETLİ ve `ppe-split_pgdata`
volume'unu bağlamış TEK container olarak meta veriden doğrulanır (ad/çalışma klasörüyle seçilmez). Snapshot kaynağı
varsayılan olarak ana dashboard container'ının /data bağlamasıdır (betiğin çalıştığı worktree DEĞİL); klasör yoksa
yedek REDDEDİLİR (eskiden sessizce 0 snapshot'la tamamlanabiliyordu).

Çıktı: backups/ppe-<UTC zaman>/  (backups/ Git'e eklenmez)
    db.dump        pg_dump custom format (rollerin PAROLALARI içermez)
    snapshots/     data/snapshots altındaki normal dosyaların kopyası (sembolik bağlar atlanır)
    manifest.json  revision, tablo sayımları, snapshot referansları, dosya özetleri, tutarlılık notu
    SHA256SUMS     tüm dosyaların özetleri
    RESTORE.md     kısa geri yükleme talimatı

Tutarlılık: pg_dump kendi içinde tutarlıdır (tek snapshot). Snapshot klasörü dump'tan SONRA kopyalanır; dashboard
çalışıyorsa arada gelen yeni ihlalin JPEG'i kopyada olup DB'de olmayabilir (zararsız, sahipsiz dosya) — ters durum
(DB'de olup kopyada olmayan) yalnızca dosya sonradan silinirse oluşur ve manifest'te 'missing_referenced' olarak
raporlanır. Sayımlar dump'tan önce ve sonra alınır; farklıysa 'db_changed_during_backup' = true yazılır.
--quiesce bu pencereyi kapatır: dedektör olayları bu sırada kendi belleğinde bekletip dashboard dönünce gönderir
(DRAIN/kuyruk sınırları geçerlidir)."""
import argparse
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ppe_ops import (ROOT, SNAPSHOT_NAME, OpsError, capacity_problems, db_exec, db_facts, dir_bytes,  # noqa: E402
                     existing_parent, fail, main_snapshots_dir, psql, resolve_dashboard, set_target, sha256,
                     start_container, stop_container, target_info, write_json)

RESTORE_MD = """# Geri yükleme (özet)

Bu klasörü önce doğrulayın (ana veritabanına DOKUNMAZ, izole `ppe_restore_verify` veritabanı kullanır):

    python scripts/restore_check.py {name}

Gerçek geri yükleme (felaket durumunda) için docs/BACKUP_RESTORE.md'deki "Ana veritabanına geri yükleme"
adımlarını izleyin. Ana veritabanının üzerine yazmak geri alınamaz; önce yeni bir yedek alın.
"""


def _shown(p: Path) -> str:
    """Proje içindeyse göreli, dışındaysa (ör. şifreli/erişim kontrollü yerel klasör) mutlak yol."""
    try:
        return p.relative_to(ROOT).as_posix()
    except ValueError:
        return p.as_posix()


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="backups", help="yedek kök klasörü (varsayılan: backups)")
    ap.add_argument("--quiesce", action="store_true", help="yedek süresince dashboard servisini durdur")
    # FAZ 01 (yalnız doğrulama): yedek aracını ana kümeye dokunmadan sınamak için işaretli geçici küme + kaynak klasör
    ap.add_argument("--target-container", help="cluster_name=ppe-ephemeral-test işaretli geçici PostgreSQL container'ı")
    ap.add_argument("--snapshots-dir", help="snapshot kaynak klasörü (varsayılan: ana dashboard'un /data bağlaması)")
    ap.add_argument("--operator", default="", help="bakım kaydı için operatör adı/kısaltması (manifest'e yazılır)")
    ap.add_argument("--min-free-gb", type=float, default=2.0,
                    help="yedekten sonra hedef diskte kalması gereken en az boş alan (GiB); sağlanmazsa yedek alınmaz")
    args = ap.parse_args()
    if args.target_container and args.quiesce:
        fail("--quiesce yalnız ana yığında anlamlıdır; --target-container ile birlikte kullanılamaz")
    if args.target_container and not args.snapshots_dir:
        fail("--target-container ile --snapshots-dir zorunludur (ana snapshot klasörü doğrulama yedeğine karışmasın)")

    out_root = (ROOT / args.out).resolve()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = out_root / f"ppe-{stamp}"
    try:
        if args.target_container:
            set_target(args.target_container)           # işaretsiz container -> OpsError (FAIL-CLOSED)
        source = target_info()                          # ana küme: etiket + volume doğrulaması burada (komuttan önce)
        snap_src = Path(args.snapshots_dir).resolve() if args.snapshots_dir else main_snapshots_dir()
        dash = resolve_dashboard(require_running=True) if args.quiesce else None
        db_exec("pg_isready", "-q")
        db_bytes = int(psql("SELECT pg_database_size(current_database())")[0][0])
    except OpsError as exc:
        fail(f"PostgreSQL hazır değil veya hedef reddedildi: {exc}")
    if not snap_src.is_dir():
        fail(f"snapshot klasörü yok: {snap_src} (yanlış worktree/bağlama?). Yedek alınmadı.")
    # FAZ 02-A.1: kapasite FAIL-CLOSED — hiçbir şey yazılmadan ve dashboard durdurulmadan önce
    free = shutil.disk_usage(existing_parent(out_root)).free
    problems = capacity_problems(free, db_bytes + dir_bytes(snap_src), int(args.min_free_gb * 2**30))
    if problems:
        fail(f"{problems[0]}. Yedek alınmadı (klasör oluşturulmadı): {out_root}")
    dest.mkdir(parents=True, exist_ok=False)
    stopped = False
    try:
        if dash is not None:
            stop_container(dash["Id"])
            stopped = True
        before = db_facts()
        with open(dest / "db.dump", "wb") as f:
            r = db_exec("sh", "-c", 'pg_dump -Fc --no-password -U "$POSTGRES_USER" -d "$POSTGRES_DB"',
                        stdout=f, text=False, check=False)
        if r.returncode != 0:
            raise OpsError("pg_dump başarısız: " + r.stderr.decode("utf-8", "replace").strip()[-300:])
        after = db_facts()

        snap_dst = dest / "snapshots"
        snap_dst.mkdir()
        files, skipped = {}, []
        for p in sorted(snap_src.iterdir()):
            if p.is_symlink() or not p.is_file() or not SNAPSHOT_NAME.match(p.name) or p.name.startswith(".tmp-"):
                skipped.append(p.name)
                continue
            shutil.copy2(p, snap_dst / p.name)
            files[p.name] = sha256(snap_dst / p.name)
        refs = [r.split("/", 1)[1] for r in after["snapshot_refs"]]
        manifest = {
            "format": "ppe-backup/1",
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "quiesced": args.quiesce,
            "source": {**source, "snapshots_dir": str(snap_src)},
            "operator": args.operator,
            "alembic_revision": after["alembic_revision"],
            "postgres_version": db_exec("postgres", "--version").stdout.strip(),
            "counts": after["counts"],
            "db_changed_during_backup": before["counts"] != after["counts"],
            "counts_before_dump": before["counts"],
            "db_dump": {"file": "db.dump", "bytes": (dest / "db.dump").stat().st_size, "sha256": sha256(dest / "db.dump")},
            "snapshots": {"count": len(files), "bytes": sum((snap_dst / n).stat().st_size for n in files),
                          "sha256": files, "skipped": skipped},
            "snapshot_refs": len(refs),
            "missing_referenced": sorted(set(refs) - set(files)),
            "unreferenced_files": len(set(files) - set(refs)),
        }
        write_json(dest / "manifest.json", manifest)
        (dest / "RESTORE.md").write_text(RESTORE_MD.format(name=_shown(dest)), encoding="utf-8")
        lines = [f"{sha256(dest / n)}  {n}" for n in ("db.dump", "manifest.json", "RESTORE.md")]
        lines += [f"{h}  snapshots/{n}" for n, h in files.items()]
        (dest / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")
    except (OpsError, OSError, subprocess.SubprocessError) as exc:
        fail(f"yedek tamamlanamadı ({exc}); yarım klasör: {dest}")
    finally:
        if stopped:
            start_container(dash["Id"], check=False)

    print(f"Yedek: {_shown(dest)}")
    print(f"  revision {manifest['alembic_revision']}, sayımlar {manifest['counts']}")
    print(f"  db.dump {manifest['db_dump']['bytes']} bayt, snapshot {manifest['snapshots']['count']} dosya")
    if manifest["db_changed_during_backup"]:
        print("  UYARI: yedek sırasında DB değişti (canlı ingest). Tutarlı kopya için --quiesce kullanın.")
    if manifest["missing_referenced"]:
        print(f"  UYARI: DB'de referansı olup klasörde olmayan {len(manifest['missing_referenced'])} snapshot.")


if __name__ == "__main__":
    main()
