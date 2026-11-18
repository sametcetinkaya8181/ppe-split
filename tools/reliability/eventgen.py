"""Sentetik kamera (yalnız test): YOLO yerine senaryolu tespitler, geri kalan zincir GERÇEK üretim kodu —
ppe_core.PPEMonitor -> protocol.enrich_event/IncidentTracker -> outbox.DurableSender (SQLite WAL) -> dashboard.
Böylece arıza enjeksiyonu GPU/video olmadan, deterministik olay akışıyla, gerçek outbox ve gerçek PostgreSQL'e karşı
yapılır. Defter (ledger.jsonl): outbox'a COMMIT edilmiş her olay/rapor kimliği (fsync). Doğrulayıcı defteri DB ile
karşılaştırır (kayıp / çift kayıt).

  python eventgen.py --camera relg1 --outbox DIR --ledger FILE --duration 60 [--run-seconds 20]
Ortam: DASHBOARD_URL, INGEST_TOKEN, DETECTOR_ENV_FILE (token yenileme), OUTBOX_MAX_ITEMS (kapasite testi)."""
import argparse
import base64
import json
import os
import signal
import sys
import time
from pathlib import Path

DET = Path(__file__).resolve().parents[2] / "detector"
sys.path.insert(0, str(DET))

import numpy as np                                   # noqa: E402

import detector_env                                  # noqa: E402
import outbox as ob                                  # noqa: E402
import ppe_core as core                              # noqa: E402
import protocol                                      # noqa: E402

W, H = 1280, 720


def _to_kbi(*_):
    raise KeyboardInterrupt


if hasattr(signal, "SIGBREAK"):
    signal.signal(signal.SIGBREAK, _to_kbi)


def jpeg(seed: int) -> bytes:
    import cv2
    img = np.random.default_rng(seed).integers(0, 255, (36, 64, 3), dtype=np.uint8)   # sentetik, kişi yok
    return cv2.imencode(".jpg", img)[1].tobytes()


def video_frames(path: str, width: int, n: int = 48, quality: int = 80) -> list:
    """Bellek testleri için gerçekçi boyutta canlı kareler: videodan eşit aralıklı n kare, dedektörle aynı
    küçültme (STREAM_WIDTH) ve JPEG kalitesi. Kareler yalnız gönderilir; diske yazılmaz."""
    import cv2
    cap = cv2.VideoCapture(path)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or n
    out = []
    for i in range(n):
        cap.set(cv2.CAP_PROP_POS_FRAMES, i * total // n)
        ok, img = cap.read()
        if not ok:
            break
        h, w = img.shape[:2]
        if w > width:
            img = cv2.resize(img, (width, int(h * width / w)))
        out.append(cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])[1].tobytes())
    cap.release()
    if not out:
        raise RuntimeError(f"videodan kare okunamadı: {path}")
    return out


class Ledger:
    def __init__(self, path: Path):
        self.f = open(path, "a", encoding="utf-8")

    def write(self, **rec):
        self.f.write(json.dumps(rec) + "\n")
        self.f.flush()
        os.fsync(self.f.fileno())


