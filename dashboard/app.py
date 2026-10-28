"""PPE dashboard servisi (Docker), çok kameralı. Model BURADA çalışmaz.
PC'deki dedektör her kamera için /ingest/frame ile etiketli kare + olay gönderir.
Tarayıcı tüm kareleri TEK WebSocket (/ws) üzerinden alır (tarayıcının 6 bağlantı sınırına takılmamak için)."""
import asyncio
import hmac
import logging
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile, WebSocket
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import DBAPIError, IntegrityError, SQLAlchemyError

from db import repository as repo
from db.health import check_ready
from db.session import get_session
from ingest import schemas, snapshots
from ingest.redact import redact_source, redact_text

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
INGEST_TOKEN = os.getenv("INGEST_TOKEN", "")
STALE_AFTER = float(os.getenv("STALE_AFTER", 5))
MAX_ALERTS = 300
# Sınırlar. Olay payload'ı (form alanı) Starlette tarafından zaten 1 MiB ile sınırlıdır.
MAX_FRAME_BYTES = int(os.getenv("MAX_FRAME_BYTES", 2 * 1024 * 1024))
MAX_EVENTS_PER_REQUEST = int(os.getenv("MAX_EVENTS_PER_REQUEST", 200))
MAX_SNAPSHOT_BYTES = int(os.getenv("MAX_SNAPSHOT_BYTES", 1024 * 1024))
MAX_REPORT_BYTES = int(os.getenv("MAX_REPORT_BYTES", 1024 * 1024))
MAX_REPORT_ROWS = int(os.getenv("MAX_REPORT_ROWS", 5000))
HEARTBEAT_SEC = float(os.getenv("HEARTBEAT_SEC", 30))     # olaysız karelerde en sık DB canlılık yazımı

log = logging.getLogger("ppe.ingest")
if not INGEST_TOKEN:
    log.warning("INGEST_TOKEN boş: /ingest uçları kimlik doğrulamasız (yalnızca 127.0.0.1'e bağlıyken kabul edilebilir).")

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
    if INGEST_TOKEN and not hmac.compare_digest((token or "").encode(), INGEST_TOKEN.encode()):
        _rlog("auth", "UYARI: geçersiz/eksik X-Token ile ingest isteği reddedildi (401). Dedektör bu olayları "
                      "DÜŞÜRÜR: detector/.env ve kök .env içindeki INGEST_TOKEN aynı olmalı.")
        raise HTTPException(status_code=401, detail="bad token")


_LAST_LOG: dict = {}


def _rlog(key, msg, every=10.0):
    now = time.monotonic()
    if now - _LAST_LOG.get(key, -every) >= every:
        _LAST_LOG[key] = now
        log.warning(msg)


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


def push_alert(ev: dict, cam: Cam, snapshot_path: Optional[str] = None):
    """Yalnızca DB'ye YENİ yazılmış olay için çağrılır (commit sonrası). id = RAM sırası (DB id'si değil)."""
    global ALERT_SEQ
    with LOCK:
        ALERT_SEQ += 1
        ev["id"] = ALERT_SEQ
        ev["cam"], ev["cam_name"] = cam.id, cam.name
        ev["wall"] = time.strftime("%H:%M:%S")
        ev.pop("snapshot_b64", None)
        ev.pop("snapshot", None)                     # payload'dan gelen yola güvenilmez
        if snapshot_path:
            ev["snapshot"] = "/" + snapshot_path
        ALERTS.append(ev)
        del ALERTS[:-MAX_ALERTS]


# ---------------- dedektörden gelen veri ----------------
def _http_error(status: int, detail):
    raise HTTPException(status_code=status, detail=detail)


