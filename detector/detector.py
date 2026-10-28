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

import base64
import json
import logging
import multiprocessing as mp
import re
import sys
import threading
import time
from collections import Counter

import cv2
import requests
import torch
from ultralytics import YOLO

import ppe_core as core

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

GREEN, RED, GRAY = (0, 200, 0), (0, 0, 255), (160, 160, 160)


# ---------------------------------------------------------------- kaynak listesi
def _split_name(item, n):
    """'Kapı=rtsp://...' -> ('Kapı', 'rtsp://...'). İsim yoksa otomatik isim verilir."""
    if "=" in item:
        left, right = item.split("=", 1)
        if "://" not in left and "/" not in left and "\\" not in left and left.strip():
            return left.strip(), right.strip()
    if _is_youtube(item):
        return f"YouTube {n}", item
    if item.isdigit() or "://" in item:
        return f"Kamera {n}", item
    return Path(item).stem or f"Kamera {n}", item


def parse_sources():
    """Öncelik: komut satırı > .env (VIDEO_SOURCES + YOUTUBE_n/SOURCE_n) > sources.txt > videos/ klasörü > VIDEO_SOURCE."""
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
    return [(f"cam{i}",) + _split_name(item, i) for i, item in enumerate(raw, start=1)]


# ---------------------------------------------------------------- gönderici (kamera başına)
class Sender(threading.Thread):
    """Ağ gecikmesi inference'ı yavaşlatmasın diye ayrı thread. Kareler: en yenisi kazanır.
    Olaylar (alert) asla düşmez; gönderilemezse bir sonraki denemeye kalır."""
    def __init__(self, cid, name):
        super().__init__(daemon=True)
        self.cid, self.name = cid, name
        self.cv = threading.Condition()
        self.latest, self.events = None, []
        self.sess = requests.Session()
        if INGEST_TOKEN:
            self.sess.headers["X-Token"] = INGEST_TOKEN
        self._last_err = 0.0

    def submit(self, jpg, payload, events):
        with self.cv:
            self.latest = (jpg, payload)
            self.events.extend(events)
            self.cv.notify()

    def post_report(self, report, status):
        try:
            self.sess.post(f"{DASHBOARD_URL}/ingest/report",
                           json={"cam": self.cid, "name": self.name, "report": report, "status": status},
                           timeout=5)
        except requests.RequestException as e:
            print(f"[{self.cid}] rapor gönderilemedi: {e}", flush=True)

    def run(self):
        while True:
            with self.cv:
                self.cv.wait_for(lambda: self.latest is not None)
                (jpg, payload), self.latest = self.latest, None
                events, self.events = self.events, []
            payload.update(cam=self.cid, name=self.name, events=events)
            try:
                r = self.sess.post(f"{DASHBOARD_URL}/ingest/frame",
                                   files={"frame": ("f.jpg", jpg, "image/jpeg")},
                                   data={"payload": json.dumps(payload)}, timeout=5)
                r.raise_for_status()
            except requests.RequestException as e:
                with self.cv:
                    self.events[:0] = events            # olayları geri koy
                if time.time() - self._last_err > 5:
                    print(f"[{self.cid}] Dashboard'a ulaşılamıyor ({DASHBOARD_URL}): {e}", flush=True)
                    self._last_err = time.time()
                time.sleep(1)


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
def run_once(model, cid, name, source, sender, loop_no):
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
    if W == 0:
        raise RuntimeError(f"Kaynak açılamadı (OpenCV akışı açamadı): {source}")
    print(f"[{cid}] Açıldı: {W}x{H} @ {fps:.0f} fps, model çalışıyor…", flush=True)

    is_file = isinstance(src, str) and os.path.isfile(src)
    meta = {"source": str(source), "fps": round(fps, 1), "width": W, "height": H,
            "status": "running", "error": None, "loop": loop_no, "proc_fps": 0.0}

    model.predictor = None                       # tracker durumunu sıfırla
    monitor = core.PPEMonitor()
    extra = {"stream_buffer": True} if (BUFFER and not is_file) else {}   # canlı akış/kamera: kare kaybını önle
    results = model.track(source=src, stream=True, persist=True, tracker=TRACKER,
                          conf=CONF, imgsz=IMGSZ, device=DEVICE, vid_stride=VID_STRIDE,
                          verbose=False, **extra)

    wall0, tick, n_tick = time.time(), time.time(), 0
    for idx, r in enumerate(results, start=1):
        t = idx * VID_STRIDE / fps
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

        helmet_map = core.match_items(persons, helmets, core.head_region)
        vest_map = core.match_items(persons, vests, core.torso_region)
        statuses, events = monitor.update(t, (W, H), persons, helmet_map, vest_map)

        for e in events:
            if e["type"] == "start":
                attach_snapshot(e, frame)
            print(f"[{cid}] [{e['t']:6.1f}s] {e['type'].upper():7} ID {e['pid']} {e['missing']}", flush=True)

        vals = list(statuses.values())
        people = [{"pid": int(pid), "status": st, "helmet": round(h * 100), "vest": round(v * 100)}
                  for pid, (st, h, v) in sorted(statuses.items())]
        summary = {"t": round(t, 1), "people": len(persons),
                   "ok": sum(s[0] == "UYGUN" for s in vals),
                   "viol": sum(s[0] == "IHLAL" for s in vals),
                   "analyzing": sum(s[0] == "ANALIZ" for s in vals),
                   "raw_helmets": len(helmets), "raw_vests": len(vests)}

        jpg = encode(draw(frame, persons, statuses, helmet_map, vest_map))

        n_tick += 1
        now = time.time()
        if now - tick >= 1.0:
            meta["proc_fps"] = round(n_tick / (now - tick), 1)
            tick, n_tick = now, 0

        if jpg:
            sender.submit(jpg, {"meta": dict(meta), "summary": summary, "people": people}, events)

        if is_file:                              # dosyayı gerçek zamanlı hızda oynat
            ahead = wall0 + t - time.time()
            if ahead > 0:
                time.sleep(ahead)

    report = [{"pid": int(pid), "first": round(s["first"], 1), "last": round(s["last"], 1),
               "n": s["n"], "helmet": round(100 * s["h"] / s["n"]),
               "vest": round(100 * s["v"] / s["n"]), "violation": s["ever_violation"]}
              for pid, s in sorted(monitor.stats.items()) if s["n"] > 0]
    sender.post_report(report, "running" if LOOP else "finished")


