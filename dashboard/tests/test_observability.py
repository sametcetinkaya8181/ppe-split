"""FAZ 9 işletim metrikleri (gerçek PostgreSQL 17): /api/ops/metrics yalnız admin; route şablonu etiketleri (yüksek
kardinaliteli kimlik yok); 401/409/422/503 sayaçları; ingest başarı/hata, DB yazım gecikmesi, tekrar olayların
yeni satır üretmemesi; WebSocket sayaçları; dedektör sağlık/outbox özetinin beyaz listeyle temizlenmesi (gizli
bilgi sızmaz); PostgreSQL ve snapshot deposu durumu; OPS_METRICS=0."""
import json
import uuid

import pytest

import app as app_module
import observability as ops
from tests.conftest import count, login, make_user
from tests.helpers import B64_A, event, frame_payload, lifecycle, post_frame


@pytest.fixture
def reg(api):
    ops.REG.__init__()                                     # süreç geneli kayıt: her test boş başlar
    yield ops.REG
    ops.REG.__init__()


def _eventually(cond, timeout=5.0):
    import time
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.05)
    return cond()


def metrics(client):
    r = client.get("/api/ops/metrics")
    assert r.status_code == 200, r.text
    return r.json()


def test_metrics_endpoint_admin_only(api, anon, reg):
    assert anon.get("/api/ops/metrics").status_code == 401
    for role in ("viewer", "supervisor"):
        from fastapi.testclient import TestClient
        c = TestClient(app_module.app)
        make_user(f"u_{role}", role)
        login(c, f"u_{role}")
        assert c.get("/api/ops/metrics").status_code == 403, role
    m = metrics(api)
    assert {"http", "ingest", "db_write", "websocket", "cameras", "postgres", "snapshot_storage"} <= set(m)
    assert m["postgres"]["ready"] is True and m["postgres"]["pool"]["size"] >= 1
    assert m["snapshot_storage"]["writable"] is True and m["snapshot_storage"]["disk_free_bytes"] > 0


def test_route_template_labels_no_ids_and_unmatched_collapsed(api, reg):
    ids = [uuid.uuid4() for _ in range(3)]
    for i in ids:
        assert api.get(f"/api/history/incidents/{i}").status_code == 404
    for i in range(3):
        api.get(f"/olmayan/{uuid.uuid4()}")
    m = metrics(api)
    routes = m["http"]["routes"]
    assert routes["/api/history/incidents/{incident_id}"]["count"] == 3
    assert routes["other"]["count"] == 3
    dump = json.dumps(m)
    assert not any(str(i) in dump for i in ids)
    assert m["history_latency_ms"]["n"] >= 3


def test_ingest_counters_status_counts_and_db_latency(api, anon, engine, reg, monkeypatch):
    run = uuid.uuid4()
    _, evs = lifecycle(run)
    body = {"cam": "cam1", "name": "Kamera cam1", "events": evs}
    assert anon.post("/ingest/events", json=body).status_code == 200
    assert anon.post("/ingest/events", json=body).status_code == 200          # replay (ACK kaybı)
    assert count(engine, "violation_events") == 3                              # yeni satır yok
    bad = dict(evs[0], missing_helmet="x")
    assert anon.post("/ingest/events", json={"cam": "cam1", "events": [bad]}).status_code == 422
    clash = dict(evs[0], pid=99)                                               # aynı event_id, farklı içerik
    assert anon.post("/ingest/events", json={"cam": "cam1", "events": [clash]}).status_code == 409
    monkeypatch.setattr(app_module, "INGEST_TOKEN", "dogru-token")
    assert anon.post("/ingest/events", json=body).status_code == 401           # güvenlik katmanı reddi de sayılır
    monkeypatch.setattr(app_module, "INGEST_TOKEN", "")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:COK_GIZLI@olmayan-sunucu:5432/x")
    run2 = uuid.uuid4()
    r = post_frame(anon, frame_payload([event("start", run_id=run2, incident_id=uuid.uuid4())], run_id=run2))
    assert r.status_code == 503
    monkeypatch.undo()
    m = metrics(api)
    t = m["http"]["tracked_status"]
    assert (t["401"], t["409"], t["422"], t["503"]) == (1, 1, 1, 1)
    assert m["ingest"]["by_route"]["/ingest/events"] == {"success": 2, "error": 3}
    assert m["ingest"]["by_route"]["/ingest/frame"] == {"success": 0, "error": 1}
    assert m["ingest"]["events_received"] == 6 and m["ingest"]["events_inserted"] == 3
    assert m["counters"]["events_duplicate_ignored"] == 3
    dbw = m["db_write"]["events"]
    assert dbw["ok"] == 2 and dbw["error"] >= 1 and dbw["p95"] is not None
    assert "COK_GIZLI" not in json.dumps(m) and "olmayan-sunucu" not in json.dumps(m)


