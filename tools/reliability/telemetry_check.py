"""Ultralytics dış bağlantı denetimi (kanıt betiği). Alt süreçte DNS/soket/urllib çağrıları KAYDEDİLİR ve ENGELLENİR
(hiçbir paket dışarı çıkmaz); gerçek best.pt ile yerel test videosunun tamamında model.track çalıştırılır.
İki koşul: (a) dedektörün FAZ 9 varsayılanı (detector.py içe aktarılır -> YOLO_OFFLINE=1), (b) YOLO_OFFLINE=0.

  python tools/reliability/telemetry_check.py"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DET = ROOT / "detector"
PY = Path(os.getenv("REL_DETECTOR_PY", DET / ".venv" / "Scripts" / "python.exe"))
VIDEO = DET / "videos" / "16533594_1920_1080_25fps.mp4"

CHILD = r'''
import json, os, socket, sys, urllib.request
attempts = []
def _block(kind):
    def f(*a, **k):
        tgt = getattr(a[0], "full_url", a[0]) if a else None    # urllib Request -> URL; yalnız host kaydedilir
        tgt = str(tgt).split("/")[2] if tgt and "://" in str(tgt) else str(tgt)[:120]
        attempts.append({"kind": kind, "target": tgt})
        raise OSError("telemetry_check: dış bağlantı engellendi")
    return f
def _fake_dns(host, *a, **k):
    """DNS denemesi kaydedilir; 'çevrimiçi' sanılması için belge adresi (203.0.113.1, TEST-NET-3) döner -> sonraki
    gerçek bağlantı denemeleri (analitik POST vb.) de görünür olur ve engellenir."""
    attempts.append({"kind": "dns", "target": str(host)[:120]})
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.1", 0))]
socket.getaddrinfo = _fake_dns
socket.create_connection = _block("connect")
_orig_connect = socket.socket.connect
def _sock_connect(self, addr):
    host = addr[0] if isinstance(addr, tuple) else str(addr)
    if host not in ("127.0.0.1", "::1", "localhost"):
        attempts.append({"kind": "socket", "target": str(host)})
        raise OSError("engellendi")
    return _orig_connect(self, addr)
socket.socket.connect = _sock_connect
urllib.request.urlopen = _block("urlopen")
sys.path.insert(0, sys.argv[1])
os.chdir(sys.argv[1])
if sys.argv[3] == "detector":
    import detector                                    # FAZ 9 varsayılanı: YOLO_OFFLINE=1 (ortamda yoksa)
from ultralytics import YOLO
from ultralytics.utils import ONLINE, SETTINGS
from ultralytics.utils.events import events
import time
m = YOLO("models/best.pt")
n = 0
for r in m.track(source=sys.argv[2], stream=True, persist=True, tracker="bytetrack.yaml", conf=0.25, imgsz=640,
                 device=os.environ.get("DEVICE", "0"), vid_stride=2, verbose=False):
    n += 1                                              # tüm video: on_predict_end (analitik kancası) çalışsın
time.sleep(1.0)                                         # analitik gönderimi arka plan thread'inde
print("RESULT " + json.dumps({"YOLO_OFFLINE": os.environ.get("YOLO_OFFLINE"), "ONLINE": ONLINE,
      "settings_sync": SETTINGS["sync"], "events_enabled": events.enabled, "frames": n,
      "queued_events": len(events.events), "attempts": attempts}))
'''


def run(mode: str, offline: str | None) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in ("YOLO_OFFLINE", "PYTEST_CURRENT_TEST")}
    if offline is not None:
        env["YOLO_OFFLINE"] = offline
    r = subprocess.run([str(PY), "-I", "-c", CHILD, str(DET), str(VIDEO), mode], env=env, capture_output=True,
                       text=True, timeout=300, cwd=DET)
    line = next((l for l in r.stdout.splitlines() if l.startswith("RESULT ")), None)
    if line is None:
        return {"error": (r.stderr or r.stdout)[-1500:]}
    return json.loads(line[7:])


def main():
    out = {"a_detector_default": run("detector", None), "b_offline_disabled": run("plain", "0")}
    for k, v in out.items():
        if "attempts" in v:
            v["attempt_summary"] = sorted({f"{a['kind']}:{a['target']}" for a in v["attempts"]})
    print(json.dumps(out, indent=1, ensure_ascii=False))
    od = Path(__file__).resolve().parent / "output"
    od.mkdir(exist_ok=True)
    (od / "telemetry_check.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
