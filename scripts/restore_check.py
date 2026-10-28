"""Yedeği İZOLE bir veritabanına geri yükleyip doğrular. Ana veritabanına ve data/snapshots'a DOKUNMAZ.

Kullanım:  python scripts/restore_check.py backups/ppe-<zaman> [--keep]

Adımlar: SHA256SUMS doğrulaması -> 'ppe_restore_verify' veritabanını (yalnızca bu adı) yeniden oluştur ->
pg_restore --no-owner --no-privileges -> sayımlar / revision / snapshot referansları manifest ile karşılaştırılır ->
her referanslı snapshot yedekte var ve özeti doğru mu -> ana DB'nin sayımları önce/sonra aynı mı ->
doğrulama veritabanı silinir (--keep ile kalır). Çıkış kodu 0 = PASS."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ppe_ops import ROOT, OpsError, compose, db_facts, fail, main_db_name, psql, sha256  # noqa: E402

VERIFY_DB = "ppe_restore_verify"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("backup", help="yedek klasörü (backups/ppe-...)")
    ap.add_argument("--keep", action="store_true", help=f"doğrulamadan sonra {VERIFY_DB} veritabanını silme")
    args = ap.parse_args()
    src = (ROOT / args.backup).resolve() if not Path(args.backup).is_absolute() else Path(args.backup)
    if not (src / "manifest.json").is_file():
        fail(f"manifest.json bulunamadı: {src}")
    manifest = json.loads((src / "manifest.json").read_text(encoding="utf-8"))
    checks, ok = [], True

    def check(name, cond, detail=""):
        nonlocal ok
        ok &= bool(cond)
        checks.append(("PASS" if cond else "FAIL", name, detail))

    # 1. dosya bütünlüğü
    bad = []
    for line in (src / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        digest, rel = line.split("  ", 1)
        p = (src / rel).resolve()
        if src not in p.parents or not p.is_file() or sha256(p) != digest:
            bad.append(rel)
    check("SHA256SUMS", not bad, f"{len(bad)} bozuk/eksik" if bad else "tüm dosyalar doğru")

    main_db = main_db_name()
    if main_db == VERIFY_DB:
        fail("güvenlik: ana veritabanı adı doğrulama veritabanıyla aynı")
    main_before = db_facts()
    try:
        psql(f'DROP DATABASE IF EXISTS "{VERIFY_DB}" WITH (FORCE)', "postgres")
        psql(f'CREATE DATABASE "{VERIFY_DB}"', "postgres")
        with open(src / "db.dump", "rb") as f:
            r = compose("exec", "-T", "db", "sh", "-c",
                        f'pg_restore --no-owner --no-privileges --exit-on-error -U "$POSTGRES_USER" -d {VERIFY_DB}',
                        stdin=f, text=False, check=False)
        check("pg_restore", r.returncode == 0, r.stderr.decode("utf-8", "replace").strip()[-300:])
        restored = db_facts(VERIFY_DB)
        check("alembic revision", restored["alembic_revision"] == manifest["alembic_revision"],
              f"{restored['alembic_revision']}")
        if manifest.get("db_changed_during_backup"):
            checks.append(("SKIP", "tablo sayımları", "yedek sırasında DB değişmişti (quiesce yok)"))
        else:
            check("tablo sayımları", restored["counts"] == manifest["counts"], json.dumps(restored["counts"]))
        refs = [x.split("/", 1)[1] for x in restored["snapshot_refs"]]
        snaps = manifest["snapshots"]["sha256"]
        missing = [n for n in refs if n not in snaps and n not in manifest["missing_referenced"]]
        bad_sum = [n for n in refs if n in snaps and sha256(src / "snapshots" / n) != snaps[n]]
        check("snapshot referansları", not missing and not bad_sum,
              f"{len(refs)} referans; yedekte eksik {len(missing)}, özeti bozuk {len(bad_sum)}; "
              f"yedek anında zaten eksik olan {len(manifest['missing_referenced'])}")
    except OpsError as exc:
        check("geri yükleme", False, str(exc))
    finally:
        if not args.keep:
            try:
                psql(f'DROP DATABASE IF EXISTS "{VERIFY_DB}" WITH (FORCE)', "postgres")
            except OpsError:
                pass
    main_after = db_facts()
    if main_before == main_after:
        check("ana veritabanı değişmedi (sayım/revision/snapshot ref)", True)
    else:                                    # geri yükleme yalnızca VERIFY_DB'ye yazar; fark canlı ingest'tir
        checks.append(("UYARI", "ana veritabanı doğrulama sırasında değişti", "canlı ingest çalışıyor olabilir"))

    for status, name, detail in checks:
        print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))
    print("SONUÇ:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
