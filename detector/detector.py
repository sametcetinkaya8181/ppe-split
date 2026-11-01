"""PPE dedektörü: bilgisayarda doğrudan çalışır, BİRDEN FAZLA kaynağı eş zamanlı işler.
Her kaynak için ayrı SÜREÇ (process) + ayrı YOLO/ByteTrack örneği. Süreç kullanılır çünkü Apple GPU (mps)
ve bazı GPU sürücüleri aynı süreçte çok thread ile çökebilir (segmentation fault).
Etiketli kare ve olaylar Docker'daki dashboard'a HTTP ile gönderilir."""
import os
from pathlib import Path


def _load_env():
    """detector/.env varsa oku (terminalde verilen değişkenler önceliklidir)."""
    p = Path(__file__).with_name(".env")
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                v = v.strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                    v = v[1:-1]                      # "değer" veya 'değer' -> değer
                os.environ.setdefault(k.strip(), v)


_load_env()
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")   # desteklenmeyen işlemler CPU'ya düşsün
os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")           # OpenCV/FFmpeg gürültüsünü kıs
os.environ.setdefault("OPENCV_FFMPEG_LOGLEVEL", "-8")
# FAZ 9 gizlilik: Ultralytics anonim kullanım analitiği (Google Analytics) ve çevrimiçi otomatik paket kurulumu
# kapalı. Ultralytics bu değişkeni içe aktarılırken okur; açmak için detector/.env içine YOLO_OFFLINE=0 yazın.
os.environ.setdefault("YOLO_OFFLINE", "1")

import base64
import logging
import multiprocessing as mp
import re
import sys
import time
from collections import Counter

import cv2
import torch
from ultralytics import YOLO

import detector_env
import outbox as outbox_mod
import ppe_core as core
import protocol
import telemetry

MODEL_PATH = os.getenv("MODEL_PATH", "models/best.pt")
DASHBOARD_URL = os.getenv("DASHBOARD_URL", "http://localhost:8000").rstrip("/")
INGEST_TOKEN = os.getenv("INGEST_TOKEN", "")


def _auto_device():
    if torch.cuda.is_available():
        return "0"                                   # NVIDIA
    if torch.backends.mps.is_available():
        return "mps"                                 # Apple Silicon GPU
    return "cpu"


DEVICE = os.getenv("DEVICE") or _auto_device()
IMGSZ = int(os.getenv("IMGSZ", 960))
CONF = float(os.getenv("CONF", 0.25))
TRACKER = os.getenv("TRACKER", "bytetrack.yaml")
LOOP = os.getenv("LOOP", "1") == "1"
VID_STRIDE = int(os.getenv("VID_STRIDE", 1))       # her N. kareyi işle
JPEG_QUALITY = int(os.getenv("JPEG_QUALITY", 80))
STREAM_WIDTH = int(os.getenv("STREAM_WIDTH", 960))
BUFFER = os.getenv("BUFFER", "1") == "1"            # canlı akışta kareleri sırayla kuyruğa al (donmayı önler)
SEND_TIMEOUT = float(os.getenv("SEND_TIMEOUT", 5))      # dashboard HTTP isteği zaman aşımı (sn)
EVENT_QUEUE_MAX = int(os.getenv("EVENT_QUEUE_MAX", 2000))  # kamera başına bellekteki en fazla bekleyen olay
DRAIN_TIMEOUT = float(os.getenv("DRAIN_TIMEOUT", 10))   # kapanışta kuyruğu boşaltmak için en fazla bekleme (sn)
# FAZ 7 kalıcı outbox (detector/outbox.py). Kapasite dolunca yalnız 'ongoing' seyreltilir; start/end/rapor için
# OUTBOX_MAX_* değerlerinin %25 üzerine kadar yedek alan vardır, o da dolarsa görünür hata (bellek yedeği).
OUTBOX_ENABLED = os.getenv("OUTBOX_ENABLED", "1") == "1"
OUTBOX_MAX_BYTES = int(os.getenv("OUTBOX_MAX_BYTES", 512 * 1024 * 1024))
OUTBOX_MAX_ITEMS = int(os.getenv("OUTBOX_MAX_ITEMS", 100_000))
OUTBOX_HIGH_WATERMARK = float(os.getenv("OUTBOX_HIGH_WATERMARK", 0.8))
OUTBOX_RETRY_BACKOFF_MAX = float(os.getenv("OUTBOX_RETRY_BACKOFF_MAX", 30))

