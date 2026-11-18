"""FAZ 3 test yardımcıları: gerçek JPEG örnekleri ve FAZ 2 (detector/protocol.py) biçiminde payload üreticileri."""
import base64
import json
import uuid
from datetime import datetime, timedelta, timezone

# cv2.imencode ile üretilmiş 8x8 gerçek JPEG'ler (A ve B farklı içerik)
JPEG_A = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAIBAQEBAQIBAQECAgICAgQDAgICAgUEBAMEBgUGBgYFBgYGBwkIBgcJBwYGCAsICQoKCgoKBggLDAsKDAkKCgr/"
    "2wBDAQICAgICAgUDAwUKBwYHCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgr/wAARCAAIAAgDASIAAhEBAxEB/8QAHwAAAQUB"
    "AQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJico"
    "KSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ"
    "2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMi"
    "MoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOk"
    "paanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDi/wDi8H/C4P8AqU/+3f8A59/+/v8Arf8AOKKK"
    "K5MJhPqvtP3kp88nL3ne1/sx7RXRdD6DP8//ALf+qf7JRoewo06P7mnye05L/vaur5607/vKmnNZaKx//9k=")
JPEG_B = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAIBAQEBAQIBAQECAgICAgQDAgICAgUEBAMEBgUGBgYFBgYGBwkIBgcJBwYGCAsICQoKCgoKBggLDAsKDAkKCgr/"
    "2wBDAQICAgICAgUDAwUKBwYHCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgr/wAARCAAIAAgDASIAAhEBAxEB/8QAHwAAAQUB"
    "AQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJico"
    "KSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ"
    "2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMi"
    "MoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOk"
    "paanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDsP+LP/wDCn/8AqbP+3j/n4/79f6r/ADmiiiuv"
    "F4v617P93GHJFR91Wvb7Uu8n1fU+fyDIP7A+t/7XWr+3rVK376pz+z57fuqWi5KMLfu6evLd6u5//9k=")
FRAME = b"\xff\xd8\xff\xe0FRAME\xff\xd9"          # canlı kare (doğrulanmaz, eski davranış)
B64_A, B64_B = base64.b64encode(JPEG_A).decode(), base64.b64encode(JPEG_B).decode()
T0 = datetime(2026, 10, 28, 11, 29, tzinfo=timezone.utc)


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def event(kind="start", *, run_id, incident_id, cam="cam1", pid=7, t=2.1, at=T0, snapshot=None, **over):
    missing = "" if kind == "end" else "Baretsiz"
    ev = {"type": kind, "pid": pid, "t": t, "missing": missing, "helmet_rate": 0 if kind != "end" else 60,
          "vest_rate": 100, "box": [100, 100, 200, 400], "event_id": str(uuid.uuid4()), "run_id": str(run_id),
          "camera_id": cam, "incident_id": str(incident_id), "occurred_at": iso(at),
          "missing_helmet": kind != "end", "missing_vest": False}
    if snapshot is not None:
        ev["snapshot_b64"] = snapshot
    ev.update(over)
    return ev


def lifecycle(run_id, cam="cam1", pid=7, snapshot=B64_A):
    inc = uuid.uuid4()
    return inc, [event("start", run_id=run_id, incident_id=inc, cam=cam, pid=pid, t=2.1, snapshot=snapshot),
                 event("ongoing", run_id=run_id, incident_id=inc, cam=cam, pid=pid, t=5.1, at=T0 + timedelta(seconds=3)),
                 event("end", run_id=run_id, incident_id=inc, cam=cam, pid=pid, t=7.4, at=T0 + timedelta(seconds=5))]


def frame_payload(events=(), cam="cam1", run_id=None, source="videos/a.mp4", **meta):
    m = {"source": source, "source_kind": "file", "fps": 25.0, "width": 1280, "height": 720, "status": "running",
         "error": None, "loop": 1, "proc_fps": 24.0}
    if run_id is not None:
        m["run_id"] = str(run_id)
    m.update(meta)
    p = {"cam": cam, "name": f"Kamera {cam}", "meta": m,
         "summary": {"t": 2.1, "people": 1, "ok": 0, "viol": 1, "analyzing": 0, "raw_helmets": 0, "raw_vests": 1},
         "people": [{"pid": 7, "status": "IHLAL", "helmet": 0, "vest": 100}], "events": list(events)}
    if run_id is not None:
        p["run_id"], p["camera_id"] = str(run_id), cam
    return p


def post_frame(client, payload, headers=None, frame=FRAME):
    data = payload if isinstance(payload, str) else json.dumps(payload)
    return client.post("/ingest/frame", files={"frame": ("f.jpg", frame, "image/jpeg")}, data={"payload": data},
                       headers=headers or {})


def report(run_id, cam="cam1", status="finished", rows=None, **over):
    body = {"cam": cam, "name": f"Kamera {cam}", "status": "running", "run_id": str(run_id), "camera_id": cam,
            "run_status": status, "loop": 1, "source_kind": "file", "started_at": iso(T0 - timedelta(seconds=3)),
            "finished_at": iso(T0 + timedelta(seconds=30)), "video_time_sec": 30.0, "error": None,
            "unclosed_incidents": [], "unclosed_overflow": 0, "orphan_events": 0,
            "report": rows if rows is not None else [
                {"pid": 7, "first": 0.1, "last": 9.5, "n": 40, "helmet": 10, "vest": 95, "violation": True}]}
    body.update(over)
    return body
