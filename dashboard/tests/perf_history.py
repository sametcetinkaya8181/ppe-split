"""Geçmiş sorguları performans ölçümü (pytest testi DEĞİL). Yalnızca izole 'ppe_perf_test' veritabanını kullanır.

    docker compose --profile test run --rm tests python -m tests.perf_history [--incidents 30000]

Sentetik veri: 3 kamera, 600 run, N incident (her biri start + 8 ongoing + %90'ında end). Her sorgu 3 kez çalışır;
en iyi süre ve EXPLAIN (ANALYZE) yürütme süresi raporlanır. Sonunda veritabanı silinir (--keep ile kalır)."""
import argparse
import os
import statistics
import time
import uuid
from datetime import datetime, timezone

from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from db import history
from tests.conftest import alembic_cfg

DB = "ppe_perf_test"
SEED = """
INSERT INTO cameras (id, name) SELECT 'cam'||i, 'Kamera '||i FROM generate_series(1,3) i;
INSERT INTO detector_runs (id, camera_id, status, started_at, finished_at, last_seen_at, report_metadata, source_label)
SELECT md5('run'||r)::uuid, 'cam'||(r%3+1), 'finished', ts, ts + interval '1 hour', ts + interval '1 hour',
       '{"unclosed_incidents": [], "unclosed_overflow": 0}'::jsonb, 'Etiket '||r
FROM (SELECT r, timestamptz '2026-01-01 00:00Z' + make_interval(hours => r * 6) ts FROM generate_series(1,600) r) x;
INSERT INTO incidents (id, run_id, camera_id, pid)
SELECT md5('inc'||n)::uuid, md5('run'||(n%600+1))::uuid, 'cam'||((n%600+1)%3+1), n%50 FROM generate_series(1,:n) n;
INSERT INTO violation_events (event_id, incident_id, run_id, camera_id, pid, event_type, missing_helmet, missing_vest,
                              missing_text, helmet_rate, vest_rate, video_time_sec, occurred_at, box, snapshot_path)
SELECT md5('ev'||n||'-'||k)::uuid, md5('inc'||n)::uuid, md5('run'||(n%600+1))::uuid, 'cam'||((n%600+1)%3+1), n%50,
       CASE WHEN k = 0 THEN 'start' WHEN k = 9 THEN 'end' ELSE 'ongoing' END,
       k < 9, k < 9 AND n%4 = 0, CASE WHEN k = 9 THEN '' WHEN n%4 = 0 THEN 'Baretsiz + Yeleksiz' ELSE 'Baretsiz' END,
       10, 90, k * 3, timestamptz '2026-01-01 00:00Z' + make_interval(hours => (n%600+1) * 6, secs => (n/600)*30 + k*3),
       '[1, 2, 30, 40]'::jsonb, NULL
FROM generate_series(1,:n) n CROSS JOIN generate_series(0,9) k WHERE k < 9 OR n%10 <> 0;
ANALYZE;
"""


def best_of(fn, n=3):
    times = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        times.append((time.perf_counter() - t0) * 1000)
    return min(times), statistics.median(times)


def explain(conn, stmt):
    sql = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    plan = conn.execute(text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql)).scalar()[0]
    return plan["Execution Time"], plan["Plan"]["Node Type"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--incidents", type=int, default=30000)
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()
    base = make_url(os.environ["TEST_DATABASE_URL"])
    admin = create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
    url = base.set(database=DB).render_as_string(hide_password=False)
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{DB}" WITH (FORCE)'))
        c.execute(text(f'CREATE DATABASE "{DB}"'))
    try:
        command.upgrade(alembic_cfg(url), "head")
        eng = create_engine(url)
        t0 = time.perf_counter()
        with eng.begin() as c:
            for stmt in [s for s in SEED.split(";\n") if s.strip()]:
                c.execute(text(stmt.replace(":n", str(args.incidents))))
        with eng.connect() as c:
            n_ev = c.execute(text("SELECT count(*) FROM violation_events")).scalar()
        print(f"veri: {args.incidents} incident, {n_ev} olay ({time.perf_counter() - t0:.1f} sn)")

        W = 90.0
        f30 = datetime(2026, 3, 1, tzinfo=timezone.utc)
        t30 = datetime(2026, 3, 31, tzinfo=timezone.utc)
        f7, t7 = datetime(2026, 3, 1, tzinfo=timezone.utc), datetime(2026, 3, 8, tzinfo=timezone.utc)
        with Session(eng) as s:
            first = history.list_incidents(s, limit=50, active_window=W)
            some_inc = first["items"][0]["incident_id"]
            some_run = first["items"][0]["run_id"]
            cases = {
                "incidents: filtresiz, 1. sayfa": lambda: history.list_incidents(s, limit=50, active_window=W),
                "incidents: filtresiz, 2. sayfa (cursor)": lambda: history.list_incidents(
                    s, limit=50, cursor=first["next_cursor"], active_window=W),
                "incidents: 7 gün": lambda: history.list_incidents(s, date_from=f7, date_to=t7, limit=50, active_window=W),
                "incidents: 7 gün + kamera": lambda: history.list_incidents(
                    s, camera_id="cam2", date_from=f7, date_to=t7, limit=50, active_window=W),
                "incidents: run filtresi": lambda: history.list_incidents(s, run_id=some_run, limit=50, active_window=W),
                "incidents: durum=closed + ekipman=both": lambda: history.list_incidents(
                    s, statuses=("closed",), equipment="both", limit=50, active_window=W),
                "incident detay + timeline": lambda: history.get_incident(s, some_inc, active_window=W),
                "summary: filtresiz": lambda: history.summary(s, active_window=W),
                "summary: 30 gün, Europe/Istanbul": lambda: history.summary(s, date_from=f30, date_to=t30,
                                                                         tz="Europe/Istanbul", active_window=W),
                "summary: 30 gün + kamera": lambda: history.summary(s, camera_id="cam1", date_from=f30, date_to=t30,
                                                                   active_window=W),
                "runs: 1. sayfa": lambda: history.list_runs(s, limit=50, active_window=W),
                "run detay": lambda: history.get_run(s, some_run, active_window=W),
                "cameras": lambda: history.list_cameras(s, active_window=W),
            }
            print(f"{'sorgu':45} {'en iyi ms':>10} {'medyan ms':>10}")
            for name, fn in cases.items():
                b, m = best_of(fn)
                print(f"{name:45} {b:10.1f} {m:10.1f}")
            from sqlalchemy import func, select
            print("\nEXPLAIN ANALYZE (sayım sorgusu, filtre yolları):")
            for name, kw in (("filtresiz", {}), ("7 gün", {"date_from": f7, "date_to": t7}),
                             ("7 gün + kamera", {"camera_id": "cam2", "date_from": f7, "date_to": t7})):
                q = history._incident_rows(active_window=W, **kw)
                ms, node = explain(s.connection(), select(func.count()).select_from(q))
                print(f"  {name:20} {ms:8.1f} ms  kök düğüm: {node}")
    finally:
        if not args.keep:
            with admin.connect() as c:
                c.execute(text(f'DROP DATABASE IF EXISTS "{DB}" WITH (FORCE)'))


if __name__ == "__main__":
    main()