GREEN, RED, GRAY = (0, 200, 0), (0, 0, 255), (160, 160, 160)


# ---------------------------------------------------------------- kaynak listesi
def parse_sources():
    """Öncelik: komut satırı > .env (VIDEO_SOURCES + YOUTUBE_n/SOURCE_n) > sources.txt > videos/ klasörü > VIDEO_SOURCE.
    Kamera kimliği: '[kimlik]Etiket=kaynak' öneki (sabit) > CAMERA_ID_MODE=source (kaynaktan türetilir) >
    'cam<sıra>' (eski davranış; sıra değişirse kimlik kayar, açılışta uyarılır). Bkz. protocol.camera_entries."""
    raw = sys.argv[1:]
    if not raw:
        raw = [x.strip() for x in os.getenv("VIDEO_SOURCES", "").split(",") if x.strip()]
        numbered = []
        for k, v in os.environ.items():              # YOUTUBE_1, YOUTUBE_2, ... / SOURCE_1, ...
            m = re.match(r"^(?:YOUTUBE|SOURCE)_(\d+)$", k)
            if m and v.strip():
                numbered.append((int(m.group(1)), v.strip()))
        raw += [v for _, v in sorted(numbered)]
    if not raw:
        f = Path(__file__).with_name("sources.txt")
        if f.exists():
            raw = [l.strip() for l in f.read_text(encoding="utf-8").splitlines()
                   if l.strip() and not l.strip().startswith("#")]
    if not raw:                                   # videos/ klasöründeki tüm videolar
        vd = Path(__file__).with_name("videos")
        exts = {".mp4", ".avi", ".mov", ".mkv", ".m4v", ".webm"}
        if vd.is_dir():
            raw = [str(f) for f in sorted(vd.iterdir())
                   if f.suffix.lower() in exts and not f.name.startswith(".")]
    if not raw:
        raw = [os.getenv("VIDEO_SOURCE", "videos/video.mp4")]
    entries, warnings = protocol.camera_entries(raw, os.getenv("CAMERA_ID_MODE", "index").strip().lower())
    for w in warnings:
        print(f"UYARI: {w}", flush=True)
    return entries


# Gönderici (kamera başına, kimlikli olaylar, FIFO + retry): protocol.Sender


