"""FAZ 5: run başına kaynak bilgisi (0003). Aynı kamera kimliği farklı çalışmalarda farklı kaynaklara ait olabilir;
geçmiş kayıt kendi run'ının donmuş etiketiyle gösterilmeli, kameranın güncel adı geçmişe aitmiş gibi sunulmamalı."""
import json
import time
import uuid

import pytest
from alembic import command
from sqlalchemy import text

import app as app_module
from db import repository as repo
from tests.conftest import alembic_cfg
from tests.helpers import B64_A, event, frame_payload, lifecycle, post_frame, report

URL = "/api/history"


def run_frame(api, run, *, name, source="videos/a.mp4", cam="cam1", events=(), kind="file"):
    p = frame_payload(list(events), cam=cam, run_id=run, source=source, source_kind=kind)
    p["name"] = name
    r = post_frame(api, p)
    assert r.status_code == 200, r.text
    app_module.flush_heartbeats()


def run_row(engine, run):
    with engine.connect() as c:
        return c.execute(text("SELECT source_label, source_kind, source_ref FROM detector_runs WHERE id = :r"),
                         {"r": run}).mappings().one()


def test_same_camera_id_two_videos_keep_their_own_labels(api, engine):
    """cam1 önce video A, sonra (kaynak sırası değişince) video B: A'nın ihlali B adıyla görünmemeli."""
    r_a, r_b = uuid.uuid4(), uuid.uuid4()
    inc_a, evs_a = lifecycle(r_a)
    run_frame(api, r_a, name="video-A", source="C:/Users/PC/videos/video-A.mp4", events=evs_a)
    assert api.post("/ingest/report", json=report(r_a)).status_code == 200
    inc_b, evs_b = lifecycle(r_b)
    run_frame(api, r_b, name="video-B", source="videos/video-B.mp4", events=evs_b)

    got = {i["incident_id"]: i for i in api.get(f"{URL}/incidents").json()["items"]}
    a, b = got[str(inc_a)], got[str(inc_b)]
    assert (a["camera_id"], a["camera_name"], a["source_ref"], a["source_verified"]) == ("cam1", "video-A", "video-A.mp4", True)
    assert (b["camera_name"], b["source_ref"]) == ("video-B", "video-B.mp4")
    assert a["camera_current_name"] == b["camera_current_name"] == "video-B"        # güncel ad ayrı alanda
    assert api.get(f"{URL}/incidents/{inc_a}").json()["camera"] == {"id": "cam1", "name": "video-A",
                                                                    "current_name": "video-B"}
    runs = {r["run_id"]: r for r in api.get(f"{URL}/runs").json()["items"]}
    assert runs[str(r_a)]["camera_name"] == "video-A" and runs[str(r_b)]["camera_name"] == "video-B"
    assert run_row(engine, r_a)["source_ref"] == "video-A.mp4"                     # kullanıcı klasör yolu saklanmaz


def test_run_label_is_frozen_after_first_record(api, engine):
    run = uuid.uuid4()
    run_frame(api, run, name="Giriş", source="videos/giris.mp4")
    app_module._HB.clear()
    run_frame(api, run, name="Yeni ad", source="videos/baska.mp4")                  # aynı run, sonradan ad değişti
    run_frame(api, run, name="Yeni ad", events=[event("start", run_id=run, incident_id=uuid.uuid4())])
    body = report(run, name="Rapordaki ad")
    assert api.post("/ingest/report", json=body).status_code == 200
    assert dict(run_row(engine, run)) == {"source_label": "Giriş", "source_kind": "file", "source_ref": "giris.mp4"}
    with engine.connect() as c:
        assert c.execute(text("SELECT name FROM cameras")).scalar() == "Rapordaki ad"   # kamera adı güncellenir


def test_report_only_run_gets_label_and_kind(api, engine):
    run = uuid.uuid4()
    assert api.post("/ingest/report", json=report(run, name="Sadece rapor", source_kind="rtsp")).status_code == 200
    r = run_row(engine, run)
    assert (r["source_label"], r["source_kind"], r["source_ref"]) == ("Sadece rapor", "rtsp", None)