def persons_at(t: float, n: int, period: float):
    """n kişi; her biri `period` sn baretli / `period` sn baretsiz (kaydırmalı) -> düzenli start/ongoing/end."""
    persons, helmet = [], {}
    for i in range(n):
        x = 80 + i * 300
        box = (x, 100, x + 160, 600)
        persons.append((i + 1, box, 0.9))
        if int((t + i * period / n) / period) % 2 == 0:
            helmet[i + 1] = (x + 50, 90, x + 110, 140)
    return persons, helmet


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--camera", required=True)
    ap.add_argument("--outbox", required=True)
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--duration", type=float, default=60)
    ap.add_argument("--run-seconds", type=float, default=30)
    ap.add_argument("--fps", type=float, default=10)
    ap.add_argument("--persons", type=int, default=3)
    ap.add_argument("--period", type=float, default=6)
    ap.add_argument("--snapshot", action="store_true", help="start olaylarına sentetik küçük JPEG ekle")
    ap.add_argument("--frame-video", help="canlı kareleri bu videodan üret (gerçekçi boyut; bellek testleri)")
    ap.add_argument("--frame-width", type=int, default=640)
    a = ap.parse_args(argv)
    frames = video_frames(a.frame_video, a.frame_width) if a.frame_video else [jpeg(i) for i in range(7)]
    url = os.environ["DASHBOARD_URL"]
    o = ob.Outbox(ob.path_for(a.camera, Path(a.outbox)), a.camera,
                  max_items=int(os.getenv("OUTBOX_MAX_ITEMS", 100_000)),
                  max_bytes=int(os.getenv("OUTBOX_MAX_BYTES", 512 * 1024 * 1024)))
    sender = ob.DurableSender(a.camera, f"Sentetik {a.camera}", url, detector_env.env_token(), o,
                              token_provider=detector_env.env_token, backoff_max=float(os.getenv("BACKOFF_MAX", 30)),
                              auth_retry=float(os.getenv("AUTH_RETRY", 30)),
                              status_path=ob.path_for(a.camera, Path(a.outbox)).with_suffix(".status.json"),
                              status_every=1.0)
    sender.start()
    ledger = Ledger(Path(a.ledger))
    t_start = time.monotonic()
    snap = base64.b64encode(jpeg(1)).decode()
    loop = 0
    try:
        while time.monotonic() - t_start < a.duration:
            loop += 1
            run_id = protocol.new_id()
            started = protocol.utc_now_iso()
            mon, tracker = core.PPEMonitor(), protocol.IncidentTracker()
            r0, idx, t = time.monotonic(), 0, 0.0
            status = "finished"
            try:
                while time.monotonic() - r0 < a.run_seconds and time.monotonic() - t_start < a.duration:
                    idx += 1
                    t = idx / a.fps
                    persons, hmap = persons_at(t, a.persons, a.period)
                    vmap = {pid: (b[0] + 20, 250, b[2] - 20, 400) for pid, b, _ in persons}
                    statuses, raw = mon.update(t, (W, H), persons, hmap, vmap)
                    events = []
                    for e in raw:
                        e = protocol.enrich_event(e, run_id=run_id, camera_id=a.camera, tracker=tracker)
                        if e["type"] == "start" and a.snapshot:
                            e["snapshot_b64"] = snap
                        events.append(e)
                    tracker.sweep(mon.state.keys(), t)
                    before = len(sender.unpersisted)
                    meta = {"source": "synthetic", "source_kind": "other", "run_id": run_id, "fps": a.fps,
                            "width": W, "height": H, "status": "running", "error": None, "loop": loop,
                            "proc_fps": a.fps}
                    sender.submit(frames[idx % len(frames)], {"meta": meta, "summary": {"t": round(t, 1),
                                  "people": len(persons), "ok": 0, "viol": 0, "analyzing": 0},
                                  "people": [], "run_id": run_id, "camera_id": a.camera}, events)
                    durable = len(sender.unpersisted) == before
                    for e in events:
                        ledger.write(kind="event", type=e["type"], event_id=e["event_id"], run_id=run_id,
                                     durable=durable, wall=time.time())
                    time.sleep(max(0.0, r0 + t - time.monotonic()))
            except KeyboardInterrupt:
                status = "interrupted"
                raise
            finally:
                tracker.close_all("run_end", t)
                # detector.py ile aynı kişi satırı biçimi (dashboard rapor yolu gerçek satırlarla çalışsın)
                report = [{"pid": int(pid), "first": round(s["first"], 1), "last": round(s["last"], 1),
                           "n": s["n"], "helmet": round(100 * s["h"] / s["n"]),
                           "vest": round(100 * s["v"] / s["n"]), "violation": s["ever_violation"]}
                          for pid, s in sorted(mon.stats.items()) if s["n"] > 0]
                sender.submit_report({"cam": a.camera, "name": f"Sentetik {a.camera}", "report": report,
                                      "status": status, "run_id": run_id, "camera_id": a.camera,
                                      "run_status": status, "loop": loop, "source_kind": "other",
                                      "started_at": started, "finished_at": protocol.utc_now_iso(),
                                      "video_time_sec": round(t, 1), "error": None,
                                      "unclosed_incidents": tracker.unclosed, "unclosed_overflow": 0,
                                      "orphan_events": 0})
                ledger.write(kind="report", run_id=run_id, status=status, wall=time.time())
    except KeyboardInterrupt:
        pass
    finally:
        res = sender.close(float(os.getenv("DRAIN_TIMEOUT", 15)))
        st = sender.snapshot_stats()
        print(json.dumps({"camera": a.camera, "pending": res["pending"], "unpersisted": res["unpersisted"],
                          "events_sent": st["events_sent"], "reports_sent": st["reports_sent"],
                          "replayed": st.get("replay_success_count")}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
