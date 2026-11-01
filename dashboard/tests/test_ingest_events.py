"""FAZ 7: POST /ingest/events (dedektör outbox teslimi; kare yok). Gerçek PostgreSQL 17 + ppe_app rolü."""
import json
import uuid

from sqlalchemy.exc import OperationalError

import app as app_module
from db import repository as repo
from tests.conftest import count
from tests.helpers import B64_A, event, lifecycle, report


def post_events(client, events, cam="cam1", headers=None, raw=None):
    body = raw if raw is not None else json.dumps({"cam": cam, "name": f"Kamera {cam}", "events": events})
    return client.post("/ingest/events", content=body, headers={"Content-Type": "application/json", **(headers or {})})


def test_events_only_persist_and_push_live_alerts(api, engine):
    run = uuid.uuid4()
    inc, evs = lifecycle(run)
    evs = [dict(e, source_label="Kamera cam1") for e in evs]            # DurableSender etiketi olayla dondurur
    r = post_events(api, evs)
    assert r.status_code == 200 and r.json() == {"ok": True, "inserted": 3}
    assert count(engine, "violation_events") == 3 and count(engine, "incidents") == 1
    assert (app_module.SNAP_DIR / f"alert_{evs[0]['event_id']}.jpg").exists()
    assert [a["type"] for a in app_module.ALERTS] == ["start", "ongoing", "end"]
    i = api.get("/api/history/incidents").json()["items"][0]
    assert i["incident_id"] == str(inc) and i["status"] == "closed" and i["camera_name"] == "Kamera cam1"


def test_run_label_from_event_is_first_wins_and_redacted(api, engine):
    from sqlalchemy import text
    r1, r2 = uuid.uuid4(), uuid.uuid4()
    post_events(api, [event("start", run_id=r1, incident_id=uuid.uuid4(), source_label="Giriş (eski ad)")])
    post_events(api, [event("start", run_id=r1, incident_id=uuid.uuid4(), pid=2, source_label="Yeni ad")])
    post_events(api, [event("start", run_id=r2, incident_id=uuid.uuid4(),
                            source_label="Kapı rtsp://admin:S3cr3t@10.0.0.5/s")])
    with engine.connect() as c:
        got = dict(c.execute(text("SELECT id, source_label FROM detector_runs")).all())
    assert got[r1] == "Giriş (eski ad)"                                  # replay yeni adı eski run'a yazmaz
    assert "S3cr3t" not in got[r2] and "***@10.0.0.5" in got[r2]


def test_replay_after_lost_ack_is_idempotent(api, engine):
    """Sunucu commit etti ama yanıt kayboldu -> dedektör aynı gövdeyi tekrar gönderir: yeni satır/uyarı yok."""
    run = uuid.uuid4()
    _, evs = lifecycle(run)
    assert post_events(api, evs).json()["inserted"] == 3
    for _ in range(3):
        r = post_events(api, evs)
        assert r.status_code == 200 and r.json()["inserted"] == 0
    assert count(engine, "violation_events") == 3 and len(app_module.ALERTS) == 3


def test_conflicting_replay_409_and_invalid_422_roll_back(api, engine):
    run = uuid.uuid4()
    ev = event("start", run_id=run, incident_id=uuid.uuid4())
    assert post_events(api, [ev]).status_code == 200
    assert post_events(api, [dict(ev, helmet_rate=99)]).status_code == 409
    good = event("start", run_id=run, incident_id=uuid.uuid4(), pid=2)
    bad = event("start", run_id=run, incident_id=uuid.uuid4(), pid=3, helmet_rate=101)
    r = post_events(api, [good, bad])
    assert r.status_code == 422 and r.json()["detail"][0]["loc"][:2] == ["events", 1]
    assert count(engine, "violation_events") == 1                       # tüm batch geri alındı


def test_events_then_report_completes_run_and_order(api, engine):
    run = uuid.uuid4()
    _, evs = lifecycle(run)
    for e in evs:                                                        # FIFO: tek tek teslim
        assert post_events(api, [e]).status_code == 200
    assert api.post("/ingest/report", json=report(run)).status_code == 200
    assert api.post("/ingest/report", json=report(run)).status_code == 200    # rapor tekrarı tekil
    assert count(engine, "detector_runs", "WHERE status = 'finished'") == 1 and count(engine, "person_run_reports") == 1


def test_token_required_session_ignored_and_checked_before_parsing(api, anon, monkeypatch):
    monkeypatch.setattr(app_module, "INGEST_TOKEN", "makine")
    run = uuid.uuid4()
    evs = [event("start", run_id=run, incident_id=uuid.uuid4())]
    assert post_events(anon, evs).status_code == 401
    assert post_events(api, evs).status_code == 401                     # admin oturumu makine kimliği değil
    assert post_events(anon, [], raw="{bozuk" + "x" * 100000, headers={"X-Token": "yanlis"}).status_code == 401
    assert post_events(anon, evs, headers={"X-Token": "makine"}).status_code == 200


def test_limits_and_malformed(api, monkeypatch):
    assert post_events(api, [], raw="{bozuk").status_code == 422
    assert post_events(api, [], raw="[]").status_code == 422
    assert post_events(api, [], raw='{"events": "x"}').status_code == 422
    assert post_events(api, []).json() == {"ok": True, "inserted": 0}
    monkeypatch.setattr(app_module, "MAX_EVENTS_BODY_BYTES", 1000)
    big = [event("start", run_id=uuid.uuid4(), incident_id=uuid.uuid4(), snapshot=B64_A)]
    assert post_events(api, big).status_code == 413
    monkeypatch.setattr(app_module, "MAX_EVENTS_BODY_BYTES", 8 * 1024 * 1024)
    monkeypatch.setattr(app_module, "MAX_EVENTS_PER_REQUEST", 2)
    _, evs = lifecycle(uuid.uuid4())
    assert post_events(api, evs).status_code == 413


def test_db_failure_503_nothing_committed_or_alerted(api, engine, monkeypatch):
    real = repo.insert_events

    def boom(s, rows_, ids):
        real(s, rows_, ids)
        raise OperationalError("INSERT", {}, Exception("bağlantı koptu"))
    monkeypatch.setattr(repo, "insert_events", boom)
    _, evs = lifecycle(uuid.uuid4())
    assert post_events(api, evs).status_code == 503
    assert count(engine, "violation_events") == 0 and app_module.ALERTS == []


def test_events_endpoint_does_not_touch_live_video(api):
    _, evs = lifecycle(uuid.uuid4())
    assert post_events(api, evs).status_code == 200
    cam = app_module.CAMS["cam1"]
    assert cam.jpeg is None and cam.frame_no == 0                        # sahte kare üretilmedi