def test_detector_health_and_outbox_are_whitelisted(api, anon, reg):
    run = uuid.uuid4()
    health = {"state": "connected", "proc_fps": 12.4, "lag_sec": 0.3, "flags": ["unevaluated_persons", "<script>"],
              "source": "rtsp://admin:S3cr3t@10.0.0.5/s", "coverage": {"visible": 3, "analyzing": 1, "decided": 2,
              "analyzing_by_reason": {"too_small": 1, "rtsp://x": 5}, "secret": "x"}, "uptime_sec": "çok"}
    outbox = {"pending_count": 4, "state": "backoff", "warnings": ["pending_age", "evil"], "body": "{...}",
              "msg_id": str(uuid.uuid4())}
    p = frame_payload([], run_id=run, health=health, outbox=outbox)
    assert post_frame(anon, p).status_code == 200
    cam = api.get("/api/state").json()["cameras"][0]
    h, o = cam["meta"]["health"], cam["meta"]["outbox"]
    assert h["flags"] == ["unevaluated_persons"] and h["proc_fps"] == 12.4 and h["uptime_sec"] is None
    assert h["coverage"]["analyzing_by_reason"] == {"too_small": 1} and "secret" not in h["coverage"]
    assert o["warnings"] == ["pending_age"] and o["pending_count"] == 4 and "body" not in o and "msg_id" not in o
    m = metrics(api)
    dump = json.dumps(m) + json.dumps(cam)
    assert "S3cr3t" not in dump and "10.0.0.5" not in dump and "<script>" not in dump
    c = m["cameras"][0]
    assert c["id"] == "cam1" and c["health"]["coverage"]["analyzing"] == 1 and c["last_frame_age_sec"] >= 0


def test_websocket_counters(api, anon, reg):
    with api.websocket_connect("/ws") as ws:
        assert post_frame(anon, frame_payload([])).status_code == 200
        ws.receive_bytes()
        assert metrics(api)["websocket"]["active"] == 1
    assert _eventually(lambda: metrics(api)["websocket"]["closed"] == 1)
    w = metrics(api)["websocket"]
    assert w["accepted"] == 1 and w["active"] == 0 and w["frames_sent"] >= 1
    with pytest.raises(Exception):
        with anon.websocket_connect("/ws") as ws:                               # oturumsuz: reddedilir
            ws.receive_bytes()
    assert metrics(api)["websocket"]["rejected"] == 1


def test_snapshot_storage_counts_files_without_names(api, anon, reg):
    run = uuid.uuid4()
    ev = event("start", run_id=run, incident_id=uuid.uuid4(), snapshot=B64_A)
    assert anon.post("/ingest/events", json={"cam": "cam1", "events": [ev]}).status_code == 200
    app_module._STORAGE.at = -1e9                                              # önbelleği yenile
    st = metrics(api)["snapshot_storage"]
    assert st["files"] == 1 and st["bytes"] > 0 and "alert_" not in json.dumps(st)


def test_metrics_can_be_disabled(api, reg, monkeypatch):
    monkeypatch.setattr(ops, "ENABLED", False)
    assert api.get("/api/ops/metrics").status_code == 404
    api.get("/healthz")
    assert ops.REG.snapshot()["http"]["routes"] == {}


def test_percentiles_helper():
    p = ops.percentiles([1.0, 2.0, 3.0, 4.0])
    assert p["p50"] == 2.5 and p["max"] == 4.0 and p["n"] == 4
    assert ops.percentiles([])["p99"] is None


def test_coverage_fields_whitelisted_for_operator_panel(api, anon, reg):
    """FAZ 10-A: kamera bazlı kapsam paneli için birikimli alanlar geçer; bilinmeyen neden/anahtar düşer."""
    cov = {"visible": 2, "decided": 1, "analyzing": 1, "track_frames": 100, "track_frames_unevaluated": 91,
           "track_frames_analyzing": 95, "unevaluated_by_reason": {"edge": 90, "low_conf": 1, "isim": 5},
           "tracks_closed_never_evaluated": 4, "never_evaluated_max_sec": 23.9, "open_unevaluated_long": 1,
           "open_unevaluated_max_sec": 14.2, "tracks_seen": 9, "kisi_adi": "Ali"}
    p = frame_payload([], run_id=uuid.uuid4(), health={"state": "connected", "flags": ["unevaluated_long"],
                                                       "coverage": cov})
    assert post_frame(anon, p).status_code == 200
    h = api.get("/api/state").json()["cameras"][0]["meta"]["health"]
    c = h["coverage"]
    assert c["unevaluated_by_reason"] == {"edge": 90, "low_conf": 1} and "kisi_adi" not in c
    assert (c["track_frames"], c["track_frames_unevaluated"], c["open_unevaluated_long"]) == (100, 91, 1)
    assert c["never_evaluated_max_sec"] == 23.9 and h["flags"] == ["unevaluated_long"]
