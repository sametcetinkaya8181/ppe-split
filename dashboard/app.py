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
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile, WebSocket
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlalchemy.exc import DBAPIError, IntegrityError, SQLAlchemyError

import observability as ops
from auth import routes as auth_routes
from auth import security
from auth.routes import require
from auth.security import AuthCtx
from db import history
from db import repository as repo
from db.health import check_ready
from db.session import get_engine, get_session
from ingest import schemas, snapshots
from ingest.redact import redact_source, redact_text

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
INGEST_TOKEN = os.getenv("INGEST_TOKEN", "")
# Kesintisiz token rotasyonu: geçiş süresince ikinci (yeni) token da kabul edilir. Bkz. docs/SECURITY.md.
INGEST_TOKEN_NEXT = os.getenv("INGEST_TOKEN_NEXT", "")
STALE_AFTER = float(os.getenv("STALE_AFTER", 5))
MAX_ALERTS = 300
# Sınırlar. Olay payload'ı (form alanı) Starlette tarafından zaten 1 MiB ile sınırlıdır.
MAX_FRAME_BYTES = int(os.getenv("MAX_FRAME_BYTES", 2 * 1024 * 1024))
MAX_EVENTS_PER_REQUEST = int(os.getenv("MAX_EVENTS_PER_REQUEST", 200))
MAX_SNAPSHOT_BYTES = int(os.getenv("MAX_SNAPSHOT_BYTES", 1024 * 1024))
MAX_REPORT_BYTES = int(os.getenv("MAX_REPORT_BYTES", 1024 * 1024))
MAX_REPORT_ROWS = int(os.getenv("MAX_REPORT_ROWS", 5000))
MAX_EVENTS_BODY_BYTES = int(os.getenv("MAX_EVENTS_BODY_BYTES", 8 * 1024 * 1024))   # /ingest/events (outbox batch)
HEARTBEAT_SEC = float(os.getenv("HEARTBEAT_SEC", 30))     # olaysız karelerde en sık DB canlılık yazımı
WS_RECHECK_SEC = float(os.getenv("WS_RECHECK_SEC", 15))   # açık WebSocket'te oturumun yeniden doğrulanma aralığı

log = logging.getLogger("ppe.ingest")
for _w in security.validate_config(tuple(t for t in (INGEST_TOKEN, INGEST_TOKEN_NEXT) if t)):
    log.warning(_w)

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

# OpenAPI/Swagger kapalı: API yüzeyini anonim kullanıcıya listelemez.
app = FastAPI(title="PPE Dashboard", docs_url=None, redoc_url=None, openapi_url=None)
STATIC = Path(__file__).parent / "static"
INDEX_HTML = (STATIC / "index.html").read_bytes()     # bellekten sunulur (Range işleme yolu yok)


def _ingest_rejected(conn):
    _rlog("auth", "UYARI: geçersiz/eksik X-Token ile ingest isteği reddedildi (401). Dedektör bu olayları "
                  "DÜŞÜRÜR: detector/.env ve kök .env içindeki INGEST_TOKEN aynı olmalı.")
    security.audit("ingest_auth", "denied", conn=conn, detail={"path": conn.url.path[:120]},
                   dedupe=f"ingest:{security.client_ip(conn)}")


# Makine token'ı middleware'de gövde okunmadan denetlenir; _auth handler içinde ikinci savunma hattıdır.
app.add_middleware(security.SecurityMiddleware, csp=security.csp_for(STATIC / "index.html"),
                   ingest_tokens=lambda: (INGEST_TOKEN, INGEST_TOKEN_NEXT), on_ingest_reject=_ingest_rejected)
# FAZ 9: en dıştaki katman — güvenlik retleri (400/401) dahil tüm HTTP isteklerini route şablonuyla sayar.
app.add_middleware(ops.OpsMiddleware, static_routes=("/ingest/frame", "/ingest/events", "/ingest/report", "/healthz",
                                                     "/health/ready", "/api/state", "/ws"))
app.include_router(auth_routes.router)


