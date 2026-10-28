"""PPE dashboard servisi (Docker), çok kameralı. Model BURADA çalışmaz.
PC'deki dedektör her kamera için /ingest/frame ile etiketli kare + olay gönderir.
Tarayıcı tüm kareleri TEK WebSocket (/ws) üzerinden alır (tarayıcının 6 bağlantı sınırına takılmamak için)."""
import asyncio
import base64
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile, WebSocket
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
INGEST_TOKEN = os.getenv("INGEST_TOKEN", "")
STALE_AFTER = float(os.getenv("STALE_AFTER", 5))
MAX_ALERTS = 300

SNAP_DIR = DATA_DIR / "snapshots"
SNAP_DIR.mkdir(parents=True, exist_ok=True)

EMPTY_SUMMARY = {"t": 0, "people": 0, "ok": 0, "viol": 0, "analyzing": 0,
                 "raw_helmets": 0, "raw_vests": 0}


class Cam:
    def __init__(self, cid, idx, name):
        self.id, self.idx, self.name = cid, idx, name
        self.cond = threading.Condition()
        self.jpeg, self.frame_no, self.last_ingest = None, 0, 0.0
        self.summary = dict(EMPTY_SUMMARY)
        self.people = []
        self.report = []
        self.meta = {"source": "-", "fps": 0, "width": 0, "height": 0, "status": "starting",
                     "error": None, "loop": 0, "proc_fps": 0.0}


LOCK = threading.Lock()          # CAMS sözlüğü + uyarılar
CAMS = {}
ALERTS, ALERT_SEQ = [], 0

app = FastAPI(title="PPE Dashboard")
STATIC = Path(__file__).parent / "static"
app.mount("/snapshots", StaticFiles(directory=SNAP_DIR), name="snapshots")


def _auth(token: Optional[str]):
    if INGEST_TOKEN and token != INGEST_TOKEN:
        raise HTTPException(status_code=401, detail="bad token")


def get_cam(cid: str, name: Optional[str] = None) -> Cam:
    cid = re.sub(r"[^A-Za-z0-9_-]", "_", cid or "cam1")[:40]
    with LOCK:
        cam = CAMS.get(cid)
        if cam is None:
            if len(CAMS) >= 255:
                raise HTTPException(status_code=400, detail="too many cameras")
            cam = CAMS[cid] = Cam(cid, len(CAMS), name or cid)
        if name:
            cam.name = name
    return cam


def push_alert(ev: dict, cam: Cam):
    global ALERT_SEQ
    with LOCK:
        ALERT_SEQ += 1
        ev["id"] = ALERT_SEQ
        ev["cam"], ev["cam_name"] = cam.id, cam.name
        ev["wall"] = time.strftime("%H:%M:%S")
        b64 = ev.pop("snapshot_b64", None)
        if b64:
            name = f"alert_{ev['id']}_{cam.id}_ID{ev['pid']}.jpg"
            (SNAP_DIR / name).write_bytes(base64.b64decode(b64))
            ev["snapshot"] = f"/snapshots/{name}"
        ALERTS.append(ev)
        del ALERTS[:-MAX_ALERTS]


# ---------------- dedektörden gelen veri ----------------
@app.post("/ingest/frame")
def ingest_frame(frame: UploadFile = File(...), payload: str = Form(...),
                 x_token: Optional[str] = Header(None)):
    _auth(x_token)
    jpg = frame.file.read()
    data = json.loads(payload)
    cam = get_cam(data.get("cam"), data.get("name"))
    for ev in data.get("events", []):
        push_alert(ev, cam)
    with cam.cond:
        cam.jpeg = jpg
        cam.frame_no += 1
        cam.last_ingest = time.time()
        cam.meta.update(data.get("meta", {}))
        cam.summary = data.get("summary", cam.summary)
        cam.people = data.get("people", [])
        cam.cond.notify_all()
    return {"ok": True}


@app.post("/ingest/report")
def ingest_report(body: dict, x_token: Optional[str] = Header(None)):
    _auth(x_token)
    cam = get_cam(body.get("cam"), body.get("name"))
    cam.report = body.get("report", [])
    if body.get("status"):
        cam.meta["status"] = body["status"]
    return {"ok": True}


# ---------------- tarayıcıya sunulanlar ----------------
@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.websocket("/ws")
async def ws_feed(ws: WebSocket):
    """Mesaj: 1 bayt kamera indeksi + JPEG. Yavaş istemcide ara kareler atlanır (hep en yenisi gider)."""
    await ws.accept()
    sent = {}
    try:
        while True:
            with LOCK:
                cams = list(CAMS.values())
            any_sent = False
            for c in cams:
                jpg, no = c.jpeg, c.frame_no
                if jpg is not None and sent.get(c.id) != no:
                    sent[c.id] = no
                    await ws.send_bytes(bytes([c.idx]) + jpg)
                    any_sent = True
            if not any_sent:
                await asyncio.sleep(0.03)
    except Exception:            # istemci ayrıldı
        pass


def _mjpeg(cam: Cam):
    def gen():
        last = -1
        while True:
            with cam.cond:
                cam.cond.wait_for(lambda: cam.frame_no != last, timeout=5)
                jpg, last = cam.jpeg, cam.frame_no
            if jpg is None:
                continue
            yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                   + str(len(jpg)).encode() + b"\r\n\r\n" + jpg + b"\r\n")
    return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame")


@app.get("/video_feed/{cid}")
def video_feed(cid: str):
    with LOCK:
        cam = CAMS.get(cid)
    if cam is None:
        raise HTTPException(status_code=404, detail="no such camera")
    return _mjpeg(cam)


def _meta(c: Cam):
    m = dict(c.meta)
    if m["status"] == "running" and time.time() - c.last_ingest > STALE_AFTER:
        m["status"] = "waiting_for_detector"
    return m


@app.get("/api/state")
def state(after: int = 0):
    with LOCK:
        cams = sorted(CAMS.values(), key=lambda c: c.idx)
        new_alerts = [a for a in ALERTS if a["id"] > after]
        seq = ALERT_SEQ
    cameras = [{"id": c.id, "idx": c.idx, "name": c.name, "meta": _meta(c),
                "summary": c.summary, "people": c.people} for c in cams]
    totals = {k: sum(c.summary.get(k, 0) for c in cams) for k in ("people", "ok", "viol", "analyzing")}
    return JSONResponse({"cameras": cameras, "totals": totals,
                         "alerts": new_alerts, "last_alert_id": seq})


@app.get("/api/alerts")
def alerts():
    with LOCK:
        return list(reversed(ALERTS))


@app.get("/api/report")
def report():
    with LOCK:
        cams = sorted(CAMS.values(), key=lambda c: c.idx)
    return [{**r, "cam": c.id, "cam_name": c.name} for c in cams for r in c.report]


@app.get("/healthz")
def health():
    with LOCK:
        cams = list(CAMS.values())
    return {"cameras": len(cams), "running": sum(_meta(c)["status"] == "running" for c in cams)}