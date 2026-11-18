"""SteelBench test başlatıcısı (yalnız test): gerçek detector.main()'i DEĞİŞTİRMEDEN çalıştırır ve şu kayıtları
süreç başına JSONL deftere yazar (STEELBENCH_LEDGER_DIR):
  event  — dedektörün kalıcı outbox'a verdiği her olay (teslimden önce); snapshot baytı yazılmaz, yalnız SHA-256
  report — run raporu ve kişi satırları
  frame  — PPEMonitor.update girdisi/çıktısı: kişi kutusu, güven, durum (UYGUN/IHLAL/ANALIZ), güvenilirlik kapısı
           nedenleri (telemetry.gate_reasons), eşleşen ve eşleşmeyen baret/yelek kutuları
Kamera süreçleri 'spawn' ile açılır ve bu modülü __mp_main__ olarak yükler; yamalar her kamera sürecinde geçerlidir.
Windows'ta CTRL_BREAK -> KeyboardInterrupt (detector_launcher.py ile aynı kapanış yolu)."""
import base64
import hashlib
import json
import os
import signal
import sys
import threading
from pathlib import Path


def _to_kbi(*_):
    raise KeyboardInterrupt


if hasattr(signal, "SIGBREAK"):
    signal.signal(signal.SIGBREAK, _to_kbi)

DET = Path(__file__).resolve().parents[2] / "detector"
if str(DET) not in sys.path:
    sys.path.insert(0, str(DET))
LEDGER = Path(os.environ["STEELBENCH_LEDGER_DIR"])

import outbox as _ob        # noqa: E402
import ppe_core as _core    # noqa: E402
import telemetry as _tel    # noqa: E402

_lock = threading.Lock()
_items = {}
EV_KEYS = ("event_id", "incident_id", "run_id", "camera_id", "type", "pid", "t", "missing", "missing_helmet",
           "missing_vest", "helmet_rate", "vest_rate", "box", "occurred_at", "orphan")


def _write(rec):
    with _lock, open(LEDGER / f"tap_{os.getpid()}.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _box(b):
    return None if b is None else [round(float(v), 1) for v in b]


_orig_submit, _orig_report = _ob.DurableSender.submit, _ob.DurableSender.submit_report
_orig_match, _orig_update = _core.match_items, _core.PPEMonitor.update


def submit(self, jpg, payload, events=()):
    meta = (payload or {}).get("meta") or {}
    for e in events:
        rec = {"kind": "event", **{k: e.get(k) for k in EV_KEYS}, "frame_w": meta.get("width"),
               "frame_h": meta.get("height"), "fps": meta.get("fps")}
        b = e.get("snapshot_b64")
        if b:
            raw = base64.b64decode(b)
            rec["snapshot_sha256"], rec["snapshot_bytes"] = hashlib.sha256(raw).hexdigest(), len(raw)
        _write(rec)
    return _orig_submit(self, jpg, payload, events)


def submit_report(self, body):
    _write({"kind": "report", **{k: v for k, v in body.items() if k != "report"}, "rows": body.get("report")})
    return _orig_report(self, body)


def match_items(persons, items, region_fn):
    _items[getattr(region_fn, "__name__", "?")] = [_box(b) for b in items]
    return _orig_match(persons, items, region_fn)


def update(self, t, frame_wh, persons, helmet_map, vest_map):
    statuses, events = _orig_update(self, t, frame_wh, persons, helmet_map, vest_map)
    ps = []
    for pid, box, conf in persons:
        st = statuses.get(pid)
        ps.append({"pid": int(pid), "box": _box(box), "conf": round(float(conf), 3),
                   "status": st[0] if st else None, "h": round(float(st[1]), 3) if st else None,
                   "v": round(float(st[2]), 3) if st else None, "gate": list(_tel.gate_reasons(box, conf, frame_wh)),
                   "helmet_box": _box(helmet_map.get(pid)), "vest_box": _box(vest_map.get(pid))})
    _write({"kind": "frame", "t": round(float(t), 3), "wh": [int(frame_wh[0]), int(frame_wh[1])], "persons": ps,
            "helmets": _items.get("head_region", []), "vests": _items.get("torso_region", []),
            "events": [{"type": e.get("type"), "pid": e.get("pid")} for e in events]})
    _items.clear()
    return statuses, events


_ob.DurableSender.submit, _ob.DurableSender.submit_report = submit, submit_report
_core.match_items, _core.PPEMonitor.update = match_items, update

if __name__ == "__main__":
    import detector
    detector.main()
