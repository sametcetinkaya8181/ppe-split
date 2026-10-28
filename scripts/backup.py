"""PPE yedeği: PostgreSQL (pg_dump -Fc) + data/snapshots + manifest (revision, sayımlar, SHA-256).

Kullanım (proje kökünde):
    python scripts/backup.py                 # canlı sistem çalışırken
    python scripts/backup.py --quiesce       # yedek süresince dashboard durdurulur (önerilen; tutarlı kopya)

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
from ppe_ops import (ROOT, SNAPSHOT_NAME, OpsError, compose, db_facts, fail, sha256, write_json)  # noqa: E402

RESTORE_MD = """# Geri yükleme (özet)

Bu klasörü önce doğrulayın (ana veritabanına DOKUNMAZ, izole `ppe_restore_verify` veritabanı kullanır):

    python scripts/restore_check.py {name}

Gerçek geri yükleme (felaket durumunda) için docs/BACKUP_RESTORE.md'deki "Ana veritabanına geri yükleme"
adımlarını izleyin. Ana veritabanının üzerine yazmak geri alınamaz; önce yeni bir yedek alın.
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="backups", help="yedek kök klasörü (varsayılan: backups)")
    ap.add_argument("--quiesce", action="store_true", help="yedek süresince dashboard servisini durdur")
    args = ap.parse_args()

    out_root = (ROOT / args.out).resolve()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = out_root / f"ppe-{stamp}"
    snap_src = ROOT / "data" / "snapshots"
    try:
        compose("exec", "-T", "db", "pg_isready", "-q")
    except OpsError:
        fail("PostgreSQL (db servisi) hazır değil: docker compose ps")
    dest.mkdir(parents=True, exist_ok=False)
    stopped = False
    try:
        if args.quiesce:
            compose("stop", "dashboard")
            stopped = True
        before = db_facts()
        with open(dest / "db.dump", "wb") as f:
            r = compose("exec", "-T", "db", "sh", "-c", 'pg_dump -Fc --no-password -U "$POSTGRES_USER" -d "$POSTGRES_DB"',
                        stdout=f, text=False, check=False)
        if r.returncode != 0:
            raise OpsError("pg_dump başarısız: " + r.stderr.decode("utf-8", "replace").strip()[-300:])
        after = db_facts()

        snap_dst = dest / "snapshots"
        snap_dst.mkdir()
        files, skipped = {}, []
        if snap_src.is_dir():
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
            "alembic_revision": after["alembic_revision"],
            "postgres_version": compose("exec", "-T", "db", "postgres", "--version").stdout.strip(),
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
        (dest / "RESTORE.md").write_text(RESTORE_MD.format(name=dest.relative_to(ROOT).as_posix()), encoding="utf-8")
        lines = [f"{sha256(dest / n)}  {n}" for n in ("db.dump", "manifest.json", "RESTORE.md")]
        lines += [f"{h}  snapshots/{n}" for n, h in files.items()]
        (dest / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")
    except (OpsError, OSError, subprocess.SubprocessError) as exc:
        fail(f"yedek tamamlanamadı ({exc}); yarım klasör: {dest}")
    finally:
        if stopped:
            compose("start", "dashboard", check=False)

    print(f"Yedek: {dest.relative_to(ROOT)}")
    print(f"  revision {manifest['alembic_revision']}, sayımlar {manifest['counts']}")
    print(f"  db.dump {manifest['db_dump']['bytes']} bayt, snapshot {manifest['snapshots']['count']} dosya")
    if manifest["db_changed_during_backup"]:
        print("  UYARI: yedek sırasında DB değişti (canlı ingest). Tutarlı kopya için --quiesce kullanın.")
    if manifest["missing_referenced"]:
        print(f"  UYARI: DB'de referansı olup klasörde olmayan {len(manifest['missing_referenced'])} snapshot.")


if __name__ == "__main__":
    main()
