"""PPE dedektörü: PC'de doğrudan çalışır (GPU kullanabilir).
YOLO + ByteTrack + KKD mantığı -> etiketli kare ve olayları Docker'daki dashboard'a HTTP ile yollar."""
import base64
import json
import os
import threading
import time
from collections import Counter

import cv2
import requests
import torch
from ultralytics import YOLO

import ppe_core as core

MODEL_PATH = os.getenv("MODEL_PATH", "models/best.pt")
VIDEO_SOURCE = os.getenv("VIDEO_SOURCE", "videos/video.mp4")   # dosya, 0 (webcam) veya rtsp://...
DASHBOARD_URL = os.getenv("DASHBOARD_URL", "http://localhost:8000").rstrip("/")
INGEST_TOKEN = os.getenv("INGEST_TOKEN", "")
DEVICE = os.getenv("DEVICE", "0" if torch.cuda.is_available() else "cpu")
IMGSZ = int(os.getenv("IMGSZ", 960))
CONF = float(os.getenv("CONF", 0.25))
TRACKER = os.getenv("TRACKER", "bytetrack.yaml")
LOOP = os.getenv("LOOP", "1") == "1"
VID_STRIDE = int(os.getenv("VID_STRIDE", 1))       # her N. kareyi işle
JPEG_QUALITY = int(os.getenv("JPEG_QUALITY", 80))
STREAM_WIDTH = int(os.getenv("STREAM_WIDTH", 960))

GREEN, RED, GRAY = (0, 200, 0), (0, 0, 255), (160, 160, 160)


class Sender(threading.Thread):
    """Ağ gecikmesi inference'ı yavaşlatmasın diye ayrı thread. Kareler: en yenisi kazanır.
    Olaylar (alert) ise asla düşmez; gönderilemezse bir sonraki denemeye kalır."""
    def __init__(self):
        super().__init__(daemon=True)
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
                           json={"report": report, "status": status}, timeout=5)
        except requests.RequestException as e:
            print("rapor gönderilemedi:", e, flush=True)

    def run(self):
        while True:
            with self.cv:
                self.cv.wait_for(lambda: self.latest is not None)
                (jpg, payload), self.latest = self.latest, None
                events, self.events = self.events, []
            payload["events"] = events
            try:
                r = self.sess.post(f"{DASHBOARD_URL}/ingest/frame",
                                   files={"frame": ("f.jpg", jpg, "image/jpeg")},
                                   data={"payload": json.dumps(payload)}, timeout=5)
                r.raise_for_status()
            except requests.RequestException as e:
                with self.cv:
                    self.events[:0] = events            # olayları geri koy
                if time.time() - self._last_err > 5:
                    print(f"[!] Dashboard'a ulaşılamıyor ({DASHBOARD_URL}): {e}", flush=True)
                    self._last_err = time.time()
                time.sleep(1)


SENDER = Sender()


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


def run_once(model, loop_no):
    src = int(VIDEO_SOURCE) if VIDEO_SOURCE.isdigit() else VIDEO_SOURCE
    cap = cv2.VideoCapture(src)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    W, H = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    if W == 0:
        raise RuntimeError(f"Video açılamadı: {VIDEO_SOURCE}")

    is_file = isinstance(src, str) and os.path.isfile(src)
    meta = {"source": str(VIDEO_SOURCE), "fps": round(fps, 1), "width": W, "height": H,
            "status": "running", "error": None, "loop": loop_no, "proc_fps": 0.0}

    model.predictor = None                       # tracker durumunu sıfırla
    monitor = core.PPEMonitor()
    results = model.track(source=src, stream=True, persist=True, tracker=TRACKER,
                          conf=CONF, imgsz=IMGSZ, device=DEVICE, vid_stride=VID_STRIDE,
                          verbose=False)

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

        if idx % 30 == 1:
            seen = Counter(model.names[int(c)] for c in cls) if has else {}
            print(f"[dbg] kare {idx} t={t:.1f}s sınıflar={dict(seen)} "
                  f"takip_id={'var' if (has and b.id is not None) else 'YOK'} "
                  f"kişi={len(persons)}", flush=True)

        helmet_map = core.match_items(persons, helmets, core.head_region)
        vest_map = core.match_items(persons, vests, core.torso_region)
        statuses, events = monitor.update(t, (W, H), persons, helmet_map, vest_map)

        for e in events:
            if e["type"] == "start":
                attach_snapshot(e, frame)
            print(f"[{e['t']:6.1f}s] {e['type'].upper():7} ID {e['pid']} {e['missing']}", flush=True)

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
            SENDER.submit(jpg, {"meta": dict(meta), "summary": summary, "people": people}, events)

        if is_file:                              # dosyayı gerçek zamanlı hızda oynat
            ahead = wall0 + t - time.time()
            if ahead > 0:
                time.sleep(ahead)

    report = [{"pid": int(pid), "first": round(s["first"], 1), "last": round(s["last"], 1),
               "n": s["n"], "helmet": round(100 * s["h"] / s["n"]),
               "vest": round(100 * s["v"] / s["n"]), "violation": s["ever_violation"]}
              for pid, s in sorted(monitor.stats.items()) if s["n"] > 0]
    SENDER.post_report(report, "running" if LOOP else "finished")


def main():
    print(f"Model: {MODEL_PATH} | Cihaz: {DEVICE} | Kaynak: {VIDEO_SOURCE} | Dashboard: {DASHBOARD_URL}",
          flush=True)
    SENDER.start()
    model = YOLO(MODEL_PATH)
    loop_no = 0
    while True:
        loop_no += 1
        try:
            run_once(model, loop_no)
        except Exception as exc:                  # noqa: BLE001
            print("HATA:", exc, flush=True)
            time.sleep(3)                         # RTSP kopması vb. için yeniden dene
            continue
        if not LOOP:
            break


if __name__ == "__main__":
    main()