def _auth(token: Optional[str], request: Optional[Request] = None):
    """Makine kimliği (dedektör). Tarayıcı oturum çerezi burada HİÇ dikkate alınmaz."""
    tokens = [t for t in (INGEST_TOKEN, INGEST_TOKEN_NEXT) if t]
    if not tokens:
        return
    given = (token or "").encode()
    if not any([hmac.compare_digest(given, t.encode()) for t in tokens]):
        if request is not None:
            _ingest_rejected(request)
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
    if "health" in meta:                          # FAZ 9: dedektör sağlık/kapsam özeti — yalnız beyaz liste
        meta["health"] = ops.sanitize_health(meta["health"])
    if "outbox" in meta:
        meta["outbox"] = ops.sanitize_outbox(meta["outbox"])
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
    kind = m.get("source_kind") if m.get("source_kind") in schemas.SOURCE_KINDS else None
    return {"run_id": run_id, "loop_no": pos_int(m.get("loop")), "fps": fps, "width": pos_int(m.get("width")),
            "height": pos_int(m.get("height")), "source_kind": kind, "camera_id": cam_id,
            "source_label": redact_text(data.name)[:120] if data.name and data.name.strip() else None,
            "source_ref": _source_ref(m.get("source"), kind)}


def _source_ref(source, kind) -> Optional[str]:
    """Run'a yazılacak kimlik bilgisi içermeyen kaynak referansı: dosya için yalnızca dosya adı (kullanıcı
    klasör yolu saklanmaz), URL için sunucu tarafında maskelenmiş adres."""
    if not isinstance(source, str) or not source.strip() or source == "-":
        return None
    if kind == "file" or (kind is None and "://" not in source):
        return redact_text(re.split(r"[\\/]", source.strip())[-1])[:300] or None
    return redact_source(source)[:300]


_HB: dict = {}                                    # cam_id -> (monotonic, run_id)
_HB_LOCK = threading.Lock()
_HB_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="heartbeat")
_HB_PENDING: dict = {}                            # cam_id -> Future (kamera başına en fazla bir bekleyen yazım)


def _heartbeat(cam: Cam, run: dict):
    """Olaysız karelerde seyrek (HEARTBEAT_SEC veya run değişince) kamera/run canlılığı. Yazım ARKA PLANDA
    yapılır: DB yavaş veya kapalıyken canlı kare isteği beklemez. Başarısızlık canlı akışı etkilemez (best effort);
    olay taşıyan istekler ise hâlâ yalnızca commit sonrası 2xx alır."""
    now = time.monotonic()
    with _HB_LOCK:
        last = _HB.get(cam.id)
        if last and now - last[0] < HEARTBEAT_SEC and last[1] == run["run_id"]:
            return
        pending = _HB_PENDING.get(cam.id)
        if pending is not None and not pending.done():
            return                                # önceki yazım hâlâ sürüyor (DB yavaş): yığılma yok
        _HB[cam.id] = (now, run["run_id"])
        _HB_PENDING[cam.id] = _HB_POOL.submit(_write_heartbeat, cam.id, cam.name, run)


def _write_heartbeat(cam_id: str, name: str, run: dict):
    try:
        with ops.REG.db_write_timer("heartbeat"), get_session() as s, s.begin():
            repo.heartbeat(s, camera_id=cam_id, name=name, source_kind=run["source_kind"],
                           run_id=run["run_id"], loop_no=run["loop_no"], fps=run["fps"], width=run["width"],
                           height=run["height"], source_label=run["source_label"], source_ref=run["source_ref"])
    except (SQLAlchemyError, RuntimeError, repo.Conflict) as exc:
        _rlog("hb", f"UYARI: canlılık kaydı yazılamadı ({type(exc).__name__}); canlı akış etkilenmedi.")


def flush_heartbeats(timeout: float = 10.0):
    """Bekleyen arka plan canlılık yazımlarını bekler (testler ve kapanış için)."""
    with _HB_LOCK:
        pending = list(_HB_PENDING.values())
    for f in pending:
        f.result(timeout)


def _persist_events(cam: Cam, data: schemas.FramePayload, raw_events: list, run_labels: Optional[dict] = None) -> list:
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
        with ops.REG.db_write_timer("events"), get_session() as s, s.begin():
            for cid in sorted({ev.camera_id for ev, _ in uniq.values()}):
                repo.upsert_camera(s, cid, cam.name if cid == cam.id else cid,
                                   meta_run["source_kind"] if cid == cam.id else None)
            for run_id in sorted(run_cams, key=str):
                first = min(ev.occurred_at for ev, _ in uniq.values() if ev.run_id == run_id)
                extra = ({k: meta_run[k] for k in ("loop_no", "fps", "width", "height", "source_label", "source_kind",
                                                   "source_ref")}
                         if run_id == meta_run["run_id"] and run_cams[run_id] == cam.id else {})
                if run_labels and run_labels.get(run_id) and not extra.get("source_label"):
                    extra = {**extra, "source_label": run_labels[run_id]}     # olayla dondurulmuş etiket (ilk değer kalır)
                repo.upsert_run(s, run_id, run_cams[run_id], started_at=first, **extra)
            repo.upsert_incidents(s, [{"id": ev.incident_id, "run_id": ev.run_id, "camera_id": ev.camera_id,
                                       "pid": ev.pid} for ev, _ in uniq.values()])
            inserted = repo.insert_events(s, rows, {ev.event_id: ev.identity() for ev, _ in uniq.values()})
    except repo.Conflict as exc:
        _http_error(409, str(exc))
    except (SQLAlchemyError, RuntimeError) as exc:
        _db_failure(exc)
    ops.REG.count("events_received", len(uniq))
    ops.REG.count("events_inserted", len(inserted))
    ops.REG.count("events_duplicate_ignored", len(uniq) - len(inserted))
    return [(raw, paths.get(ev.event_id)) for ev, raw in uniq.values() if ev.event_id in inserted]


