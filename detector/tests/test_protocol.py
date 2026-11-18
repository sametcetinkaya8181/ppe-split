"""Kimlik, incident yaşam döngüsü, missing->boolean, UTC zaman ve kaynak maskeleme."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

import protocol as p


# ---------------------------------------------------------------- missing -> boolean (10)
@pytest.mark.parametrize("kind,missing,expected", [
    ("start", "Baretsiz", (True, False)),
    ("start", "Yeleksiz", (False, True)),
    ("start", "Baretsiz + Yeleksiz", (True, True)),
    ("ongoing", "Yeleksiz + Baretsiz", (True, True)),
    ("ongoing", "Baretsiz", (True, False)),
    ("end", "", (False, False)),
])
def test_missing_flags(kind, missing, expected):
    assert p.missing_flags(kind, missing) == expected


@pytest.mark.parametrize("kind,missing", [
    ("start", ""), ("start", "baretsiz"), ("start", "Eldivensiz"), ("start", "Baretsiz + Baretsiz"),
    ("start", "Baretsiz+Yeleksiz"), ("ongoing", None), ("end", "Baretsiz"), ("foo", "Baretsiz"), (None, ""),
])
def test_missing_flags_rejects_unexpected(kind, missing):
    with pytest.raises(p.ProtocolError):
        p.missing_flags(kind, missing)


# ---------------------------------------------------------------- UTC zaman (11)
def test_utc_now_iso_is_timezone_aware_utc():
    s = p.utc_now_iso()
    assert s.endswith("Z")
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    assert dt.utcoffset() == timedelta(0)
    assert abs((datetime.now(timezone.utc) - dt).total_seconds()) < 2


# ---------------------------------------------------------------- incident yaşam döngüsü (4, 5, 8, 9)
def test_incident_lifecycle_same_id():
    tr = p.IncidentTracker()
    s, o1, o2, e = (tr.assign(k, 7) for k in ("start", "ongoing", "ongoing", "end"))
    assert s[0] == o1[0] == o2[0] == e[0] and not any(x[1] for x in (s, o1, o2, e))
    assert tr.active == {}


def test_new_violation_gets_new_incident_and_old_id_never_reused():
    tr = p.IncidentTracker()
    seen = set()
    for _ in range(50):
        iid, _ = tr.assign("start", 7)
        assert iid not in seen
        seen.add(iid)
        assert tr.assign("end", 7)[0] == iid


def test_pids_are_independent_within_run():
    tr = p.IncidentTracker()
    a, b = tr.assign("start", 1)[0], tr.assign("start", 2)[0]
    assert a != b and tr.assign("ongoing", 1)[0] == a and tr.assign("ongoing", 2)[0] == b


@pytest.mark.parametrize("kind", ["ongoing", "end"])
def test_orphan_event_gets_own_incident(kind):
    tr = p.IncidentTracker()
    active = tr.assign("start", 1)[0]
    iid, orphan = tr.assign(kind, 2)                  # pid 2 için aktif incident yok
    assert orphan and iid != active and tr.orphans == 1
    assert 2 not in tr.active and tr.active == {1: active}
    uuid.UUID(iid)


def test_stale_closes_without_end_and_restart_gets_new_incident():
    tr = p.IncidentTracker()
    old = tr.assign("start", 5)[0]
    tr.sweep(live_pids=[], t=42.04)                   # PPEMonitor kişiyi unuttu
    assert tr.active == {}
    assert tr.unclosed == [{"incident_id": old, "pid": 5, "reason": "stale", "t": 42.0}]
    iid, orphan = tr.assign("ongoing", 5)             # olmamalı ama gelirse eskiye bağlanmaz
    assert orphan and iid != old
    assert tr.assign("start", 5)[0] != old


def test_sweep_keeps_live_pids():
    tr = p.IncidentTracker()
    a = tr.assign("start", 1)[0]
    tr.assign("start", 2)
    tr.sweep(live_pids={1}, t=1.0)
    assert tr.active == {1: a} and [u["pid"] for u in tr.unclosed] == [2]


def test_superseded_start_and_close_all():
    tr = p.IncidentTracker()
    first = tr.assign("start", 3)[0]
    second = tr.assign("start", 3)[0]
    assert first != second and tr.unclosed[0]["reason"] == "superseded"
    tr.close_all("run_end", 9.0)
    assert tr.active == {} and tr.unclosed[-1] == {"incident_id": second, "pid": 3, "reason": "run_end", "t": 9.0}


def test_unclosed_list_is_bounded():
    tr = p.IncidentTracker()
    for pid in range(p.IncidentTracker.MAX_UNCLOSED + 25):
        tr.assign("start", pid)
    tr.sweep([], 1.0)
    assert len(tr.unclosed) == p.IncidentTracker.MAX_UNCLOSED and tr.unclosed_overflow == 25
    assert tr.active == {}


# ---------------------------------------------------------------- olay zenginleştirme (2)
RAW = {"type": "start", "pid": 7, "t": 12.4, "missing": "Baretsiz", "helmet_rate": 12, "vest_rate": 78,
       "box": [20, 30, 200, 420]}


def test_enrich_event_fields_and_legacy_preserved():
    tr = p.IncidentTracker()
    raw = dict(RAW)
    ev = p.enrich_event(raw, run_id="R", camera_id="cam1", tracker=tr)
    assert raw == RAW                                  # kaynak dict değişmedi
    for k, v in RAW.items():
        assert ev[k] == v
    assert ev["run_id"] == "R" and ev["camera_id"] == "cam1"
    assert ev["missing_helmet"] is True and ev["missing_vest"] is False
    uuid.UUID(ev["event_id"]), uuid.UUID(ev["incident_id"])
    assert ev["occurred_at"].endswith("Z") and "orphan" not in ev


def test_event_ids_unique():
    tr = p.IncidentTracker()
    ids = {p.enrich_event(dict(RAW, pid=i), run_id="R", camera_id="c", tracker=tr)["event_id"] for i in range(2000)}
    assert len(ids) == 2000


def test_protocol_error_leaves_tracker_untouched():
    tr = p.IncidentTracker()
    with pytest.raises(p.ProtocolError):
        p.enrich_event(dict(RAW, missing="Bilinmeyen"), run_id="R", camera_id="c", tracker=tr)
    assert tr.active == {} and tr.orphans == 0


# ---------------------------------------------------------------- kaynak türü ve maskeleme (18)
@pytest.mark.parametrize("src,kind", [
    ("0", "webcam"), ("2", "webcam"), ("rtsp://u:p@10.0.0.5:554/s", "rtsp"), ("RTSPS://h/s", "rtsp"),
    ("https://www.youtube.com/watch?v=abc", "youtube"), ("https://youtu.be/abc", "youtube"),
    ("http://cam.local/mjpeg", "other"), (r"C:\Users\PC\videos\a.mp4", "file"), ("videos/a.mp4", "file"),
])
def test_source_kind(src, kind):
    assert p.source_kind(src) == kind


@pytest.mark.parametrize("src,expected", [
    ("rtsp://admin:S3cr3t!@10.0.0.5:554/stream1", "rtsp://***@10.0.0.5:554/stream1"),
    ("rtsp://admin@10.0.0.5/s", "rtsp://***@10.0.0.5/s"),
    ("http://cam/video?user=admin&password=S3cr3t&res=720", "http://cam/video?user=***&password=***&res=720"),
    ("http://cam/v?token=abc#frag", "http://cam/v?token=***"),
    ("https://www.youtube.com/watch?v=8ObsKcpVKLs", "https://www.youtube.com/watch?v=8ObsKcpVKLs"),
    (r"C:\Users\PC\videos\a.mp4", r"C:\Users\PC\videos\a.mp4"),
    ("0", "0"),
])
def test_redact_source(src, expected):
    out = p.redact_source(src)
    assert out == expected
    assert "S3cr3t" not in out and "admin" not in out


def test_redact_text_in_error_messages():
    msg = "OpenCV: rtsp://admin:S3cr3t@10.0.0.5/s açılamadı; http://x/y?api_key=K3Y&a=1"
    out = p.redact_text(msg)
    assert "S3cr3t" not in out and "K3Y" not in out and "admin" not in out
    assert "rtsp://***@10.0.0.5/s" in out and "api_key=***&a=1" in out


# ---------------------------------------------------------------- HTTP sınıflandırma (12)
@pytest.mark.parametrize("status,outcome", [
    (200, "ok"), (201, "ok"), (204, "ok"),
    (408, "transient"), (425, "transient"), (429, "transient"), (500, "transient"), (502, "transient"),
    (503, "transient"), (504, "transient"),
    (400, "permanent"), (401, "permanent"), (403, "permanent"), (404, "permanent"), (413, "permanent"),
    (415, "permanent"), (422, "permanent"),
])
def test_classify(status, outcome):
    assert p.classify(status) == outcome


def test_parse_retry_after():
    assert p.parse_retry_after("3", 60) == 3.0
    assert p.parse_retry_after("999", 60) == 60.0
    assert p.parse_retry_after("-5", 60) == 0.0
    assert p.parse_retry_after(None, 60) is None and p.parse_retry_after("çöp", 60) is None
    future = (datetime.now(timezone.utc) + timedelta(seconds=30)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    assert 25 <= p.parse_retry_after(future, 60) <= 30
