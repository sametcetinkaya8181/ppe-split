"""Yedeği İZOLE, geçici bir PostgreSQL kümesine geri yükleyip doğrular. Ana veritabanına ve data/snapshots'a DOKUNMAZ.

Kullanım:  python scripts/restore_check.py <yedek-klasörü> [--json rapor.json]
           python scripts/restore_check.py <yedek-klasörü> --target-container <geçici-küme>   (hazır küme)

FAZ 02-A: geri yükleme HER ZAMAN ana kümeden ayrı bir kümede yapılır. Varsayılan olarak araç kendi geçici kümesini
kurar (scripts/ppe_ops.py ephemeral_cluster: internal ağ, tmpfs, rastgele parola, `ppe.ephemeral-db=1` etiketi,
cluster_name işareti) ve sonunda yalnız onu siler. Eski "ana kümede `ppe_restore_verify` veritabanı oluştur" yolu
kaldırıldı (üretim kümesine yazıyordu). `--target-container` verilirse container önce meta veriden, sonra cluster_name
işaretinden doğrulanır; işaretsiz/etiketsiz hedefe hiçbir komut gönderilmez.

Adımlar: SHA256SUMS -> 'ppe_restore_verify' veritabanı -> pg_restore --no-owner --no-privileges -> sayımlar / revision /
snapshot referansları manifest ile karşılaştırılır -> her referanslı snapshot yedekte var ve özeti doğru mu.
Çıkış kodu 0 = PASS."""
import argparse
import json
import sys
from contextlib import nullcontext
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ppe_ops import (ROOT, OpsError, compare_counts, db_exec, db_facts, ephemeral_cluster, fail, psql,  # noqa: E402
                     set_target, sha256, target_info, verify_sums, write_json)

VERIFY_DB = "ppe_restore_verify"


def restore_into(src: Path, database: str) -> tuple[int, str]:
    psql(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)', "postgres")
    psql(f'CREATE DATABASE "{database}"', "postgres")
    with open(src / "db.dump", "rb") as f:
        r = db_exec("sh", "-c",
                    f'pg_restore --no-owner --no-privileges --exit-on-error -U "$POSTGRES_USER" -d {database}',
                    stdin=f, text=False, check=False)
    return r.returncode, r.stderr.decode("utf-8", "replace").strip()[-300:]


def check_backup(src: Path) -> tuple[bool, list, dict]:
    """Hedef (doğrulanmış geçici küme) ayarlıyken çağrılır. Döner: (ok, [(durum, ad, ayrıntı)], geri yüklenen olgular)."""
    manifest = json.loads((src / "manifest.json").read_text(encoding="utf-8"))
    checks, ok = [], True

    def check(name, cond, detail=""):
        nonlocal ok
        ok &= bool(cond)
        checks.append(("PASS" if cond else "FAIL", name, detail))

    bad = verify_sums(src)
    check("SHA256SUMS", not bad, f"{len(bad)} bozuk/eksik" if bad else "tüm dosyalar doğru")
    restored = {}
    try:
        rc, err = restore_into(src, VERIFY_DB)
        check("pg_restore", rc == 0, err)
        restored = db_facts(VERIFY_DB)
        check("alembic revision", restored["alembic_revision"] == manifest["alembic_revision"],
              f"{restored['alembic_revision']}")
        if manifest.get("db_changed_during_backup"):
            checks.append(("SKIP", "tablo sayımları", "yedek sırasında DB değişmişti (quiesce yok)"))
        else:
            same, diff, extra = compare_counts(manifest["counts"], restored["counts"])
            check("tablo sayımları", same, json.dumps(restored["counts"]) + (f"; farklı: {diff}" if diff else "")
                  + (f"; manifestte olmayan (bilgi): {extra}" if extra else ""))
        refs = [x.split("/", 1)[1] for x in restored["snapshot_refs"]]
        snaps = manifest["snapshots"]["sha256"]
        missing = [n for n in refs if n not in snaps and n not in manifest["missing_referenced"]]
        bad_sum = [n for n in refs if n in snaps and sha256(src / "snapshots" / n) != snaps[n]]
        cov = psql("SELECT coalesce(min(occurred_at)::text, ''), coalesce(max(occurred_at)::text, ''), "
                   "coalesce(max(ingested_at)::text, ''), (SELECT coalesce(max(started_at)::text, '') FROM detector_runs) "
                   "FROM violation_events", VERIFY_DB)[0]
        restored["coverage"] = {"first_event": cov[0], "last_event": cov[1], "last_ingested": cov[2],
                                "last_run_started": cov[3]}
        checks.append(("BİLGİ", "kapsanan olay aralığı", f"{cov[0]} … {cov[1]} (son ingest {cov[2]}, son run {cov[3]})"))
        check("snapshot referansları", not missing and not bad_sum,
              f"{len(refs)} referans; yedekte eksik {len(missing)}, özeti bozuk {len(bad_sum)}; "
              f"yedek anında zaten eksik olan {len(manifest['missing_referenced'])}")
    except OpsError as exc:
        check("geri yükleme", False, str(exc))
    return ok, checks, restored


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("backup", help="yedek klasörü (backups/ppe-... veya proje dışı mutlak yol)")
    ap.add_argument("--target-container", help="hazır geçici küme (ppe.ephemeral-db=1 + cluster_name işareti)")
    ap.add_argument("--json", help="sonuçları bu JSON dosyasına da yaz (sayımlar/durumlar; satır içeriği yok)")
    args = ap.parse_args()
    src = Path(args.backup) if Path(args.backup).is_absolute() else (ROOT / args.backup)
    src = src.resolve()
    if not (src / "manifest.json").is_file():
        fail(f"manifest.json bulunamadı: {src}")
    try:
        ctx = nullcontext() if args.target_container else ephemeral_cluster("ppe-restore-check")
        with ctx:
            if args.target_container:
                set_target(args.target_container)
            target = target_info()
            if target["kind"] != "ephemeral":               # savunma: ana kümeye asla geri yükleme yapılmaz
                fail("güvenlik: hedef geçici küme değil")
            ok, checks, restored = check_backup(src)
            if args.target_container:
                try:
                    psql(f'DROP DATABASE IF EXISTS "{VERIFY_DB}" WITH (FORCE)', "postgres")
                except OpsError:
                    pass
    except OpsError as exc:
        fail(f"hedef reddedildi veya geçici küme kurulamadı: {exc}")
    checks.append(("BİLGİ", "hedef", f"geçici küme {target['container_id']} (ana kümeye bağlanılmadı)"))
    for status, name, detail in checks:
        print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))
    print("SONUÇ:", "PASS" if ok else "FAIL")
    if args.json:
        write_json(Path(args.json), {"backup": src.name, "result": "PASS" if ok else "FAIL",
                                     "checks": [{"status": s, "name": n, "detail": d} for s, n, d in checks],
                                     "restored_counts": restored.get("counts"),
                                     "restored_revision": restored.get("alembic_revision"),
                                     "coverage": restored.get("coverage")})
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
