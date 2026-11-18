"""FAZ 10-A.7 canlı izleme UI fixture'ı: İZOLE '_test' veritabanı + gerçek ingest uçlarına sürekli kare/olay besleyen
sahte dedektör. Ana DB'ye ve gerçek videolara dokunmaz; görüntüler birkaç baytlık test JPEG'leridir.

    UI_PASSWORD=<geçici parola> python -m tests.ui.serve_live_fixture      # tests konteynerinde, :8011

Kullanıcılar: ui_admin (admin), ui_user (user = supervisor rolü), ui_viewer (viewer). Parola kaynak kodda YOKTUR;
UI_PASSWORD ortam değişkeninden okunur (yalnız bu izole veritabanı için)."""
import os
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import httpx

from tests.helpers import B64_A, JPEG_A, JPEG_B, iso
from tests.ui.serve_fixture import db, make_users

PORT = 8011
LONG_NAME = "Kuzey cephe iskele ve vinç sahası — çok uzun kamera adı (taşma testi) <b>kalın değil</b>"
CAMS = [("cam_a", "Giriş kapısı"), ("cam_b", LONG_NAME), ("cam_c", "Depo"), ("cam_d", "Kule vinci")]


def _event(kind, *, run, inc, cam, pid, t, at, missing="Baretsiz", mh=True, mv=False, snapshot=None):
    ev = {"type": kind, "pid": pid, "t": t, "missing": "" if kind == "end" else missing,
          "helmet_rate": 60 if kind == "end" else 12, "vest_rate": 90 if kind == "end" else 78,
          "box": [100, 100, 200, 400], "event_id": str(uuid.uuid4()), "run_id": str(run), "camera_id": cam,
          "incident_id": str(inc), "occurred_at": iso(at), "missing_helmet": mh and kind != "end",
          "missing_vest": mv and kind != "end"}
    if snapshot:
        ev["snapshot_b64"] = snapshot
    return ev


def _payload(cam, name, run, t, events=()):
    return {"cam": cam, "name": name, "run_id": str(run), "camera_id": cam,
            "meta": {"source": f"videos/{cam}.mp4", "source_kind": "file", "fps": 25.0, "width": 1280, "height": 720,
                     "status": "running", "error": None, "loop": 1, "proc_fps": 12.5, "run_id": str(run)},
            "summary": {"t": round(t, 1), "people": 1, "ok": 0, "viol": 1 if cam == "cam_a" else 0, "analyzing": 0,
                        "raw_helmets": 0, "raw_vests": 1},
            "people": [{"pid": 7, "status": "IHLAL" if cam == "cam_a" else "UYGUN", "helmet": 12, "vest": 78}],
            "events": list(events)}


def feeder(base: str, stop: threading.Event):
    """Kamera başına sürekli kare; cam_a/cam_b'de snapshot'lı ihlal başlangıcı, sürüyor ve bitiş olayları."""
    import json
    c = httpx.Client(base_url=base, timeout=5)
    runs = {cid: uuid.uuid4() for cid, _ in CAMS}
    incs = {"cam_a": uuid.uuid4(), "cam_b": uuid.uuid4()}
    plan = {(0, "cam_a"): "start", (0, "cam_b"): "start", (5, "cam_a"): "ongoing", (12, "cam_b"): "end"}
    n, t0 = 0, time.time()
    while not stop.is_set():
        for k, (cid, name) in enumerate(CAMS):
            kind = plan.get((n, cid))
            evs = []
            if kind:
                evs = [_event(kind, run=runs[cid], inc=incs[cid], cam=cid, pid=7 + k, t=round(1.0 + n * 0.5, 1),
                              at=datetime.now(timezone.utc).replace(microsecond=0) - timedelta(seconds=1),
                              missing="Baretsiz + Yeleksiz" if cid == "cam_b" else "Baretsiz", mv=cid == "cam_b",
                              snapshot=B64_A if kind == "start" else None)]
            body = json.dumps(_payload(cid, name, runs[cid], time.time() - t0, evs))
            try:
                c.post("/ingest/frame", files={"frame": ("f.jpg", JPEG_A if n % 2 else JPEG_B, "image/jpeg")},
                       data={"payload": body})
            except httpx.HTTPError:
                pass
        n += 1
        stop.wait(0.4)


def seed_finished_run(base: str):
    """Bitmiş run + run raporu (kişi satırları) — geçmiş/rapor ekranı için."""
    c = httpx.Client(base_url=base, timeout=5)
    run, inc = uuid.uuid4(), uuid.uuid4()
    at = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=20)
    import json
    evs = [_event("start", run=run, inc=inc, cam="cam_c", pid=3, t=2.0, at=at, snapshot=B64_A),
           _event("end", run=run, inc=inc, cam="cam_c", pid=3, t=6.0, at=at + timedelta(seconds=4))]
    c.post("/ingest/frame", files={"frame": ("f.jpg", JPEG_A, "image/jpeg")},
           data={"payload": json.dumps(_payload("cam_c", "Depo", run, 6.0, evs))}).raise_for_status()
    rep = {"cam": "cam_c", "name": "Depo", "status": "running", "run_id": str(run), "camera_id": "cam_c",
           "run_status": "finished", "loop": 1, "source_kind": "file", "started_at": iso(at - timedelta(seconds=5)),
           "finished_at": iso(at + timedelta(seconds=30)), "video_time_sec": 30.0, "error": None,
           "unclosed_incidents": [], "unclosed_overflow": 0, "orphan_events": 0,
           "report": [{"pid": 3, "first": 0.5, "last": 9.5, "n": 40, "helmet": 15, "vest": 95, "violation": True}]}
    c.post("/ingest/report", json=rep).raise_for_status()


def main():
    url = db("ppe_ui_live_test")
    data_dir = "/tmp/ui-live"
    os.makedirs(f"{data_dir}/snapshots", exist_ok=True)
    make_users(url, (("ui_admin", "admin", None), ("ui_user", os.environ.get("UI_USER_ROLE", "user"), None),
                     ("ui_viewer", "viewer", None)))
    env = dict(os.environ, DATABASE_URL=url, DATA_DIR=data_dir, ACTIVE_WINDOW_SEC="3600", INGEST_TOKEN="")
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", str(PORT),
                             "--log-level", "warning"], env=env)
    base = f"http://127.0.0.1:{PORT}"
    for _ in range(100):
        try:
            if httpx.get(f"{base}/healthz", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.2)
    seed_finished_run(base)
    stop = threading.Event()
    threading.Thread(target=feeder, args=(base, stop), daemon=True).start()
    print(f"UI canlı fixture hazır: {PORT}", flush=True)
    try:
        proc.wait()
    finally:
        stop.set()


if __name__ == "__main__":
    main()
