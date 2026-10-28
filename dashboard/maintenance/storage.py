"""Depolama bakımı: sahipsiz snapshot raporu ve olay saklama (retention) analizi. VARSAYILAN: YALNIZCA RAPOR.

    python -m maintenance.storage snapshots [--min-age-hours 24] [--json]
    python -m maintenance.storage snapshots --quarantine --confirm <adet>
    python -m maintenance.storage retention [--json]

Snapshot kuralları (dosya DB commit'inden ÖNCE yazıldığı için commit başarısızsa sahipsiz dosya kalabilir):
- Yalnızca 'alert_<uuid>.jpg' (FAZ 3+ biçimi) ve '.tmp-<hex>.jpg' (yarım yazım artığı) değerlendirilir.
- Diğer adlar (ör. eski 'alert_<sıra>_camX_IDy.jpg') DOKUNULMAZ; yalnızca sayılır.
- Sembolik bağlar ve normal olmayan dosyalar izlenmez/taşınmaz.
- min-age (varsayılan 24 sa, en az 1 sa) içindeki dosyalar atlanır: commit'i süren bir yazım sahipsiz sanılmaz.
- Silme YOKTUR. --quarantine dosyaları DATA_DIR/quarantine/<zaman>/ altına TAŞIR (web'den servis edilen
  snapshots/ dışında); --confirm değeri bulunan sahipsiz dosya sayısına eşit olmalıdır. Taşımadan hemen önce her
  dosya DB'de yeniden aranır.
Retention: ham olayları SİLMEZ; ongoing hacmini ve olası bir politikanın etkisini raporlar."""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from db.session import get_session

ALERT_RE = re.compile(r"^alert_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.jpg$")
TMP_RE = re.compile(r"^\.tmp-[0-9a-f]{32}\.jpg$")
MIN_AGE_FLOOR_H = 1.0


def _referenced(names=None) -> set:
    q = "SELECT snapshot_path FROM violation_events WHERE snapshot_path IS NOT NULL"
    params = {}
    if names is not None:
        q += " AND snapshot_path = ANY(:p)"
        params["p"] = [f"snapshots/{n}" for n in names]
    with get_session() as s:
        return {r[0].split("/", 1)[1] for r in s.execute(text(q), params)}


def scan(data_dir: Path, min_age_hours: float, now: float | None = None) -> dict:
    snap_dir = (data_dir / "snapshots").resolve()
    now = time.time() if now is None else now
    min_age = max(min_age_hours, MIN_AGE_FLOOR_H) * 3600
    refs = _referenced()
    out = {"snapshot_dir": str(snap_dir), "min_age_hours": max(min_age_hours, MIN_AGE_FLOOR_H), "referenced": 0,
           "orphans": [], "tmp_leftovers": [], "too_new": 0, "legacy_kept": 0, "skipped_non_regular": 0,
           "referenced_missing": 0}
    present = set()
    for p in sorted(snap_dir.iterdir()) if snap_dir.is_dir() else []:
        if p.is_symlink() or not p.is_file():
            out["skipped_non_regular"] += 1
            continue
        present.add(p.name)
        if ALERT_RE.match(p.name):
            if p.name in refs:
                out["referenced"] += 1
            elif now - p.stat().st_mtime < min_age:
                out["too_new"] += 1
            else:
                out["orphans"].append(p.name)
        elif TMP_RE.match(p.name):
            if now - p.stat().st_mtime >= min_age:
                out["tmp_leftovers"].append(p.name)
            else:
                out["too_new"] += 1
        else:
            out["legacy_kept"] += 1
    out["referenced_missing"] = len(refs - present)
    return out


def quarantine(data_dir: Path, report: dict, confirm: int) -> list:
    names = report["orphans"] + report["tmp_leftovers"]
    if confirm != len(names):
        raise SystemExit(f"Onay uyuşmuyor: {len(names)} dosya bulundu, --confirm {confirm} verildi. Hiçbir şey taşınmadı.")
    if not names:
        return []
    snap_dir = (data_dir / "snapshots").resolve()
    still_ref = _referenced(names)                              # son anda commit olmuş olabilir
    dest = (data_dir / "quarantine" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")).resolve()
    if snap_dir in dest.parents or dest == snap_dir:
        raise SystemExit("güvenlik: karantina klasörü snapshots/ içinde olamaz")
    dest.mkdir(parents=True, exist_ok=False)
    moved = []
    for n in names:
        src = snap_dir / n
        if n in still_ref or src.is_symlink() or not src.is_file() or src.resolve().parent != snap_dir:
            continue
        os.replace(src, dest / n)
        moved.append(n)
    return moved


def retention() -> dict:
    with get_session() as s:
        by_type = {r[0]: r[1] for r in s.execute(text(
            "SELECT event_type, count(*) FROM violation_events GROUP BY 1"))}
        per_inc = s.execute(text(
            "SELECT coalesce(max(n), 0), coalesce(round(avg(n), 1), 0) FROM (SELECT count(*) n FROM violation_events "
            "WHERE event_type = 'ongoing' GROUP BY incident_id) x")).one()
        ages = {r[0]: r[1] for r in s.execute(text(
            "SELECT CASE WHEN occurred_at >= now() - interval '30 days' THEN '0-30g' "
            "WHEN occurred_at >= now() - interval '90 days' THEN '30-90g' ELSE '90g+' END, count(*) "
            "FROM violation_events WHERE event_type = 'ongoing' GROUP BY 1"))}
        size = s.execute(text("SELECT pg_total_relation_size('violation_events')")).scalar()
    return {"events_by_type": by_type, "ongoing_max_per_incident": per_inc[0], "ongoing_avg_per_incident":
            float(per_inc[1]), "ongoing_by_age": ages, "violation_events_bytes": size,
            "note": "Bu fazda otomatik silme YOK. Önerilen politika: docs/DATABASE.md 'Saklama'."}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m maintenance.storage")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("snapshots")
    sp.add_argument("--min-age-hours", type=float, default=24.0)
    sp.add_argument("--quarantine", action="store_true")
    sp.add_argument("--confirm", type=int, default=-1)
    sp.add_argument("--json", action="store_true")
    rp = sub.add_parser("retention")
    rp.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    data_dir = Path(os.getenv("DATA_DIR", "/data"))

    if args.cmd == "retention":
        res = retention()
    else:
        res = scan(data_dir, args.min_age_hours)
        if args.quarantine:
            res["quarantined"] = quarantine(data_dir, res, args.confirm)
        else:
            res["mode"] = "DRY RUN (hiçbir dosya değiştirilmedi)"
    if getattr(args, "json", False):
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        for k, v in res.items():
            print(f"{k}: {len(v)} {v[:20]}" if isinstance(v, list) else f"{k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
