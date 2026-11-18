"""Çökmüş run uzlaştırması (FAZ 7). VARSAYILAN: YALNIZCA RAPOR (dry run). Veri silinmez.

    python -m maintenance.runs reconcile [--stale-minutes 60]
    python -m maintenance.runs reconcile --apply --confirm <adet> [--stale-minutes 60]

Aday: status='running' VE rapor metadata'sı yok VE son canlılık (last_seen_at, yoksa started_at) eşikten eski.
Dedektör canlıyken en geç HEARTBEAT_SEC (30 sn) / olay başına canlılık yazar; eşik en az 10 dk zorunludur.
Uygulama: status='abandoned' (sahte 'finished' YOK), finished_at = son canlılık kanıtı, reconciled_at/_reason; her run
için audit_log 'run_reconcile'. Ölçütler UPDATE içinde yeniden denetlenir (bu arada canlanan run değişmez).
Geri dönüş: aynı run'dan canlılık gelirse 'running', rapor gelirse rapordaki duruma geçer (repository)."""
import argparse
import json
import sys
from datetime import timedelta

from sqlalchemy import text

from auth import store
from db.session import get_session

MIN_STALE_MINUTES = 10
CANDIDATES = """
SELECT r.id, r.camera_id, r.source_label, r.started_at, r.last_seen_at,
       round(extract(epoch FROM now() - coalesce(r.last_seen_at, r.started_at)) / 60) AS idle_minutes,
       (SELECT count(*) FROM violation_events e WHERE e.run_id = r.id) AS events
FROM detector_runs r
WHERE r.status = 'running' AND r.report_metadata IS NULL
  AND coalesce(r.last_seen_at, r.started_at) < now() - :stale
ORDER BY r.started_at
"""
APPLY = """
UPDATE detector_runs SET status = 'abandoned',
       finished_at = greatest(started_at, coalesce(last_seen_at, started_at)),
       reconciled_at = now(), reconcile_reason = :reason
WHERE id = ANY(:ids) AND status = 'running' AND report_metadata IS NULL
  AND coalesce(last_seen_at, started_at) < now() - :stale
RETURNING id
"""


def candidates(s, stale_minutes: float) -> list[dict]:
    return [dict(r) for r in s.execute(text(CANDIDATES), {"stale": timedelta(minutes=stale_minutes)}).mappings()]


def apply(s, stale_minutes: float, confirm: int) -> list:
    found = candidates(s, stale_minutes)
    if confirm != len(found):
        raise SystemExit(f"Onay uyuşmuyor: {len(found)} aday bulundu, --confirm {confirm} verildi. Hiçbir şey "
                         f"değiştirilmedi.")
    reason = f"no_heartbeat_{int(stale_minutes)}m"
    done = [r[0] for r in s.execute(text(APPLY), {"ids": [c["id"] for c in found], "reason": reason,
                                                  "stale": timedelta(minutes=stale_minutes)})]
    for rid in done:
        store.audit(s, "run_reconcile", "success", detail={"run_id": str(rid), "reason": "abandoned",
                                                           "threshold_minutes": int(stale_minutes)})
    return done


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m maintenance.runs")
    sub = ap.add_subparsers(dest="cmd", required=True)
    rc = sub.add_parser("reconcile")
    rc.add_argument("--stale-minutes", type=float, default=60.0)
    rc.add_argument("--apply", action="store_true")
    rc.add_argument("--confirm", type=int, default=-1)
    args = ap.parse_args(argv)
    if args.stale_minutes < MIN_STALE_MINUTES:
        print(f"HATA: --stale-minutes en az {MIN_STALE_MINUTES}", file=sys.stderr)
        return 2
    with get_session() as s, s.begin():
        if args.apply:
            done = apply(s, args.stale_minutes, args.confirm)
            print(f"abandoned olarak işaretlenen run: {len(done)} (audit_log 'run_reconcile')")
            return 0
        rows = candidates(s, args.stale_minutes)
    print(f"DRY RUN — eşik {args.stale_minutes:.0f} dk; aday {len(rows)} run (hiçbir şey değiştirilmedi):")
    for r in rows:
        print(json.dumps({k: (v.isoformat() if hasattr(v, "isoformat") else str(v) if k == "id" else v)
                          for k, v in r.items()}, ensure_ascii=False, default=str))
    if rows:
        print(f"Uygulamak için: python -m maintenance.runs reconcile --stale-minutes {args.stale_minutes:.0f} "
              f"--apply --confirm {len(rows)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