def stream_worker(cid, name, source, delay):
    time.sleep(delay)                            # modeller aynı anda yüklenmesin
    if os.getenv("VERBOSE", "0") != "1":         # "Waiting for stream" gibi zararsız uyarıları gizle
        logging.getLogger("ultralytics").setLevel(logging.ERROR)
    sender = Sender(cid, name)
    sender.start()
    try:
        model = YOLO(MODEL_PATH)                 # her thread kendi modelini yükler (takip durumu ayrı kalır)
    except Exception as exc:                     # noqa: BLE001
        print(f"[{cid}] Model yüklenemedi: {exc}", flush=True)
        return
    loop_no = 0
    while True:
        loop_no += 1
        try:
            run_once(model, cid, name, source, sender, loop_no)
        except Exception as exc:                 # noqa: BLE001
            print(f"[{cid}] HATA: {exc}", flush=True)
            time.sleep(3)                        # RTSP kopması vb. için yeniden dene
            continue
        if not LOOP:
            break


def main():
    sources = parse_sources()
    print(f"Model: {MODEL_PATH} | Cihaz: {DEVICE} | Dashboard: {DASHBOARD_URL} | Kaynak sayısı: {len(sources)}",
          flush=True)
    for cid, name, src in sources:
        print(f"  {cid}: {name}  <-  {src}", flush=True)

    ctx = mp.get_context("spawn")
    stagger = float(os.getenv("START_DELAY", 2.0))   # GPU/model yükleri aynı anda başlamasın
    procs = {}
    for i, (cid, name, src) in enumerate(sources):
        p = ctx.Process(target=stream_worker, args=(cid, name, src, i * stagger), daemon=True)
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
        print("Durduruluyor…", flush=True)
    finally:
        for p in procs.values():
            if p.is_alive():
                p.terminate()


if __name__ == "__main__":
    main()