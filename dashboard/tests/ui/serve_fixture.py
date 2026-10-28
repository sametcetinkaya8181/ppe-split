"""Tarayıcı UI testleri için İZOLE dashboard örnekleri (ana DB'ye dokunmaz). tests servisinde çalışır:

    docker compose --profile test run --rm -d -p 127.0.0.1:8001:8001 -p 127.0.0.1:8002:8002 -p 127.0.0.1:8003:8003 \\
        tests sh -c "pip install -q -r requirements-dev.txt && python -m tests.ui.serve_fixture"

:8001  ppe_ui_test       — tüm durumlar (closed/active/stale/run_end/orphan/unknown), eski kayıt, XSS adı, 60+ satır
:8002  erişilemeyen DB   — geçmiş uçları 503
:8003  ppe_ui_empty_test — boş veritabanı
Veriler gerçek ingest uçlarından (TestClient) yazılır; yalnızca zaman/eski kayıt koşulları SQL ile hazırlanır."""
import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone

from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.conftest import alembic_cfg

BASE = make_url(os.environ["TEST_DATABASE_URL"])
XSS = '<img src=x onerror="window.__xss=1">'


def db(name):
    admin = create_engine(BASE.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        c.execute(text(f'CREATE DATABASE "{name}"'))
    admin.dispose()
    url = BASE.set(database=name).render_as_string(hide_password=False)
    command.upgrade(alembic_cfg(url), "head")
    return url


def seed(url, data_dir):
    os.environ.update(DATABASE_URL=url, DATA_DIR=data_dir, ACTIVE_WINDOW_SEC="3600")
    import app as app_module
    from fastapi.testclient import TestClient
    from tests.helpers import B64_A, event, frame_payload, iso, lifecycle, post_frame, report
    api = TestClient(app_module.app)
    now = datetime.now(timezone.utc).replace(microsecond=0)

    def send(evs, run, cam="cam1", name=None):
        p = frame_payload(evs, cam=cam, run_id=run, source=f"videos/{cam}.mp4")
        p["name"] = name or f"Kamera {cam}"
        assert post_frame(api, p).status_code == 200

    def at(minutes):
        return now - timedelta(minutes=minutes)

    # closed (snapshot'lı, start -> ongoing -> end) + rapor
    r1 = uuid.uuid4()
    inc = uuid.uuid4()
    send([event("start", run_id=r1, incident_id=inc, t=2.1, at=at(30), snapshot=B64_A),
          event("ongoing", run_id=r1, incident_id=inc, t=5.1, at=at(30) + timedelta(seconds=3)),
          event("end", run_id=r1, incident_id=inc, t=7.4, at=at(30) + timedelta(seconds=5))], r1, name="Giriş kapısı")
    # stale + run_end + 60 kapalı ihlal (sayfalama)
    stale, run_end = uuid.uuid4(), uuid.uuid4()
    send([event("start", run_id=r1, incident_id=stale, pid=11, at=at(29)),
          event("start", run_id=r1, incident_id=run_end, pid=12, at=at(28), missing="Yeleksiz", missing_helmet=False,
                missing_vest=True)], r1, name="Giriş kapısı")
    for i in range(60):
        k = uuid.uuid4()
        send([event("start", run_id=r1, incident_id=k, pid=100 + i, at=at(200 + i),
                     missing="Baretsiz + Yeleksiz", missing_vest=True),
              event("end", run_id=r1, incident_id=k, pid=100 + i, at=at(200 + i) + timedelta(seconds=9))], r1,
             name="Giriş kapısı")
    assert api.post("/ingest/report", json=report(r1, name="Giriş kapısı", unclosed_incidents=[
        {"incident_id": str(stale), "pid": 11, "reason": "stale", "t": 20.0},
        {"incident_id": str(run_end), "pid": 12, "reason": "run_end", "t": 30.0}],
        started_at=iso(at(300)), finished_at=iso(at(1)))).status_code == 200
    # active (çalışan run) + orphan + XSS adlı kamera
    r2 = uuid.uuid4()
    send([event("start", run_id=r2, incident_id=uuid.uuid4(), cam="cam2", pid=21, at=at(2)),
          event("end", run_id=r2, incident_id=uuid.uuid4(), cam="cam2", pid=22, at=at(3), orphan=True)], r2,
         cam="cam2", name=XSS)
    # unknown (heartbeat kayıp) + eski kayıt (kaynak doğrulanamadı)
    r3 = uuid.uuid4()
    send([event("start", run_id=r3, incident_id=uuid.uuid4(), cam="cam3", pid=31, at=at(1500))], r3, cam="cam3",
         name="Eski video")
    eng = create_engine(url)
    with eng.begin() as c:
        c.execute(text("UPDATE detector_runs SET last_seen_at = now() - interval '2 hours', source_label = NULL, "
                       "source_kind = NULL, source_ref = NULL WHERE id = :r"), {"r": r3})
        c.execute(text("UPDATE violation_events SET ingested_at = now() - interval '2 hours' WHERE run_id = :r"),
                  {"r": r3})
        c.execute(text("UPDATE cameras SET name = 'Bugün başka video' WHERE id = 'cam3'"))
    eng.dispose()
    app_module.flush_heartbeats()
    make_users(url, (("ui_admin", "admin", None), ("ui_viewer", "viewer", None), ("ui_cam1", "supervisor", ["cam1"])))


UI_PASSWORD = "Ui-Test-Parola-2026"      # yalnız izole UI test veritabanları


def make_users(url, users):
    from auth import store
    from db.session import get_session
    with get_session(url) as s, s.begin():
        for name, role, cams in users:
            store.create_user(s, name, UI_PASSWORD, role, cams)


def main():
    root = "/tmp/ui"
    full = db("ppe_ui_test")
    empty = db("ppe_ui_empty_test")
    full_dir, empty_dir, bad_dir = f"{root}/full", f"{root}/empty", f"{root}/bad"
    for d in (full_dir, empty_dir, bad_dir):
        os.makedirs(f"{d}/snapshots", exist_ok=True)
    seed(full, full_dir)
    make_users(empty, (("ui_admin", "admin", None),))
    bad ="postgresql+psycopg://u:p@olmayan-db:5432/x"
    procs = []
    for port, url, d in ((8001, full, full_dir), (8002, bad, bad_dir), (8003, empty, empty_dir)):
        env = dict(os.environ, DATABASE_URL=url, DATA_DIR=d, ACTIVE_WINDOW_SEC="3600")
        procs.append(subprocess.Popen([sys.executable, "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port",
                                       str(port), "--log-level", "warning"], env=env))
    print("UI fixture hazır: 8001 (veri), 8002 (DB yok), 8003 (boş)", flush=True)
    for p in procs:
        p.wait()


if __name__ == "__main__":
    main()