@app.post("/ingest/frame")
def ingest_frame(request: Request, frame: UploadFile = File(...), payload: str = Form(...),
                 x_token: Optional[str] = Header(None)):
    _auth(x_token, request)
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
    ops.REG.count("frames_received")
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
    if rep.end_reason is not None:                # yalnız varsa: eski raporların birebir tekrarı aynı kalır
        meta["end_reason"] = rep.end_reason
    rows = [{"run_id": rep.run_id, "pid": r.pid, "first_sec": schemas.tenth(r.first, "first"),
             "last_sec": schemas.tenth(r.last, "last"), "observations": r.n, "helmet_rate": r.helmet,
             "vest_rate": r.vest, "ever_violation": r.violation} for r in rep.report]
    try:
        with ops.REG.db_write_timer("report"), get_session() as s, s.begin():
            repo.upsert_camera(s, rep.camera_id, redact_text(rep.name) if rep.name else rep.camera_id, rep.source_kind)
            repo.complete_run(s, run_id=rep.run_id, camera_id=rep.camera_id, run_status=rep.run_status,
                              started_at=rep.started_at, finished_at=rep.finished_at, loop_no=rep.loop,
                              metadata=meta, rows=rows,
                              source_label=redact_text(rep.name)[:120] if rep.name and rep.name.strip() else None,
                              source_kind=rep.source_kind)
    except repo.Conflict as exc:
        _http_error(409, str(exc))
    except (SQLAlchemyError, RuntimeError) as exc:
        _db_failure(exc)
    ops.REG.count("reports_persisted")


@app.post("/ingest/report")
async def ingest_report(request: Request, x_token: Optional[str] = Header(None)):
    await run_in_threadpool(_auth, x_token, request)
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


@app.post("/ingest/events")
async def ingest_events(request: Request, x_token: Optional[str] = Header(None)):
    """Yalnız olay (kare YOK): dedektörün kalıcı outbox'ından teslim (FAZ 7). Gövde JSON {cam, name, events}.
    /ingest/frame ile aynı doğrulama, transaction ve event_id idempotency'si; 2xx yalnız commit sonrası.
    Canlı video/WebSocket'e dokunmaz (sahte kare üretilmez)."""
    await run_in_threadpool(_auth, x_token, request)
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_EVENTS_BODY_BYTES:
            _http_error(413, f"olay gövdesi {MAX_EVENTS_BODY_BYTES} bayt sınırını aşıyor")
    try:
        raw = schemas.load_json(bytes(body), "body")
        if not isinstance(raw, dict) or not isinstance(raw.get("events"), list):
            raise schemas.PayloadError([{"loc": ["body", "events"], "msg": "events listesi gerekli", "type": "list_type"}])
        data = schemas.validate(schemas.FramePayload, {"cam": raw.get("cam"), "name": raw.get("name"),
                                                       "events": raw["events"]})
    except schemas.PayloadError as exc:
        _http_error(422, exc.errors)
    if not data.events:
        return {"ok": True, "inserted": 0}
    cam = get_cam(data.cam, redact_text(data.name) if data.name else None)
    labels = {}
    for e in data.events:                      # outbox olayları kendi run etiketini taşır (FAZ 7)
        lbl, rid = e.get("source_label"), e.get("run_id")
        if isinstance(lbl, str) and lbl.strip() and rid:
            try:
                labels.setdefault(uuid.UUID(str(rid)), redact_text(lbl)[:120])
            except ValueError:
                pass
    new = await run_in_threadpool(_persist_events, cam, data, data.events, labels)   # 2xx yalnız commit sonrası
    for raw_ev, path in new:
        target = cam if raw_ev.get("camera_id") == cam.id else get_cam(raw_ev.get("camera_id"))
        push_alert(raw_ev, target, path)
    return {"ok": True, "inserted": len(new)}