def _db_failure(exc: Exception):
    """DB hatasını HTTP'ye çevirir; bağlantı adresi/parola mesaja girmez."""
    if isinstance(exc, IntegrityError):
        code = getattr(exc.orig, "sqlstate", None)
        if code in ("23505", "23503"):           # benzersizlik / FK: aynı kimlikle çelişkili kayıt
            _http_error(409, "kayıt mevcut verilerle çelişiyor")
        if code == "23514":
            _http_error(422, "kayıt veritabanı kısıtlarını ihlal ediyor")
    if isinstance(exc, DBAPIError) and getattr(exc.orig, "sqlstate", None) in ("40001", "40P01"):
        _rlog("db_retry", "UYARI: eşzamanlılık çakışması (deadlock/serialization); istemci tekrar deneyecek.")
    else:
        _rlog("db_down", f"HATA: veritabanı yazımı başarısız ({type(exc).__name__}); 503 dönülüyor.")
    _http_error(503, "veritabanı şu an kullanılamıyor; tekrar deneyin")


def _safe_meta(meta: dict) -> dict:
    meta = dict(meta)
    if "source" in meta:
        meta["source"] = redact_source(meta["source"])
    if meta.get("error"):
        meta["error"] = redact_text(meta["error"])[:300]
    if meta.get("source_kind") not in schemas.SOURCE_KINDS:
        meta.pop("source_kind", None)
    return meta


def _meta_run(data: schemas.FramePayload, cam_id: str) -> dict:
    """Karenin ait olduğu run'ın (yalnızca run_id geçerliyse) metadata'sı."""
    m = data.meta
    run_id = data.run_id
    if run_id is None:
        try:
            run_id = uuid.UUID(str(m.get("run_id")))
        except ValueError:
            run_id = None

    def pos_int(v):
        return v if isinstance(v, int) and not isinstance(v, bool) and 0 < v < 1_000_000 else None

    fps = m.get("fps")
    fps = round(fps, 2) if isinstance(fps, (int, float)) and not isinstance(fps, bool) and 0 < fps < 10_000 else None
    return {"run_id": run_id, "loop_no": pos_int(m.get("loop")), "fps": fps, "width": pos_int(m.get("width")),
            "height": pos_int(m.get("height")),
            "source_kind": m.get("source_kind") if m.get("source_kind") in schemas.SOURCE_KINDS else None,
            "camera_id": cam_id}


_HB: dict = {}                                    # cam_id -> (monotonic, run_id)
_HB_LOCK = threading.Lock()


def _heartbeat(cam: Cam, run: dict):
    """Olaysız karelerde seyrek (HEARTBEAT_SEC veya run değişince) kamera/run canlılığı. Başarısızlık canlı
    akışı etkilemez (best effort)."""
    now = time.monotonic()
    with _HB_LOCK:
        last = _HB.get(cam.id)
        if last and now - last[0] < HEARTBEAT_SEC and last[1] == run["run_id"]:
            return
        _HB[cam.id] = (now, run["run_id"])
    try:
        with get_session() as s, s.begin():
            repo.heartbeat(s, camera_id=cam.id, name=cam.name, source_kind=run["source_kind"],
                           run_id=run["run_id"], loop_no=run["loop_no"], fps=run["fps"], width=run["width"],
                           height=run["height"])
    except (SQLAlchemyError, RuntimeError, repo.Conflict) as exc:
        _rlog("hb", f"UYARI: canlılık kaydı yazılamadı ({type(exc).__name__}); canlı akış etkilenmedi.")