def test_legacy_run_without_source_is_not_attributed_to_current_name(api, engine):
    run = uuid.uuid4()
    inc, evs = lifecycle(run)
    run_frame(api, run, name="Eski video", events=evs)
    with engine.begin() as c:                                    # 0003 öncesi kayıt: kaynak bilgisi yok
        c.execute(text("UPDATE detector_runs SET source_label = NULL, source_kind = NULL, source_ref = NULL"))
        c.execute(text("UPDATE cameras SET name = 'Bugünkü başka video'"))
    i = api.get(f"{URL}/incidents").json()["items"][0]
    assert i["camera_name"] is None and i["source_verified"] is False and i["source_ref"] is None
    assert i["camera_current_name"] == "Bugünkü başka video"
    d = api.get(f"{URL}/incidents/{inc}").json()
    assert d["camera"]["name"] is None
    r = api.get(f"{URL}/runs/{run}").json()
    assert r["camera_name"] is None and r["source_verified"] is False


def test_rtsp_credentials_never_in_run_source(api, engine):
    run = uuid.uuid4()
    src = "rtsp://admin:S3cr3t@10.0.0.5:554/s?token=T0K3N#frag"
    run_frame(api, run, name=f"Kapı {src}", source=src, kind="rtsp",
              events=[event("start", run_id=run, incident_id=uuid.uuid4(), snapshot=B64_A)])
    r = run_row(engine, run)
    assert r["source_ref"] == "rtsp://***@10.0.0.5:554/s?token=***" and r["source_kind"] == "rtsp"
    dump = json.dumps(dict(r)) + api.get(f"{URL}/incidents").text + api.get(f"{URL}/runs").text
    for secret in ("S3cr3t", "admin", "T0K3N", "frag"):
        assert secret not in dump, secret


def test_invalid_source_kind_is_not_stored(api, engine):
    run = uuid.uuid4()
    run_frame(api, run, name="x", kind="ftp")
    assert run_row(engine, run)["source_kind"] is None


# ================================================================ heartbeat canlı akışı bekletmez
def test_slow_db_heartbeat_does_not_block_live_frames(api, monkeypatch):
    real = repo.heartbeat
    monkeypatch.setattr(repo, "heartbeat", lambda s, **kw: (time.sleep(2), real(s, **kw)))
    run = uuid.uuid4()
    t0 = time.monotonic()
    for i in range(5):
        assert post_frame(api, frame_payload([], run_id=run)).status_code == 200
        if i == 0:
            app_module._HB.clear()                               # throttling'i atla: yığılma koruması devrede
    assert time.monotonic() - t0 < 1.5                           # 2 sn'lik yazım isteği bekletmedi
    app_module.flush_heartbeats()


def test_db_down_heartbeat_fast_but_events_still_503(api, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@10.255.255.1:5432/x")   # yanıt vermeyen adres
    run = uuid.uuid4()
    t0 = time.monotonic()
    assert post_frame(api, frame_payload([], run_id=run)).status_code == 200
    assert time.monotonic() - t0 < 1.0
    r = post_frame(api, frame_payload([event("start", run_id=run, incident_id=uuid.uuid4())], run_id=run))
    assert r.status_code == 503 and app_module.ALERTS == []      # olay başarılı gösterilmez
    app_module.flush_heartbeats(timeout=15)


# ================================================================ migration 0003 (yalnızca ppe_test)
def test_0003_downgrade_upgrade_and_no_backfill(db_url, engine):
    with engine.begin() as c:
        c.execute(text("TRUNCATE violation_events, incidents, person_run_reports, detector_runs, cameras"))
        c.execute(text("INSERT INTO cameras (id, name) VALUES ('cam1', 'Güncel ad')"))
        c.execute(text("INSERT INTO detector_runs (id, camera_id, source_label) VALUES (:r, 'cam1', 'Etiket')"),
                  {"r": uuid.uuid4()})
    try:
        with pytest.raises(RuntimeError, match="kaynak bilgileri dolu"):
            command.downgrade(alembic_cfg(), "0002")                 # veri kaybını onaysız yapmaz
        with engine.begin() as c:
            c.execute(text("UPDATE detector_runs SET source_label = NULL"))
        command.downgrade(alembic_cfg(), "0002")
        command.upgrade(alembic_cfg(), "head")
        with engine.connect() as c:                                  # eski run: tahmini backfill YOK
            row = c.execute(text("SELECT source_label, source_kind, source_ref FROM detector_runs")).one()
        assert tuple(row) == (None, None, None)
    finally:
        command.upgrade(alembic_cfg(), "head")
        with engine.begin() as c:
            c.execute(text("TRUNCATE violation_events, incidents, person_run_reports, detector_runs, cameras"))