# ---------------- tarayıcıya sunulanlar ----------------
@app.get("/")
def index():
    """Yalnız uygulama kabuğu (veri içermez); veriler oturumlu API'lerden gelir. Giriş ekranı bu sayfadadır."""
    return Response(INDEX_HTML, media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-store"})


SNAP_NAME = re.compile(r"^alert_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.jpg$")


@app.get("/snapshots/{name}")
def snapshot(name: str, request: Request, ctx: AuthCtx = Depends(require("supervisor"))):
    """İhlal görüntüsü: supervisor+ ve kameranın kapsamda olması. Adı bilmek erişim vermez; DB'de referansı olmayan
    veya kapsam dışı dosya 404 (var/yok bilgisi sızmaz)."""
    if not SNAP_NAME.match(name):
        raise HTTPException(status_code=404, detail="bulunamadı")
    cam = _read(history.snapshot_camera, f"snapshots/{name}")
    path = SNAP_DIR / name
    if cam is None or not ctx.camera_ok(cam) or path.is_symlink() or not path.is_file():
        raise HTTPException(status_code=404, detail="bulunamadı")
    security.audit("snapshot_access", "success", conn=request, user_id=ctx.user_id,
                   detail={"snapshot": name, "camera_id": cam}, dedupe=f"snap:{ctx.token_hash}:{name}", every=3600)
    # Snapshot'lar küçüktür (MAX_SNAPSHOT_BYTES); FileResponse'un Range ayrıştırması kullanılmaz.
    return Response(path.read_bytes(), media_type="image/jpeg", headers={"Cache-Control": "private, no-store"})


async def _ws_auth(ws: WebSocket) -> Optional[AuthCtx]:
    try:
        return await run_in_threadpool(security.authenticate, ws)
    except HTTPException:                      # DB erişilemiyor
        return None


@app.websocket("/ws")
async def ws_feed(ws: WebSocket):
    """Mesaj: 1 bayt kamera indeksi + JPEG (biçim değişmedi). FAZ 6: aynı köken (Origin) + geçerli oturum +
    supervisor; yalnız kapsamdaki kameraların kareleri gider; oturum her ~15 sn'de yeniden doğrulanır."""
    reason, ctx = None, None
    if not security.origin_ok(ws):
        reason = "bad_origin"
    else:
        ctx = await _ws_auth(ws)
        if ctx is None or not ctx.at_least("supervisor"):
            reason = "auth_required" if ctx is None else "insufficient_role"
    if reason:
        ops.REG.ws_event("rejected")
        await run_in_threadpool(security.audit, "ws_rejected", "denied", conn=ws,
                                user_id=ctx.user_id if ctx else None, detail={"reason": reason},
                                dedupe=f"ws:{reason}:{security.client_ip(ws)}")
        await ws.close(code=1008)
        return
    await ws.accept()
    ops.REG.ws_event("accepted")
    sent = {}
    recheck = time.monotonic() + WS_RECHECK_SEC
    # FAZ 9: istemcinin kapandığı ancak receive() ile öğrenilir; kare gelmeyen bir kamerada kopmuş bağlantının
    # döngüsü (ve 15 sn'lik oturum sorgusu) aksi halde süresiz sürerdi. İstemciden gelen veri yok sayılır.
    recv = asyncio.ensure_future(ws.receive())
    try:
        while True:
            if recv.done():
                if recv.cancelled() or recv.exception() is not None                         or recv.result().get("type") == "websocket.disconnect":
                    return
                recv = asyncio.ensure_future(ws.receive())
            if time.monotonic() >= recheck:
                recheck = time.monotonic() + WS_RECHECK_SEC
                if await _ws_auth(ws) is None:      # çıkış, iptal veya süre dolumu
                    await ws.close(code=1008)
                    return
            with LOCK:
                cams = [c for c in CAMS.values() if ctx.camera_ok(c.id)]
            any_sent = False
            for c in cams:
                jpg, no = c.jpeg, c.frame_no
                if jpg is not None and sent.get(c.id) != no:
                    sent[c.id] = no
                    await ws.send_bytes(bytes([c.idx]) + jpg)
                    ops.REG.ws_event("frames_sent")
                    any_sent = True
            if not any_sent:
                await asyncio.sleep(0.03)
    except Exception:            # istemci ayrıldı
        pass
    finally:
        recv.cancel()
        ops.REG.ws_event("closed")


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
def video_feed(cid: str, ctx: AuthCtx = Depends(require("supervisor"))):
    with LOCK:
        cam = CAMS.get(cid)
    if cam is None or not ctx.camera_ok(cid):
        raise HTTPException(status_code=404, detail="no such camera")
    return _mjpeg(cam)


def _meta(c: Cam):
    m = dict(c.meta)
    if m["status"] == "running" and time.time() - c.last_ingest > STALE_AFTER:
        m["status"] = "waiting_for_detector"
    return m


@app.get("/api/state")
def state(after: int = 0, ctx: AuthCtx = Depends(require("supervisor"))):
    with LOCK:
        cams = sorted((c for c in CAMS.values() if ctx.camera_ok(c.id)), key=lambda c: c.idx)
        new_alerts = [a for a in ALERTS if a["id"] > after and ctx.camera_ok(a.get("cam", ""))]
        seq = ALERT_SEQ
    cameras = [{"id": c.id, "idx": c.idx, "name": c.name, "meta": _meta(c),
                "summary": c.summary, "people": c.people} for c in cams]
    totals = {k: sum(c.summary.get(k, 0) for c in cams) for k in ("people", "ok", "viol", "analyzing")}
    return JSONResponse({"cameras": cameras, "totals": totals,
                         "alerts": new_alerts, "last_alert_id": seq})


@app.get("/api/alerts")
def alerts(ctx: AuthCtx = Depends(require("supervisor"))):
    with LOCK:
        return [a for a in reversed(ALERTS) if ctx.camera_ok(a.get("cam", ""))]


@app.get("/api/report")
def report(ctx: AuthCtx = Depends(require("supervisor"))):
    with LOCK:
        cams = sorted((c for c in CAMS.values() if ctx.camera_ok(c.id)), key=lambda c: c.idx)
    return [{**r, "cam": c.id, "cam_name": c.name} for c in cams for r in c.report]


_DB_STATUS = ops.Cached(lambda: ops.db_status(check_ready, get_engine), ttl=5.0)
_STORAGE = ops.Cached(lambda: ops.storage_status(SNAP_DIR), ttl=30.0)


@app.get("/api/ops/metrics")
def ops_metrics(ctx: AuthCtx = Depends(require("admin"))):
    """FAZ 9 işletim metrikleri (yalnız admin; JSON). Kamera başına canlı durum + dedektör sağlık/kapsam özeti +
    outbox, HTTP/ingest/DB/WebSocket sayaçları, PostgreSQL ve snapshot deposu. Gizli bilgi/görüntü içermez."""
    if not ops.ENABLED:
        raise HTTPException(status_code=404, detail="metrikler kapalı (OPS_METRICS=0)")
    now = time.time()
    with LOCK:
        cams = sorted(CAMS.values(), key=lambda c: c.idx)
    cameras = []
    for c in cams:
        m = _meta(c)
        cameras.append({"id": c.id, "status": m.get("status"),
                        "last_frame_age_sec": round(now - c.last_ingest, 1) if c.last_ingest else None,
                        "frames_received": c.frame_no, "proc_fps": m.get("proc_fps"), "source_fps": m.get("fps"),
                        "width": m.get("width"), "height": m.get("height"),
                        "people": c.summary.get("people"), "analyzing": c.summary.get("analyzing"),
                        "health": m.get("health"), "outbox": m.get("outbox")})
    return {"generated_at": _iso(datetime.now(timezone.utc)), **ops.REG.snapshot(), "cameras": cameras,
            "postgres": _DB_STATUS.get(), "snapshot_storage": _STORAGE.get(),
            "notes": ["sayaçlar dashboard süreci yeniden başlayınca sıfırlanır",
                      "track sayısı benzersiz kişi sayısı değildir; ANALIZ uygun sayılmaz"]}


@app.get("/healthz")
def health():
    """Canlılık (anonim): yalnız durum. Kamera sayısı vb. işletim bilgisi oturumlu API'lerdedir."""
    return {"status": "ok"}


@app.get("/health/ready")
def health_ready(request: Request):
    """DB erişimi + beklenen Alembic revision. Anonim çağrı yalnız durum alır; ayrıntı (revision) yalnız admin."""
    ok, detail = check_ready()
    if not os.access(SNAP_DIR, os.W_OK):       # ayrıcalıksız süreç snapshot yazamıyorsa hazır DEĞİL (sessiz kayıp yok)
        ok, detail = False, {**detail, "status": "not_ready", "storage": "snapshots_not_writable"}
    try:
        ctx = security.authenticate(request)
    except HTTPException:
        ctx = None
    body = detail if ctx is not None and ctx.at_least("admin") else {"status": detail["status"]}
    return JSONResponse(body, status_code=200 if ok else 503)


# ---------------- geçmiş (PostgreSQL, salt okuma) ----------------
# Durum/sayım tanımları: db/history.py. Bu uçlar canlı RAM durumunu (CAMS/ALERTS) kullanmaz ve değiştirmez.
ACTIVE_WINDOW_SEC = float(os.getenv("ACTIVE_WINDOW_SEC", max(3 * HEARTBEAT_SEC, 60)))
RUN_STATUSES = ("running", "finished", "error", "interrupted", "abandoned", "disconnected")
_SNAP_URL = re.compile(r"^snapshots/alert_[0-9a-f-]{36}\.jpg$")
CameraQ = Query(None, pattern=schemas.CAMERA_ID, max_length=40)
TzQ = Query("UTC", max_length=64, pattern=r"^[A-Za-z0-9_+\-/]+$")


def _bad(loc, msg, typ="value_error"):
    _http_error(422, [{"loc": ["query", loc], "msg": msg, "type": typ}])


def _utc_param(value: Optional[str], loc: str) -> Optional[datetime]:
    """ISO 8601, saat dilimi ZORUNLU (Z veya +03:00); UTC'ye çevrilir. Saat dilimsiz değer belirsizdir -> 422."""
    if value is None or value == "":
        return None
    try:
        dt = datetime.fromisoformat(value) if len(value) <= 40 else None
    except ValueError:
        dt = None
    if dt is None:
        _bad(loc, "ISO 8601 tarih/saat olmalı (ör. 2026-10-28T00:00:00Z)", "datetime_parsing")
    if dt.utcoffset() is None:
        _bad(loc, "saat dilimi belirtilmeli (ör. Z veya +03:00)", "timezone_aware")
    return dt.astimezone(timezone.utc)


def _range_params(date_from, date_to):
    f, t = _utc_param(date_from, "from"), _utc_param(date_to, "to")
    if f is not None and t is not None and f >= t:
        _bad("to", "to, from'dan büyük olmalı")
    return f, t


def _enum_list(values, allowed, loc) -> tuple:
    for v in values:
        if v not in allowed:
            _bad(loc, f"geçersiz değer; izin verilenler: {', '.join(allowed)}", "enum")
    return tuple(dict.fromkeys(values))


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return None if dt is None else dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _num(v):
    return None if v is None else float(v)


def _missing_text(helmet: bool, vest: bool) -> str:
    return " + ".join(x for x, f in ((schemas.HELMET_TEXT, helmet), (schemas.VEST_TEXT, vest)) if f)


def _snapshot(path: Optional[str], images: bool = True) -> dict:
    """Yalnızca DB'deki doğrulanmış biçimdeki ve diskte gerçekten bulunan dosya için URL verir. images=False
    (viewer rolü): görüntü bağlantısı verilmez, yalnız var olduğu belirtilir (kişisel veri en aza indirme)."""
    if not path:
        return {"snapshot": None, "snapshot_missing": False, "snapshot_restricted": False}
    if not images:
        return {"snapshot": None, "snapshot_missing": False, "snapshot_restricted": True}
    if not _SNAP_URL.match(path) or not (SNAP_DIR / path.split("/", 1)[1]).is_file():
        return {"snapshot": None, "snapshot_missing": True, "snapshot_restricted": False}
    return {"snapshot": "/" + path, "snapshot_missing": False, "snapshot_restricted": False}


def _report_summary(md: Optional[dict]) -> Optional[dict]:
    if not isinstance(md, dict):
        return None
    unclosed = md.get("unclosed_incidents") if isinstance(md.get("unclosed_incidents"), list) else []
    by_reason: dict = {}
    for u in unclosed:
        r = u.get("reason") if isinstance(u, dict) else None
        by_reason[str(r)] = by_reason.get(str(r), 0) + 1
    err = md.get("error")
    return {"unclosed_by_reason": by_reason, "unclosed_overflow": md.get("unclosed_overflow", 0),
            "orphan_events": md.get("orphan_events", 0), "video_time_sec": md.get("video_time_sec"),
            "error": redact_text(err)[:300] if err else None,
            "end_reason": md.get("end_reason") if md.get("end_reason") in schemas.END_REASONS else None}


def _source_out(r: dict) -> dict:
    """camera_name: RUN'ın kendi (donmuş) kaynak etiketi; eski kayıtlarda None ve source_verified=False.
    camera_current_name: kameranın bugünkü adı (geçmiş kayda ait olduğu iddia edilmez)."""
    return {"camera_name": r["source_label"], "camera_current_name": r["camera_current_name"],
            "source_verified": r["source_label"] is not None, "source_kind": r["source_kind"],
            "source_ref": r["source_ref"]}


def _incident_out(r: dict, images: bool = True) -> dict:
    return {"incident_id": str(r["incident_id"]), "camera_id": r["camera_id"], **_source_out(r),
            "run_id": str(r["run_id"]), "track_id": r["pid"],
            "status": r["status"], "status_reason": r["status_reason"], "verified": r["verified"],
            "started_at": _iso(r["started_at"]), "ended_at": _iso(r["ended_at"]),
            "last_event_at": _iso(r["last_event_at"]),
            "duration_sec": (round((r["ended_at"] - r["started_at"]).total_seconds(), 1)
                             if r["ended_at"] is not None else None),
            "video_first_sec": _num(r["video_first_sec"]), "video_last_sec": _num(r["video_last_sec"]),
            "missing_helmet": r["missing_helmet"], "missing_vest": r["missing_vest"],
            "missing": _missing_text(r["missing_helmet"], r["missing_vest"]),
            "missing_at_start": _missing_text(r["start_missing_helmet"], r["start_missing_vest"]),
            "event_count": r["event_count"], "ongoing_count": r["ongoing_count"],
            **_snapshot(r["snapshot_path"], images)}


def _run_out(r: dict) -> dict:
    return {"run_id": str(r["run_id"]), "camera_id": r["camera_id"], **_source_out(r),
            "loop_no": r["loop_no"], "status": r["status"], "liveness": r["liveness"],
            "started_at": _iso(r["started_at"]), "finished_at": _iso(r["finished_at"]),
            "last_seen_at": _iso(r["last_seen_at"]), "fps": _num(r["fps"]), "width": r["width"],
            "height": r["height"], "person_count": r["person_count"],
            "persons_with_violation": r["persons_with_violation"], "incident_count": r["incident_count"],
            "verified_incidents": r["verified_incidents"], "event_count": r["event_count"],
            "report": _report_summary(r["report_metadata"])}


def _read(fn, *args, **kwargs):
    """Salt-okunur DB çağrısı; DB hatası -> 503 (adres/parola mesaja girmez)."""
    try:
        with get_session() as s:
            return fn(s, *args, **kwargs)
    except history.BadCursor:
        _bad("cursor", "geçersiz cursor")
    except (SQLAlchemyError, RuntimeError) as exc:
        _rlog("db_read", f"UYARI: geçmiş sorgusu başarısız ({type(exc).__name__}); 503 dönülüyor.")
        _http_error(503, "veritabanı şu an kullanılamıyor; tekrar deneyin")


def _check_tz(tz: str):
    if tz != "UTC" and not _read(history.valid_timezone, tz):
        _bad("tz", "bilinmeyen saat dilimi (ör. UTC, Europe/Istanbul)")


@app.get("/api/history/incidents")
def history_incidents(camera_id: Optional[str] = CameraQ, run_id: Optional[uuid.UUID] = None,
                      date_from: Optional[str] = Query(None, alias="from"),
                      date_to: Optional[str] = Query(None, alias="to"),
                      status: list[str] = Query([]), equipment: Optional[str] = None,
                      limit: int = Query(50, ge=1, le=200), cursor: Optional[str] = Query(None, max_length=200),
                      ctx: AuthCtx = Depends(require("viewer"))):
    f, t = _range_params(date_from, date_to)
    statuses = _enum_list(status, history.STATUSES, "status")
    if equipment is not None:
        _enum_list([equipment], history.EQUIPMENT, "equipment")
    res = _read(history.list_incidents, camera_id=camera_id, run_id=run_id, date_from=f, date_to=t,
                statuses=statuses, equipment=equipment, limit=limit, cursor=cursor,
                active_window=ACTIVE_WINDOW_SEC, camera_scope=ctx.scope)
    images = ctx.at_least("supervisor")
    return {"items": [_incident_out(r, images) for r in res["items"]], "total": res["total"],
            "next_cursor": res["next_cursor"], "limit": limit}


@app.get("/api/history/incidents/{incident_id}")
def history_incident(incident_id: uuid.UUID, ctx: AuthCtx = Depends(require("viewer"))):
    r = _read(history.get_incident, incident_id, active_window=ACTIVE_WINDOW_SEC, camera_scope=ctx.scope)
    images = ctx.at_least("supervisor")
    if r is None:                               # yok VEYA kapsam dışı: aynı yanıt (varlık bilgisi sızmaz)
        raise HTTPException(status_code=404, detail="incident bulunamadı")
    md = r["report_metadata"] if isinstance(r["report_metadata"], dict) else {}
    unclosed = next((u for u in md.get("unclosed_incidents") or []
                     if isinstance(u, dict) and u.get("incident_id") == str(r["incident_id"])), None)
    return {**_incident_out(r, images),
            "camera": {"id": r["camera_id"], "name": r["source_label"], "current_name": r["camera_current_name"]},
            "run": {"id": str(r["run_id"]), "status": r["run_status"], "loop_no": r["loop_no"],
                    "started_at": _iso(r["run_started_at"]), "finished_at": _iso(r["run_finished_at"]),
                    "last_seen_at": _iso(r["run_last_seen_at"])},
            "unclosed": ({"reason": unclosed.get("reason"), "video_time_sec": unclosed.get("t")}
                         if unclosed else None),
            "timeline": [{"event_id": str(e["event_id"]), "type": e["event_type"],
                          "occurred_at": _iso(e["occurred_at"]), "ingested_at": _iso(e["ingested_at"]),
                          "video_time_sec": _num(e["video_time_sec"]),
                          "missing": _missing_text(e["missing_helmet"], e["missing_vest"]),
                          "helmet_rate": e["helmet_rate"], "vest_rate": e["vest_rate"], "box": e["box"],
                          **_snapshot(e["snapshot_path"], images)} for e in r["timeline"]],
            "timeline_truncated": r["timeline_truncated"]}


@app.get("/api/history/summary")
def history_summary(camera_id: Optional[str] = CameraQ, run_id: Optional[uuid.UUID] = None,
                    date_from: Optional[str] = Query(None, alias="from"),
                    date_to: Optional[str] = Query(None, alias="to"), tz: str = TzQ,
                    ctx: AuthCtx = Depends(require("viewer"))):
    f, t = _range_params(date_from, date_to)
    _check_tz(tz)
    res = _read(history.summary, camera_id=camera_id, run_id=run_id, date_from=f, date_to=t, tz=tz,
                active_window=ACTIVE_WINDOW_SEC, camera_scope=ctx.scope)
    return {**res, "filters": {"camera_id": camera_id, "run_id": str(run_id) if run_id else None,
                               "from": _iso(f), "to": _iso(t), "tz": tz}}


@app.get("/api/history/cameras")
def history_cameras(ctx: AuthCtx = Depends(require("viewer"))):
    return [{"id": c["id"], "name": c["name"], "source_kind": c["source_kind"], "created_at": _iso(c["created_at"]),
             "last_seen_at": _iso(c["last_seen_at"]), "run_count": c["run_count"],
             "last_run_started_at": _iso(c["last_run_started_at"]), "live_runs": c["live_runs"],
             "incident_count": c["incident_count"], "event_count": c["event_count"]}
            for c in _read(history.list_cameras, active_window=ACTIVE_WINDOW_SEC, camera_scope=ctx.scope)]


@app.get("/api/history/runs")
def history_runs(camera_id: Optional[str] = CameraQ, date_from: Optional[str] = Query(None, alias="from"),
                 date_to: Optional[str] = Query(None, alias="to"), status: list[str] = Query([]),
                 limit: int = Query(50, ge=1, le=200), cursor: Optional[str] = Query(None, max_length=200),
                 ctx: AuthCtx = Depends(require("viewer"))):
    f, t = _range_params(date_from, date_to)
    statuses = _enum_list(status, RUN_STATUSES, "status")
    res = _read(history.list_runs, camera_id=camera_id, date_from=f, date_to=t, statuses=statuses, limit=limit,
                cursor=cursor, active_window=ACTIVE_WINDOW_SEC, camera_scope=ctx.scope)
    return {"items": [_run_out(r) for r in res["items"]], "total": res["total"],
            "next_cursor": res["next_cursor"], "limit": limit}


@app.get("/api/history/runs/{run_id}")
def history_run(run_id: uuid.UUID, ctx: AuthCtx = Depends(require("viewer"))):
    r = _read(history.get_run, run_id, active_window=ACTIVE_WINDOW_SEC, camera_scope=ctx.scope)
    if r is None:
        raise HTTPException(status_code=404, detail="run bulunamadı")
    return {**_run_out(r), "incidents_by_status": r["incidents_by_status"],
            "persons": [{"track_id": p["pid"], "first_sec": _num(p["first_sec"]), "last_sec": _num(p["last_sec"]),
                         "observations": p["observations"], "helmet_rate": p["helmet_rate"],
                         "vest_rate": p["vest_rate"], "ever_violation": p["ever_violation"],
                         "updated_at": _iso(p["updated_at"])} for p in r["persons"]]}