def _persist_events(cam: Cam, data: schemas.FramePayload, raw_events: list) -> list:
    """Olayları doğrular, snapshot'ları hazırlar, tek transaction'da yazar. Döner: [(ham_olay, snapshot_yolu)]
    yalnızca GERÇEKTEN yeni olaylar için (commit sonrası RAM'e eklenecek)."""
    if len(raw_events) > MAX_EVENTS_PER_REQUEST:
        _http_error(413, f"istek başına en fazla {MAX_EVENTS_PER_REQUEST} olay")
    events, errors = [], []
    for i, raw in enumerate(raw_events):
        try:
            events.append(schemas.validate(schemas.EventIn, raw, ("events", i)))
        except schemas.PayloadError as exc:
            errors.extend(exc.errors)
    if errors:
        _http_error(422, errors)

    # aynı istek içindeki tekrarlar: aynı içerik -> tek kayıt, farklı içerik -> 409
    uniq: dict = {}
    for ev, raw in zip(events, raw_events):
        prev = uniq.get(ev.event_id)
        if prev is None:
            uniq[ev.event_id] = (ev, raw)
        elif prev[0].identity() != ev.identity() or prev[0].snapshot_b64 != ev.snapshot_b64:
            _http_error(409, f"event_id {ev.event_id} aynı istekte farklı içerikle tekrarlandı")
    run_cams: dict = {}
    for ev, _ in uniq.values():
        if run_cams.setdefault(ev.run_id, ev.camera_id) != ev.camera_id:
            _http_error(409, f"run {ev.run_id} aynı istekte farklı kameralarla geldi")

    # snapshot: commit'ten önce doğrulanır ve yazılır (idempotent; farklı içerik -> 409)
    paths = {}
    for ev, _ in uniq.values():
        if ev.snapshot_b64 is not None:
            try:
                paths[ev.event_id] = snapshots.store(
                    SNAP_DIR, ev.event_id, snapshots.decode(ev.snapshot_b64, MAX_SNAPSHOT_BYTES))
            except snapshots.SnapshotInvalid as exc:
                _http_error(422, [{"loc": ["events", str(ev.event_id), "snapshot_b64"], "msg": str(exc),
                                   "type": "snapshot_invalid"}])
            except snapshots.SnapshotConflict:
                _http_error(409, f"event_id {ev.event_id} için farklı içerikli snapshot zaten var")

    meta_run = _meta_run(data, cam.id)
    rows = [{"event_id": ev.event_id, "incident_id": ev.incident_id, "run_id": ev.run_id, "camera_id": ev.camera_id,
             "pid": ev.pid, "event_type": ev.type, "missing_helmet": ev.missing_helmet, "missing_vest": ev.missing_vest,
             "missing_text": ev.missing, "helmet_rate": ev.helmet_rate, "vest_rate": ev.vest_rate,
             "video_time_sec": ev.video_time_sec, "occurred_at": ev.occurred_at, "box": list(ev.box),
             "snapshot_path": paths.get(ev.event_id)} for ev, _ in uniq.values()]
    try:
        with get_session() as s, s.begin():
            for cid in sorted({ev.camera_id for ev, _ in uniq.values()}):
                repo.upsert_camera(s, cid, cam.name if cid == cam.id else cid,
                                   meta_run["source_kind"] if cid == cam.id else None)
            for run_id in sorted(run_cams, key=str):
                first = min(ev.occurred_at for ev, _ in uniq.values() if ev.run_id == run_id)
                extra = ({k: meta_run[k] for k in ("loop_no", "fps", "width", "height")}
                         if run_id == meta_run["run_id"] and run_cams[run_id] == cam.id else {})
                repo.upsert_run(s, run_id, run_cams[run_id], started_at=first, **extra)
            repo.upsert_incidents(s, [{"id": ev.incident_id, "run_id": ev.run_id, "camera_id": ev.camera_id,
                                       "pid": ev.pid} for ev, _ in uniq.values()])
            inserted = repo.insert_events(s, rows, {ev.event_id: ev.identity() for ev, _ in uniq.values()})
    except repo.Conflict as exc:
        _http_error(409, str(exc))
    except (SQLAlchemyError, RuntimeError) as exc:
        _db_failure(exc)
    return [(raw, paths.get(ev.event_id)) for ev, raw in uniq.values() if ev.event_id in inserted]


