"""GPU eşdeğerlik testi (FAZ 10-A.1): verilen yorumlayıcının venv'inde gerçek best.pt + ByteTrack + PPEMonitor'ü
dedektörle aynı ayarlarla (model.track, persist, bytetrack.yaml, CONF, IMGSZ, VID_STRIDE, GPU) yerel bir videonun
ilk N işlenen karesinde çalıştırır; kare başına sınıf/güven/kutu/track kimliği ve PPEMonitor kararlarını JSON'a
yazar. İki venv'in çıktısı `--compare` ile karşılaştırılır (paket yükseltmesi model davranışını değiştirdi mi?).
Görüntü kaydetmez; yalnız sayılar.

  <venv>\\Scripts\\python.exe tools/reliability/gpu_parity.py --out A.json [--frames 300]
  python tools/reliability/gpu_parity.py --compare A.json B.json"""
import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

DET = Path(__file__).resolve().parents[2] / "detector"


def run(a):
    os.environ.setdefault("YOLO_OFFLINE", "1")
    sys.path.insert(0, str(DET))
    os.chdir(DET)
    import cv2
    import numpy
    import PIL
    import torch
    import ultralytics
    from ultralytics import YOLO

    import ppe_core as core
    model_path = DET / "models" / "best.pt"
    sha = hashlib.sha256(model_path.read_bytes()).hexdigest()
    env = {"python": sys.version.split()[0], "prefix": Path(sys.prefix).name, "torch": torch.__version__,
           "cuda": torch.version.cuda, "cuda_available": torch.cuda.is_available(),
           "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
           "ultralytics": ultralytics.__version__, "numpy": numpy.__version__, "pillow": PIL.__version__,
           "opencv": cv2.__version__, "model_sha256": sha}
    model = YOLO(str(model_path))
    names = model.names
    cap = cv2.VideoCapture(str(a.video))
    W, H = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    cap.release()
    mon = core.PPEMonitor()
    frames, t0 = [], time.perf_counter()
    for i, r in enumerate(model.track(source=str(a.video), stream=True, persist=True, tracker="bytetrack.yaml",
                                      conf=a.conf, imgsz=a.imgsz, device=a.device, vid_stride=a.stride,
                                      verbose=False)):
        b = r.boxes
        cls = b.cls.int().tolist() if b is not None else []
        conf = [round(x, 3) for x in b.conf.tolist()] if b is not None else []
        xyxy = [[round(v, 1) for v in row] for row in b.xyxy.tolist()] if b is not None else []
        ids = b.id.int().tolist() if b is not None and b.id is not None else []
        persons, helmets, vests = [], {}, {}
        for k, c in enumerate(cls):
            if names[c] == "Person" and k < len(ids):
                persons.append((ids[k], tuple(xyxy[k]), conf[k]))
        hb = [tuple(xyxy[k]) for k, c in enumerate(cls) if names[c] == "helmet"]
        vb = [tuple(xyxy[k]) for k, c in enumerate(cls) if names[c] == "vest"]
        for pid, box, _ in persons:                  # parite için basit atama (karar mantığı PPEMonitor'de)
            helmets[pid] = next((h for h in hb if box[0] <= (h[0] + h[2]) / 2 <= box[2] and box[1] - 40 <= h[1] <= box[3]), None)
            vests[pid] = next((v for v in vb if box[0] <= (v[0] + v[2]) / 2 <= box[2] and box[1] <= v[1] <= box[3]), None)
        statuses, events = mon.update((i * a.stride) / fps, (W, H), persons,
                                      {k: v for k, v in helmets.items() if v}, {k: v for k, v in vests.items() if v})
        frames.append({"cls": cls, "conf": conf, "xyxy": xyxy, "ids": ids,
                       "events": [[e.get("type"), e.get("pid")] for e in events]})
        if i + 1 >= a.frames:
            break
    env["sec"] = round(time.perf_counter() - t0, 1)
    env["frames"] = len(frames)
    env["detections"] = sum(len(f["cls"]) for f in frames)
    Path(a.out).write_text(json.dumps({"env": env, "names": names, "frames": frames}), encoding="utf-8")
    print(json.dumps(env, ensure_ascii=False))


def iou(p, q):
    ix = max(0, min(p[2], q[2]) - max(p[0], q[0]))
    iy = max(0, min(p[3], q[3]) - max(p[1], q[1]))
    inter = ix * iy
    u = (p[2] - p[0]) * (p[3] - p[1]) + (q[2] - q[0]) * (q[3] - q[1]) - inter
    return inter / u if u > 0 else 1.0


def compare(pa, pb):
    A, B = (json.loads(Path(p).read_text(encoding="utf-8")) for p in (pa, pb))
    res = {"env_a": A["env"], "env_b": B["env"], "names_equal": A["names"] == B["names"],
           "frames": min(len(A["frames"]), len(B["frames"]))}
    same_count = same_cls = same_ids = same_events = 0
    ious, dconf, worst = [], [], 0.0
    for fa, fb in zip(A["frames"], B["frames"]):
        same_count += len(fa["cls"]) == len(fb["cls"])
        same_cls += sorted(fa["cls"]) == sorted(fb["cls"])
        same_ids += sorted(fa["ids"]) == sorted(fb["ids"])
        same_events += fa["events"] == fb["events"]
        if fa["cls"] == fb["cls"]:
            for x, y, ca, cb in zip(fa["xyxy"], fb["xyxy"], fa["conf"], fb["conf"]):
                ious.append(iou(x, y))
                dconf.append(abs(ca - cb))
    n = res["frames"]
    res.update({"same_detection_count": f"{same_count}/{n}", "same_classes": f"{same_cls}/{n}",
                "same_track_ids": f"{same_ids}/{n}", "same_ppe_events": f"{same_events}/{n}",
                "box_iou_min": round(min(ious), 4) if ious else None,
                "box_iou_mean": round(sum(ious) / len(ious), 5) if ious else None,
                "conf_absdiff_max": round(max(dconf), 4) if dconf else None,
                "events_a": sum(len(f["events"]) for f in A["frames"]),
                "events_b": sum(len(f["events"]) for f in B["frames"])})
    print(json.dumps(res, ensure_ascii=False, indent=1))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    ap.add_argument("--compare", nargs=2)
    ap.add_argument("--video", default=str(DET / "videos" / "16533594_1920_1080_25fps.mp4"))
    ap.add_argument("--frames", type=int, default=300)
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--stride", type=int, default=2)
    ap.add_argument("--device", default="0")
    a = ap.parse_args(argv)
    if a.compare:
        compare(*a.compare)
    else:
        run(a)


if __name__ == "__main__":
    main()
