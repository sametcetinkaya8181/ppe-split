"""PPE dashboard servisi (Docker). Model BURADA çalışmaz.
PC'deki dedektör (detector/detector.py) etiketli kareyi ve olayları /ingest/* ile gönderir;
bu servis onları MJPEG + JSON olarak tarayıcıya sunar. API, eski index.html ile uyumludur."""
import base64
import json
import os
import threading
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
INGEST_TOKEN = os.getenv("INGEST_TOKEN", "")
STALE_AFTER = float(os.getenv("STALE_AFTER", 5))   # sn: bu kadar süre kare gelmezse "bekleniyor"
MAX_ALERTS = 200

SNAP_DIR = DATA_DIR / "snapshots"
SNAP_DIR.mkdir(parents=True, exist_ok=True)


class Shared:
    def __init__(self):
        self.cond = threading.Condition()
        self.jpeg = None
        self.frame_no = 0
        self.last_ingest = 0.0
        self.alerts = []
        self.alert_seq = 0
        self.people = []
        self.summary = {"t": 0, "people": 0, "ok": 0, "viol": 0, "analyzing": 0,
                        "raw_helmets": 0, "raw_vests": 0}
        self.meta = {"source": "-", "fps": 0, "width": 0, "height": 0,
                     "status": "waiting_for_detector", "error": None, "loop": 0, "proc_fps": 0.0}
        self.report = []
        self.lock = threading.Lock()


S = Shared()
app = FastAPI(title="PPE Dashboard")
STATIC = Path(__file__).parent / "static"
app.mount("/snapshots", StaticFiles(directory=SNAP_DIR), name="snapshots")


def _auth(token: Optional[str]):
    if INGEST_TOKEN and token != INGEST_TOKEN:
        raise HTTPException(status_code=401, detail="bad token")


def push_alert(ev: dict):
    with S.lock:
        S.alert_seq += 1
        ev["id"] = S.alert_seq
        ev["wall"] = time.strftime("%H:%M:%S")
        b64 = ev.pop("snapshot_b64", None)
        if b64:
            name = f"alert_{ev['id']}_ID{ev['pid']}.jpg"
            (SNAP_DIR / name).write_bytes(base64.b64decode(b64))
            ev["snapshot"] = f"/snapshots/{name}"
        S.alerts.append(ev)
        del S.alerts[:-MAX_ALERTS]


# ---------------- dedektörden gelen veri ----------------
@app.post("/ingest/frame")
def ingest_frame(frame: UploadFile = File(...), payload: str = Form(...),
                 x_token: Optional[str] = Header(None)):
    _auth(x_token)
    jpg = frame.file.read()
    data = json.loads(payload)
    for ev in data.get("events", []):
        push_alert(ev)
    with S.cond:
        S.jpeg = jpg
        S.frame_no += 1
        S.last_ingest = time.time()
        S.meta.update(data.get("meta", {}))
        S.summary = data.get("summary", S.summary)
        S.people = data.get("people", [])
        S.cond.notify_all()
    return {"ok": True}


@app.post("/ingest/report")
def ingest_report(body: dict, x_token: Optional[str] = Header(None)):
    _auth(x_token)
    with S.lock:
        S.report = body.get("report", [])
        if body.get("status"):
            S.meta["status"] = body["status"]
    return {"ok": True}


# ---------------- tarayıcıya sunulanlar ----------------
@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/video_feed")
def video_feed():
    def gen():
        last = -1
        while True:
            with S.cond:
                S.cond.wait_for(lambda: S.frame_no != last, timeout=5)
                jpg, last = S.jpeg, S.frame_no
            if jpg is None:
                continue
            yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                   + str(len(jpg)).encode() + b"\r\n\r\n" + jpg + b"\r\n")
    return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame")


def _meta():
    m = dict(S.meta)
    if m["status"] == "running" and time.time() - S.last_ingest > STALE_AFTER:
        m["status"] = "waiting_for_detector"
    return m


@app.get("/api/state")
def state(after: int = 0):
    with S.lock:
        new_alerts = [a for a in S.alerts if a["id"] > after]
        return JSONResponse({"meta": _meta(), "summary": S.summary, "people": S.people,
                             "alerts": new_alerts, "last_alert_id": S.alert_seq})


@app.get("/api/alerts")
def alerts():
    with S.lock:
        return list(reversed(S.alerts))


@app.get("/api/report")
def report():
    with S.lock:
        return S.report


@app.get("/healthz")
def health():
    return {"status": _meta()["status"]}