@app.post("/ingest/frame")
def ingest_frame(frame: UploadFile = File(...), payload: str = Form(...),
                 x_token: Optional[str] = Header(None)):
    _auth(x_token)
    jpg = frame.file.read(MAX_FRAME_BYTES + 1)
    if len(jpg) > MAX_FRAME_BYTES:
        _http_error(413, f"kare {MAX_FRAME_BYTES} bayt sınırını aşıyor")
    try:
        data = schemas.validate(schemas.FramePayload, schemas.load_json(payload, "payload"))
    except schemas.PayloadError as exc:
        _http_error(422, exc.errors)
    cam = get_cam(data.cam, redact_text(data.name) if data.name else None)
    # canlı kare DB'den bağımsız olarak her durumda güncellenir
    with cam.cond:
        cam.jpeg = jpg
        cam.frame_no += 1
        cam.last_ingest = time.time()
        cam.meta.update(_safe_meta(data.meta))
        cam.summary = data.summary if data.summary is not None else cam.summary
        cam.people = data.people
        cam.cond.notify_all()
    if data.events:
        for raw, path in _persist_events(cam, data, data.events):   # 2xx yalnızca commit sonrası
            target = cam if raw.get("camera_id") == cam.id else get_cam(raw.get("camera_id"))
            push_alert(raw, target, path)
    else:
        _heartbeat(cam, _meta_run(data, cam.id))
    return {"ok": True}


def _persist_report(rep: schemas.ReportIn):
    meta = {"unclosed_incidents": [dict(u.model_dump(mode="json")) for u in rep.unclosed_incidents],
            "unclosed_overflow": rep.unclosed_overflow, "orphan_events": rep.orphan_events,
            "error": redact_text(rep.error)[:300] if rep.error else None,
            "video_time_sec": rep.video_time_sec}
    rows = [{"run_id": rep.run_id, "pid": r.pid, "first_sec": schemas.tenth(r.first, "first"),
             "last_sec": schemas.tenth(r.last, "last"), "observations": r.n, "helmet_rate": r.helmet,
             "vest_rate": r.vest, "ever_violation": r.violation} for r in rep.report]
    try:
        with get_session() as s, s.begin():
            repo.upsert_camera(s, rep.camera_id, redact_text(rep.name) if rep.name else rep.camera_id, rep.source_kind)
            repo.complete_run(s, run_id=rep.run_id, camera_id=rep.camera_id, run_status=rep.run_status,
                              started_at=rep.started_at, finished_at=rep.finished_at, loop_no=rep.loop,
                              metadata=meta, rows=rows)
    except repo.Conflict as exc:
        _http_error(409, str(exc))
    except (SQLAlchemyError, RuntimeError) as exc:
        _db_failure(exc)


@app.post("/ingest/report")
async def ingest_report(request: Request, x_token: Optional[str] = Header(None)):
    _auth(x_token)
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_REPORT_BYTES:
            _http_error(413, f"rapor {MAX_REPORT_BYTES} bayt sınırını aşıyor")
    try:
        raw = schemas.load_json(bytes(body), "body")
        if not isinstance(raw, dict):
            raise schemas.PayloadError([{"loc": ["body"], "msg": "JSON nesnesi olmalı", "type": "dict_type"}])
        if len(raw.get("report") or []) > MAX_REPORT_ROWS:
            _http_error(413, f"rapor başına en fazla {MAX_REPORT_ROWS} kişi satırı")
        rep = schemas.validate(schemas.ReportIn, raw)
    except schemas.PayloadError as exc:
        _http_error(422, exc.errors)
    await run_in_threadpool(_persist_report, rep)                    # 2xx yalnızca commit sonrası
    cam = get_cam(rep.cam or rep.camera_id, redact_text(rep.name) if rep.name else None)
    cam.report = raw["report"]
    if rep.status:
        cam.meta["status"] = rep.status
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


@app.get("/health/ready")
def health_ready():
    """DB erişimi + beklenen Alembic revision. /healthz'den bağımsızdır (canlılık değil hazır olma)."""
    ok, detail = check_ready()
    return JSONResponse(detail, status_code=200 if ok else 503)