# ---------------------------------------------------------------- çizim / yardımcılar
def draw(frame, persons, statuses, helmet_map, vest_map):
    for pid, box, _ in persons:
        status, hr, vr = statuses.get(int(pid), ("ANALIZ", 0, 0))
        color = {"UYGUN": GREEN, "IHLAL": RED}.get(status, GRAY)
        x1, y1, x2, y2 = map(int, box)
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        cv2.putText(frame, f"ID{int(pid)} {status}", (x1, max(14, y1 - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
        cv2.putText(frame, f"B:{hr*100:.0f}% Y:{vr*100:.0f}%", (x1, y2 + 14),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1)
        for m in (helmet_map, vest_map):
            if pid in m:
                bx = list(map(int, m[pid]))
                cv2.rectangle(frame, (bx[0], bx[1]), (bx[2], bx[3]), color, 1)
    return frame


def encode(frame):
    h, w = frame.shape[:2]
    if w > STREAM_WIDTH:
        frame = cv2.resize(frame, (STREAM_WIDTH, int(h * STREAM_WIDTH / w)))
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    return buf.tobytes() if ok else None


def attach_snapshot(ev, frame):
    """'start' olaylarına kişi kırpıntısını base64 olarak ekle; dashboard kaydeder."""
    x1, y1, x2, y2 = ev["box"]
    pad = 20
    crop = frame[max(0, y1 - pad):y2 + pad, max(0, x1 - pad):x2 + pad]
    if crop.size:
        ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if ok:
            ev["snapshot_b64"] = base64.b64encode(buf.tobytes()).decode()


# ---------------------------------------------------------------- YouTube desteği
def _is_youtube(u):
    return "youtube.com/" in u or "youtu.be/" in u


def resolve_source(source):
    """YouTube sayfa adresini yt-dlp ile gerçek akış adresine (m3u8/mp4) çevirir. Diğerlerini olduğu gibi döner.
    Akış adresleri birkaç saat sonra geçersizleşir; her yeniden bağlanmada yeniden çözülür."""
    if not _is_youtube(source):
        return source
    try:
        import yt_dlp
    except ImportError:
        raise RuntimeError("YouTube için önce şunu kur: pip install yt-dlp")
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True, "socket_timeout": 20,
            "format": os.getenv("YT_FORMAT", "bv*[height<=720][vcodec^=avc1]/bv*[height<=720]/b[height<=720]/bv*/b")}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(source, download=False)
    url = info.get("url")
    if not url:                                   # seçilen format birleştirmeliyse görüntü parçasını al
        for f in (info.get("requested_formats") or []) + list(reversed(info.get("formats") or [])):
            if f.get("url") and f.get("vcodec") not in (None, "none"):
                url = f["url"]
                break
    if not url:
        raise RuntimeError("YouTube akış adresi alınamadı (yayın özel/kapalı olabilir)")
    return url


# ---------------------------------------------------------------- tek kaynağın işlenmesi
def run_once(model, cid, name, source, sender, loop_no, stop=None):
    """Tek run: kaynağı açar ve sonuna kadar işler. Her çağrı YENİ bir run_id üretir (tekrar oynatma ve yeniden
    bağlanma = yeni run). Kaynak açıldıktan sonra run nasıl biterse bitsin (finished / error / interrupted) rapor
    finally içinde Sender kuyruğuna girer; kaynak hiç açılamazsa run başlamamış sayılır ve rapor gönderilmez."""
    run_id = protocol.new_id()
    shown, kind = protocol.redact_source(source), protocol.source_kind(source)
    if _is_youtube(source):
        print(f"[{cid}] YouTube adresi çözülüyor (10-20 sn sürebilir)…", flush=True)
    real = resolve_source(source)
    if real != source:
        print(f"[{cid}] Akış adresi alındı, bağlanılıyor…", flush=True)
    src = int(real) if real.isdigit() else real
    cap = cv2.VideoCapture(src)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    if not fps or fps != fps or fps > 120:           # bazı RTSP kaynakları saçma fps döndürür
        fps = 25.0
    W, H = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    tel = telemetry.for_camera(cid, device=DEVICE)
    if W == 0:
        tel.open_failed("source_open_failed")
        raise RuntimeError(f"Kaynak açılamadı (OpenCV akışı açamadı): {shown}")
    print(f"[{cid}] Açıldı: {W}x{H} @ {fps:.0f} fps, model çalışıyor…", flush=True)

    started_at = protocol.utc_now_iso()
    is_file = isinstance(src, str) and os.path.isfile(src)
    meta = {"source": shown, "source_kind": kind, "run_id": run_id, "fps": round(fps, 1), "width": W, "height": H,
            "status": "running", "error": None, "loop": loop_no, "proc_fps": 0.0}

    model.predictor = None                       # tracker durumunu sıfırla
    monitor = core.PPEMonitor()
    tracker = protocol.IncidentTracker()         # incident durumu run'a özel
    extra = {"stream_buffer": True} if (BUFFER and not is_file) else {}   # canlı akış/kamera: kare kaybını önle
    run_status, error, progress = "error", None, {"t": 0.0}
    tel.run_started(run_id, loop=loop_no, source_kind=kind, fps=fps, width=W, height=H, vid_stride=VID_STRIDE)
    try:
        results = model.track(source=src, stream=True, persist=True, tracker=TRACKER,
                              conf=CONF, imgsz=IMGSZ, device=DEVICE, vid_stride=VID_STRIDE,
                              verbose=False, **extra)
        _process(model, cid, results, fps, W, H, is_file, meta, monitor, tracker, sender, run_id, stop, progress,
                 tel)
        run_status = "interrupted" if (stop is not None and stop.is_set()) else "finished"
    except KeyboardInterrupt:
        run_status = "interrupted"
        raise
    except Exception as exc:
        error = protocol.redact_text(exc)[:300]
        raise
    finally:
        t = progress["t"]
        tel.run_ended(run_status, error)
        tracker.close_all("run_end", t)          # açık kalan incident'lar: sahte end üretilmez, raporda listelenir
        report = [{"pid": int(pid), "first": round(s["first"], 1), "last": round(s["last"], 1),
                   "n": s["n"], "helmet": round(100 * s["h"] / s["n"]),
                   "vest": round(100 * s["v"] / s["n"]), "violation": s["ever_violation"]}
                  for pid, s in sorted(monitor.stats.items()) if s["n"] > 0]
        legacy = "running" if (LOOP and run_status != "interrupted") else run_status   # dashboard kamera durumu
        sender.submit_report({"cam": cid, "name": name, "report": report, "status": legacy,
                              "run_id": run_id, "camera_id": cid, "run_status": run_status, "loop": loop_no,
                              "source_kind": kind, "started_at": started_at, "finished_at": protocol.utc_now_iso(),
                              "video_time_sec": round(t, 1), "error": error,
                              "unclosed_incidents": tracker.unclosed,
                              "unclosed_overflow": tracker.unclosed_overflow, "orphan_events": tracker.orphans})


def _process(model, cid, results, fps, W, H, is_file, meta, monitor, tracker, sender, run_id, stop, progress,
             tel=None):
    """Kare döngüsü (YOLO + ByteTrack + PPEMonitor). Karar mantığı değişmedi; olaylar burada kimlik kazanır.
    FAZ 9: aşama süreleri ve kapsam telemetry'ye yazılır (yalnız ölçüm; kararları etkilemez)."""
    wall0, tick, n_tick = time.time(), time.time(), 0
    tel = tel or telemetry.for_camera(cid, device=DEVICE)
    stage = {}
    pc = time.perf_counter
    loop_end = pc()
    for idx, r in enumerate(results, start=1):
        got = pc()
        if stop is not None and stop.is_set():
            return
        t = idx * VID_STRIDE / fps
        progress["t"] = t
        frame = r.orig_img.copy()
        persons, helmets, vests = [], [], []
        b = r.boxes
        has = b is not None and len(b)
        if has:
            cls = b.cls.cpu().numpy().astype(int)
            xyxy = b.xyxy.cpu().numpy()
            conf = b.conf.cpu().numpy()
            ids = b.id.cpu().numpy().astype(int) if b.id is not None else None
            helmets = list(xyxy[cls == core.CLASS_HELMET])
            vests = list(xyxy[cls == core.CLASS_VEST])
            if ids is not None:
                pm = cls == core.CLASS_PERSON
                persons = list(zip(ids[pm], xyxy[pm], conf[pm]))

        if idx % 60 == 1:
            seen = Counter(model.names[int(c)] for c in cls) if has else {}
            print(f"[{cid}] [dbg] kare {idx} t={t:.1f}s sınıflar={dict(seen)} "
                  f"takip_id={'var' if (has and b.id is not None) else 'YOK'} "
                  f"kişi={len(persons)}", flush=True)

        telemetry.install_tracker_timer(model, stage)     # ilk kareden sonra: ByteTrack süresi (bir sonraki kare)
        s0 = pc()
        helmet_map = core.match_items(persons, helmets, core.head_region)
        vest_map = core.match_items(persons, vests, core.torso_region)
        s1 = pc()
        statuses, raw_events = monitor.update(t, (W, H), persons, helmet_map, vest_map)
        s2 = pc()

        events = []
        for e in raw_events:                     # kimlikler olay doğduğu anda verilir; retry'da değişmez
            try:
                e = protocol.enrich_event(e, run_id=run_id, camera_id=cid, tracker=tracker)
            except protocol.ProtocolError as exc:
                print(f"[{cid}] HATA: olay sözleşme dışı, gönderilmedi: {exc}", flush=True)
                continue
            if e["type"] == "start":
                attach_snapshot(e, frame)
            print(f"[{cid}] [{e['t']:6.1f}s] {e['type'].upper():7} ID {e['pid']} {e['missing']}", flush=True)
            events.append(e)
        tracker.sweep(monitor.state.keys(), t)   # PPEMonitor'ün unuttuğu (STALE) kişiler: end ÜRETİLMEZ

        vals = list(statuses.values())
        people = [{"pid": int(pid), "status": st, "helmet": round(h * 100), "vest": round(v * 100)}
                  for pid, (st, h, v) in sorted(statuses.items())]
        summary = {"t": round(t, 1), "people": len(persons),
                   "ok": sum(s[0] == "UYGUN" for s in vals),
                   "viol": sum(s[0] == "IHLAL" for s in vals),
                   "analyzing": sum(s[0] == "ANALIZ" for s in vals),
                   "raw_helmets": len(helmets), "raw_vests": len(vests)}

        sp = getattr(r, "speed", None) or {}
        stage_ms = {"wait_ms": (got - loop_end) * 1e3, "preprocess_ms": sp.get("preprocess"),
                    "inference_ms": sp.get("inference"), "postprocess_ms": sp.get("postprocess"),
                    "bytetrack_ms": stage.pop("bytetrack_ms", None),
                    "match_ms": (s1 - s0) * 1e3, "monitor_ms": (s2 - s1) * 1e3}
        tel.frame(t=t, frame=r.orig_img, persons=persons, statuses=statuses, stage_ms={}, events=events)

        s3 = pc()
        jpg = encode(draw(frame, persons, statuses, helmet_map, vest_map))
        stage_ms["render_ms"] = (pc() - s3) * 1e3

        n_tick += 1
        now = time.time()
        if now - tick >= 1.0:
            meta["proc_fps"] = round(n_tick / (now - tick), 1)
            meta["health"] = tel.compact()          # saniyede bir: dashboard kapsam/sağlık göstergesi
            tick, n_tick = now, 0

        # jpg None olsa da (kodlama hatası) olaylar kuyruğa girer
        s4 = pc()
        sender.submit(jpg, {"meta": dict(meta), "summary": summary, "people": people,
                            "run_id": run_id, "camera_id": cid}, events)
        end = pc()
        stage_ms["submit_ms"] = (end - s4) * 1e3
        stage_ms["frame_ms"] = (end - loop_end) * 1e3   # kare döngüsü: çözme+YOLO+takip+karar+çizim+kuyruk
        tel.timings_add(stage_ms)

        if is_file:                              # dosyayı gerçek zamanlı hızda oynat
            ahead = wall0 + t - time.time()
            if ahead > 0:
                time.sleep(ahead)
        loop_end = pc()


def stream_worker(cid, name, source, delay, stop):
    if stop.wait(delay):                         # modeller aynı anda yüklenmesin
        return
    if os.getenv("VERBOSE", "0") != "1":         # "Waiting for stream" gibi zararsız uyarıları gizle
        logging.getLogger("ultralytics").setLevel(logging.ERROR)
    tel = telemetry.for_camera(cid, device=DEVICE)
    tel.set_output(outbox_mod.path_for(cid).with_suffix(".health.json"))   # gizli bilgi/görüntü içermez
    tel.start_flusher()                          # kare gelmese de (donmuş akış) sağlık dosyası güncellenir
    sender = _make_sender(cid, name)
    if sender is None:                           # outbox kilitli/bozuk: bu kamera başlatılmaz (diğerleri sürer)
        tel.open_failed("outbox_unavailable")
        return
    sender.start()
    try:
        try:
            model = YOLO(MODEL_PATH)             # her süreç kendi modelini yükler (takip durumu ayrı kalır)
        except Exception as exc:                 # noqa: BLE001
            print(f"[{cid}] Model yüklenemedi: {exc}", flush=True)
            return
        loop_no = 0
        while not stop.is_set():
            loop_no += 1
            try:
                run_once(model, cid, name, source, sender, loop_no, stop)
            except Exception as exc:             # noqa: BLE001
                print(f"[{cid}] HATA: {protocol.redact_text(exc)}", flush=True)
                tel.reconnecting()
                if stop.wait(3):                 # RTSP kopması vb. için yeniden dene
                    break
                continue
            if not LOOP:
                break
    except KeyboardInterrupt:
        pass
    finally:                                     # bekleyen olay/raporları sınırlı süre boşalt
        sender.close(DRAIN_TIMEOUT)
        st = sender.snapshot_stats()
        if OUTBOX_ENABLED:
            print(f"[{cid}] gönderici kapandı: {st['events_sent']} olay, {st['reports_sent']} rapor iletildi; "
                  f"outbox'ta bekleyen {st['pending_count']}, ölü {st['dead_letter_count']}, karantina "
                  f"{st['quarantine_count']}, atılan ongoing {st['evicted_ongoing'] + st['dropped_ongoing_full']}",
                  flush=True)
        else:
            print(f"[{cid}] gönderici kapandı: {st.get('events_sent', 0)} olay, {st.get('reports_sent', 0)} rapor "
                  f"iletildi; düşürülen {st.get('dropped_permanent', 0) + st.get('dropped_queue_full', 0)}, "
                  f"atılan ongoing {st.get('evicted_ongoing', 0)}", flush=True)


def _make_sender(cid, name):
    """Varsayılan: kalıcı outbox (FAZ 7). OUTBOX_ENABLED=0 -> eski bellek içi Sender (olaylar çökmede kaybolur)."""
    if not OUTBOX_ENABLED:
        return protocol.Sender(cid, name, DASHBOARD_URL, INGEST_TOKEN, timeout=SEND_TIMEOUT, queue_max=EVENT_QUEUE_MAX)
    try:
        ob = outbox_mod.Outbox(outbox_mod.path_for(cid), cid, max_bytes=OUTBOX_MAX_BYTES, max_items=OUTBOX_MAX_ITEMS,
                               high_watermark=OUTBOX_HIGH_WATERMARK)
    except outbox_mod.OutboxError as exc:
        print(f"[{cid}] HATA: outbox açılamadı — bu kamera BAŞLATILMADI. {exc}. Dosyayı silmeyin; "
              f"docs/OUTBOX.md 'Kurtarma' adımlarını izleyin.", flush=True)
        return None
    m = ob.metrics()
    if m["pending_count"] or m["dead_letter_count"] or m["quarantine_count"]:
        print(f"[{cid}] outbox kurtarma: {m['pending_count']} bekleyen mesaj (en eski {m['oldest_pending_age_sec']} sn) "
              f"aynı kimliklerle gönderilecek; ölü {m['dead_letter_count']}, karantina {m['quarantine_count']}.",
              flush=True)
    return outbox_mod.DurableSender(cid, name, DASHBOARD_URL, INGEST_TOKEN, ob, timeout=SEND_TIMEOUT,
                                    backoff_max=OUTBOX_RETRY_BACKOFF_MAX, token_provider=detector_env.env_token,
                                    status_path=outbox_mod.path_for(cid).with_suffix(".status.json"))


def main():
    try:
        sources = parse_sources()
    except protocol.ProtocolError as exc:
        print(f"HATA: kaynak yapılandırması geçersiz: {protocol.redact_text(exc)}", flush=True)
        sys.exit(2)
    print(f"Model: {MODEL_PATH} | Cihaz: {DEVICE} | Dashboard: {protocol.redact_source(DASHBOARD_URL)} | "
          f"Kaynak sayısı: {len(sources)}", flush=True)
    for cid, name, src in sources:
        print(f"  {cid}: {name}  <-  {protocol.redact_source(src)}", flush=True)

    ctx = mp.get_context("spawn")
    stop = ctx.Event()                           # kontrollü kapanış: süreçler run'ı 'interrupted' bitirip boşaltır
    stagger = float(os.getenv("START_DELAY", 2.0))   # GPU/model yükleri aynı anda başlamasın
    procs = {}
    for i, (cid, name, src) in enumerate(sources):
        p = ctx.Process(target=stream_worker, args=(cid, name, src, i * stagger, stop), daemon=True)
        p.start()
        procs[cid] = p

    reported = set()
    try:
        while any(p.is_alive() for p in procs.values()):
            for cid, p in procs.items():
                if not p.is_alive() and cid not in reported:
                    reported.add(cid)
                    print(f"[{cid}] süreç sona erdi (çıkış kodu {p.exitcode})", flush=True)
            time.sleep(1)
    except KeyboardInterrupt:
        print(f"Durduruluyor… (bekleyen olaylar en fazla {DRAIN_TIMEOUT:.0f} sn gönderilmeye çalışılır)", flush=True)
        stop.set()
        deadline = time.time() + DRAIN_TIMEOUT + SEND_TIMEOUT + 5
        for p in procs.values():
            p.join(max(0.0, deadline - time.time()))
    finally:
        for cid, p in procs.items():
            if p.is_alive():
                print(f"[{cid}] süreç zamanında kapanmadı; zorla sonlandırılıyor (bellekteki olaylar kaybolabilir)",
                      flush=True)
                p.terminate()


if __name__ == "__main__":
    main()