"""FAZ 5: sahipsiz snapshot bakımı (yalnızca rapor varsayılanı), retention raporu ve güvenlik regresyonları."""
import logging
import os
import time
import uuid

import pytest
from fastapi.testclient import TestClient

import app as app_module
from maintenance import storage
from tests.helpers import B64_A, JPEG_A, event, frame_payload, post_frame

OLD = time.time() - 48 * 3600


def put(name, data=JPEG_A, mtime=OLD):
    p = app_module.SNAP_DIR / name
    p.write_bytes(data)
    os.utime(p, (mtime, mtime))
    return p


@pytest.fixture
def snaps(api):
    run = uuid.uuid4()
    ev = event("start", run_id=run, incident_id=uuid.uuid4(), snapshot=B64_A)
    assert post_frame(api, frame_payload([ev], run_id=run)).status_code == 200
    ref = app_module.SNAP_DIR / f"alert_{ev['event_id']}.jpg"
    os.utime(ref, (OLD, OLD))
    files = {"ref": ref, "orphan": put(f"alert_{uuid.uuid4()}.jpg"), "new_orphan": put(f"alert_{uuid.uuid4()}.jpg",
             mtime=time.time()), "legacy": put("alert_7_cam2_ID3.jpg"), "upper": put(f"alert_{str(uuid.uuid4()).upper()}.jpg"),
             "tmp": put(f".tmp-{uuid.uuid4().hex}.jpg")}
    link = app_module.SNAP_DIR / f"alert_{uuid.uuid4()}.jpg"
    outside = app_module.DATA_DIR / "disarida.jpg"
    outside.write_bytes(b"dokunulmaz")
    link.symlink_to(outside)
    files["link"], files["outside"] = link, outside
    yield files
    outside.unlink(missing_ok=True)
    qdir = app_module.DATA_DIR / "quarantine"
    if qdir.exists():
        for f in qdir.rglob("*"):
            if f.is_file():
                f.unlink()


def test_dry_run_reports_only(snaps):
    before = sorted(p.name for p in app_module.SNAP_DIR.iterdir())
    r = storage.scan(app_module.DATA_DIR, 24)
    assert r["orphans"] == [snaps["orphan"].name] and r["tmp_leftovers"] == [snaps["tmp"].name]
    assert r["referenced"] == 1 and r["too_new"] == 1 and r["legacy_kept"] == 2 and r["skipped_non_regular"] == 1
    assert sorted(p.name for p in app_module.SNAP_DIR.iterdir()) == before                 # hiçbir şey değişmedi
    assert storage.main(["snapshots"]) == 0 and sorted(p.name for p in app_module.SNAP_DIR.iterdir()) == before


def test_quarantine_requires_exact_confirmation(snaps):
    r = storage.scan(app_module.DATA_DIR, 24)
    with pytest.raises(SystemExit, match="Onay uyuşmuyor"):
        storage.quarantine(app_module.DATA_DIR, r, confirm=5)
    assert snaps["orphan"].exists() and snaps["tmp"].exists()


def test_quarantine_moves_only_safe_orphans_and_never_deletes(snaps):
    r = storage.scan(app_module.DATA_DIR, 24)
    moved = storage.quarantine(app_module.DATA_DIR, r, confirm=2)
    assert sorted(moved) == sorted([snaps["orphan"].name, snaps["tmp"].name])
    for k in ("ref", "new_orphan", "legacy", "upper", "link"):
        assert snaps[k].exists() or snaps[k].is_symlink(), k
    assert snaps["outside"].read_bytes() == b"dokunulmaz"                                   # bağ izlenmedi
    q = list((app_module.DATA_DIR / "quarantine").rglob("*.jpg"))
    assert sorted(p.name for p in q) == sorted(moved)                                       # silinmedi, taşındı
    assert app_module.SNAP_DIR not in q[0].parents


def test_min_age_floor_protects_in_flight_writes(snaps):
    r = storage.scan(app_module.DATA_DIR, 0)                     # 0 sa istenirse bile en az 1 sa uygulanır
    assert r["min_age_hours"] == 1.0 and snaps["new_orphan"].name not in r["orphans"]


def test_retention_report_is_read_only(api, engine):
    run, inc = uuid.uuid4(), uuid.uuid4()
    evs = [event("start", run_id=run, incident_id=inc)] + [event("ongoing", run_id=run, incident_id=inc, t=3.0 + i)
                                                           for i in range(5)]
    assert post_frame(api, frame_payload(evs, run_id=run)).status_code == 200
    r = storage.retention()
    assert r["events_by_type"] == {"start": 1, "ongoing": 5} and r["ongoing_max_per_incident"] == 5
    from tests.conftest import count
    assert count(engine, "violation_events") == 6


# ================================================================ güvenlik regresyonları
@pytest.mark.parametrize("path", ["/snapshots/../app.py", "/snapshots/..%2Fapp.py", "/snapshots/%2e%2e/app.py",
                                  "/snapshots/../../etc/passwd", "/snapshots/..\\app.py"])
def test_snapshot_static_path_traversal_blocked(api, path):
    r = api.get(path)
    assert r.status_code == 404 and "import" not in r.text


def test_ingest_token_never_echoed(api, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(app_module, "INGEST_TOKEN", "GERCEK_TOKEN_123")
    r = post_frame(api, frame_payload([]), headers={"X-Token": "YANLIS_TOKEN_456"})
    assert r.status_code == 401
    for s in ("GERCEK_TOKEN_123", "YANLIS_TOKEN_456"):
        assert s not in r.text and s not in caplog.text
    assert "GERCEK_TOKEN_123" not in api.get("/api/state").text + api.get("/").text


def test_unexpected_error_returns_generic_500_without_secrets(api, monkeypatch):
    from db import history

    def boom(*a, **k):
        raise ValueError("postgresql+psycopg://u:GIZLI_PAROLA@db/x Traceback")
    monkeypatch.setattr(history, "list_cameras", boom)
    r = TestClient(app_module.app, raise_server_exceptions=False).get("/api/history/cameras")
    assert r.status_code == 500 and "GIZLI_PAROLA" not in r.text and "Traceback" not in r.text


def test_browser_page_contains_no_secrets(api, monkeypatch):
    monkeypatch.setattr(app_module, "INGEST_TOKEN", "GERCEK_TOKEN_123")
    page = api.get("/").text
    for s in ("GERCEK_TOKEN_123", "DATABASE_URL", "POSTGRES_PASSWORD", "X-Token"):
        assert s not in page
