"""Üretim karar akışının çevrimdışı, ölçümlü kopyası:
YOLO + ByteTrack (model.track, persist=True) -> core.match_items -> core.PPEMonitor -> protocol.enrich_event/
IncidentTracker. Karar mantığı detector._process ile birebir aynıdır (test_evaluation_pipeline eşdeğerlik testi);
farklar: (1) kareler cv2 ile okunur (Ultralytics LoadImagesAndVideos ile aynı grab×stride + retrieve), (2) gerçek
zaman beklemesi yoktur (çevrimdışı hızlı işleme), (3) gönderici yoktur. no_helmet (sınıf 7) yalnız sayılır, karara
katılmaz."""
import time
from collections import Counter, defaultdict

import ppe_core as core
import protocol

from .metrics import box_iou, match_boxes, person_frame_confusion, match_incidents, incidents_from_events, \
    false_alarms_per_camera_hour, summary_stats, safe_div, wilson

CLASS_NO_HELMET = 7          # yalnız analiz; üretim kararına eklenmez
ITEMS = ("helmet", "vest")


def extract(r):
    """detector._process ile aynı sınıf ayrıştırması. Döner: (persons, helmets, vests, no_helmet_sayısı)."""
    persons, helmets, vests, n_no_helmet = [], [], [], 0
    b = r.boxes
    if b is not None and len(b):
        cls = b.cls.cpu().numpy().astype(int)
        xyxy = b.xyxy.cpu().numpy()
        conf = b.conf.cpu().numpy()
        ids = b.id.cpu().numpy().astype(int) if b.id is not None else None
        helmets = list(xyxy[cls == core.CLASS_HELMET])
        vests = list(xyxy[cls == core.CLASS_VEST])
        n_no_helmet = int((cls == CLASS_NO_HELMET).sum())
        if ids is not None:
            pm = cls == core.CLASS_PERSON
            persons = list(zip(ids[pm], xyxy[pm], conf[pm]))
    return persons, helmets, vests, n_no_helmet


class DecisionStep:
    """Bir run'ın karar durumu (PPEMonitor + IncidentTracker). Üretimdeki çağrı sırası korunur."""

    def __init__(self, run_id: str, camera_id: str, frame_wh, snapshot_fn=None):
        self.run_id, self.camera_id, self.wh = run_id, camera_id, frame_wh
        self.monitor, self.tracker = core.PPEMonitor(), protocol.IncidentTracker()
        self.snapshot_fn = snapshot_fn
        self.events, self.contract_errors = [], 0

    def step(self, t, persons, helmets, vests, frame=None) -> dict:
        t0 = time.perf_counter()
        helmet_map = core.match_items(persons, helmets, core.head_region)
        vest_map = core.match_items(persons, vests, core.torso_region)
        t1 = time.perf_counter()
        statuses, raw = self.monitor.update(t, self.wh, persons, helmet_map, vest_map)
        t2 = time.perf_counter()
        events = []
        for e in raw:
            try:
                e = protocol.enrich_event(e, run_id=self.run_id, camera_id=self.camera_id, tracker=self.tracker)
            except protocol.ProtocolError:
                self.contract_errors += 1
                continue
            if e["type"] == "start" and self.snapshot_fn is not None and frame is not None:
                self.snapshot_fn(e, frame)
            events.append(e)
        self.tracker.sweep(self.monitor.state.keys(), t)
        t3 = time.perf_counter()
        people = []
        for pid, box, conf in persons:
            pid = int(pid)
            st = self.monitor.state.get(pid, {})
            status, hr, vr = statuses.get(pid, ("ANALIZ", 0.0, 0.0))
            people.append({"pid": pid, "box": [float(x) for x in box], "conf": float(conf), "status": status,
                           "decided": bool(st.get("decided")), "helmet_viol": bool(st.get("helmet_viol")),
                           "vest_viol": bool(st.get("vest_viol")), "h_rate": hr, "v_rate": vr,
                           "helmet_box": [float(x) for x in helmet_map[pid]] if pid in helmet_map else None,
                           "vest_box": [float(x) for x in vest_map[pid]] if pid in vest_map else None})
        self.events += events
        return {"t": t, "people": people, "events": events, "ms_match": (t1 - t0) * 1e3,
                "ms_monitor": (t2 - t1) * 1e3, "ms_alert": (t3 - t2) * 1e3}

    def close(self, t):
        self.tracker.close_all("run_end", t)


# ---------------------------------------------------------------- gerçek model ile video
def _install_tracker_timer(model, box: dict):
    """ByteTrack update süresini ölçmek için predictor.trackers[*].update sarılır (yalnız değerlendirme süreci)."""
    pred = getattr(model, "predictor", None)
    for trk in getattr(pred, "trackers", None) or []:
        if getattr(trk.update, "_ppe_timed", False):
            continue
        orig = trk.update

        def timed(*a, _orig=orig, **kw):
            s = time.perf_counter()
            try:
                return _orig(*a, **kw)
            finally:
                box["ms"] = (time.perf_counter() - s) * 1e3
        timed._ppe_timed = True
        trk.update = timed


def run_video(model, path: str, settings: dict, *, camera_id: str = "eval", max_frames: int | None = None,
              snapshot_fn=None, resource_every: int = 25) -> dict:
    import cv2
    import psutil
    import torch
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    if not fps or fps != fps or fps > 120:           # detector.run_once ile aynı düzeltme
        fps = 25.0
    W, H = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    n_total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if W == 0:
        raise RuntimeError("video açılamadı")
    stride = int(settings["vid_stride"])
    model.predictor = None                            # üretimdeki gibi tracker durumunu sıfırla
    step = DecisionStep(protocol.new_id(), camera_id, (W, H), snapshot_fn)
    proc = psutil.Process()
    proc.cpu_percent(None)
    cuda = torch.cuda.is_available() and str(settings["device"]) not in ("cpu", "mps")
    if cuda:
        torch.cuda.reset_peak_memory_stats()
    frames, timing, resources, tbox = [], defaultdict(list), [], {}
    idx, wall0, t = 0, time.perf_counter(), 0.0
    while max_frames is None or idx < max_frames:
        s0 = time.perf_counter()
        ok = False
        for _ in range(stride):
            ok = cap.grab()
            if not ok:
                break
        if not ok:
            break
        ok, frame = cap.retrieve()
        if not ok or frame is None:
            break
        s1 = time.perf_counter()
        idx += 1
        t = idx * stride / fps                        # detector._process ile aynı zaman formülü
        tbox["ms"] = None
        r = model.track(frame, persist=True, tracker=settings["tracker"], conf=settings["conf"],
                        imgsz=settings["imgsz"], device=settings["device"], verbose=False)[0]
        if cuda:
            torch.cuda.synchronize()
        s2 = time.perf_counter()
        _install_tracker_timer(model, tbox)
        persons, helmets, vests, n_noh = extract(r)
        rec = step.step(t, persons, helmets, vests, frame)
        s3 = time.perf_counter()
        sp = getattr(r, "speed", {}) or {}
        timing["decode_ms"].append((s1 - s0) * 1e3)
        timing["track_call_ms"].append((s2 - s1) * 1e3)
        timing["preprocess_ms"].append(sp.get("preprocess"))
        timing["inference_ms"].append(sp.get("inference"))
        timing["postprocess_ms"].append(sp.get("postprocess"))
        timing["bytetrack_ms"].append(tbox.get("ms"))
        timing["match_ms"].append(rec["ms_match"])
        timing["monitor_ms"].append(rec["ms_monitor"])
        timing["alert_ms"].append(rec["ms_alert"])
        timing["frame_total_ms"].append((s3 - s0) * 1e3)
        if any(e["type"] == "start" for e in rec["events"]):
            timing["alert_frame_total_ms"].append((s3 - s0) * 1e3)
        frames.append({"idx": idx, "frame": idx * stride - 1, "t": t, "people": rec["people"],
                       "events": [e["type"] for e in rec["events"]], "raw_helmets": len(helmets),
                       "raw_vests": len(vests), "raw_no_helmet": n_noh})
        if idx % resource_every == 1:
            resources.append({"idx": idx, "cpu_percent": proc.cpu_percent(None),
                              "rss_mb": proc.memory_info().rss / 2**20})
    wall = time.perf_counter() - wall0
    cap.release()
    step.close(t)
    gpu = None
    if cuda:
        gpu = {"max_allocated_mb": torch.cuda.max_memory_allocated() / 2**20,
               "max_reserved_mb": torch.cuda.max_memory_reserved() / 2**20, "device_used_mb": _nvml_used_mb()}
    warm = 5                                          # ilk kareler (CUDA/cuDNN ısınması) yüzdeliklere katılmaz
    timing_summary = {k: summary_stats(v[warm:] if len(v) > warm * 2 else v) for k, v in timing.items()}
    timing_summary["first_frame_total_ms"] = timing["frame_total_ms"][0] if timing["frame_total_ms"] else None
    video_sec = idx * stride / fps
    return {"path_name": path.replace("\\", "/").rsplit("/", 1)[-1], "fps": fps, "width": W, "height": H,
            "frame_count": n_total, "stride": stride, "processed_frames": idx, "video_sec": video_sec,
            "wall_sec": wall, "effective_proc_fps": idx / wall if wall else None,
            "required_proc_fps": fps / stride, "realtime_factor": (video_sec / wall) if wall else None,
            "timing_ms": timing_summary, "resources": resources, "gpu": gpu, "frames": frames,
            "events": step.events, "unclosed": list(step.tracker.unclosed), "orphans": step.tracker.orphans,
            "contract_errors": step.contract_errors,
            "report": [{"pid": int(pid), "n": s["n"], "violation": s["ever_violation"]}
                       for pid, s in sorted(step.monitor.stats.items()) if s["n"] > 0]}


def _nvml_used_mb():
    try:
        import pynvml
        pynvml.nvmlInit()
        h = pynvml.nvmlDeviceGetHandleByIndex(0)
        return pynvml.nvmlDeviceGetMemoryInfo(h).used / 2**20
    except Exception:                                 # noqa: BLE001 — ölçüm yoksa None
        return None


def production_stream_signature(model, path: str, settings: dict, max_frames: int) -> list:
    """Üretimdeki çağrı biçimi (source=dosya, stream=True, vid_stride) ile kare başına (id, sınıf, kutu) imzası.
    run_video'nun kare okuma yolunun üretimle aynı sonucu verdiğini doğrulamak için."""
    model.predictor = None
    out = []
    for i, r in enumerate(model.track(source=path, stream=True, persist=True, tracker=settings["tracker"],
                                      conf=settings["conf"], imgsz=settings["imgsz"], device=settings["device"],
                                      vid_stride=settings["vid_stride"], verbose=False), start=1):
        out.append(_signature(r))
        if i >= max_frames:
            break
    model.predictor = None
    return out


def frame_signatures(model, path, settings, max_frames) -> list:
    import cv2
    model.predictor = None
    cap, out = cv2.VideoCapture(path), []
    while len(out) < max_frames:
        if not all(cap.grab() for _ in range(settings["vid_stride"])):
            break
        ok, frame = cap.retrieve()
        if not ok or frame is None:
            break
        out.append(_signature(model.track(frame, persist=True, tracker=settings["tracker"], conf=settings["conf"],
                                          imgsz=settings["imgsz"], device=settings["device"], verbose=False)[0]))
    cap.release()
    model.predictor = None
    return out


def _signature(r):
    b = r.boxes
    if b is None or not len(b):
        return []
    ids = b.id.cpu().numpy().astype(int).tolist() if b.id is not None else [None] * len(b)
    return sorted(zip(ids, b.cls.cpu().numpy().astype(int).tolist(),
                      [tuple(round(float(x), 1) for x in bb) for bb in b.xyxy.cpu().numpy()]),
                  key=lambda x: (x[1], x[2]))


# ---------------------------------------------------------------- GT karşılaştırması
def _height_bin(box):
    h = box[3] - box[1]
    return "<60px" if h < 60 else "60-120px" if h < 120 else "120-240px" if h < 240 else ">=240px"


def evaluate_against_gt(run: dict, doc: dict, *, iou_person: float = 0.5, iou_item: float = 0.3,
                        tol_start: float = 1.0, tol_end: float = 2.0) -> dict:
    """run_video çıktısını ppe-eval-video/1 GT belgesiyle karşılaştırır. Kişi-kare ve incident düzeyleri AYRI."""
    from .annotations import frames_by_index, ground_truth_incidents
    vid = doc["video"]["video_id"]
    gt_frames = frames_by_index(doc)
    by_frame = {f["frame"]: f for f in run["frames"]}
    rows = {i: [] for i in ITEMS}
    rows_by = defaultdict(lambda: {i: [] for i in ITEMS})
    assoc = {i: Counter() for i in ITEMS}
    votes = defaultdict(Counter)
    gt_track_pids = defaultdict(list)
    not_processed = 0
    for f, gpersons in sorted(gt_frames.items()):
        pf = by_frame.get(f)
        if pf is None:
            not_processed += 1
            continue
        preds = pf["people"]
        matches, fp_idx, fn_idx = match_boxes([g["box"] for g in gpersons], [(p["box"], p["conf"]) for p in preds],
                                              iou_person)
        pairs = {gi: preds[pi] for pi, gi, _ in matches}
        for gi, g in enumerate(gpersons):
            p = pairs.get(gi)
            if p is not None:
                votes[(vid, p["pid"])][g["track_id"]] += 1
                gt_track_pids[g["track_id"]].append(p["pid"])
            else:
                gt_track_pids[g["track_id"]].append(None)
            for item in ITEMS:
                if p is None:
                    pred = "not_detected"
                elif not p["decided"]:
                    pred = "undecided"
                else:
                    pred = "violation" if p[f"{item}_viol"] else "ok"
                row = {"gt": g[item], "pred": pred}
                rows[item].append(row)
                rows_by[("visibility", g.get("visibility"))][item].append(row)
                rows_by[("height", _height_bin(g["box"]))][item].append(row)
                if g[item] == "present" and g.get(f"{item}_box") and p is not None:
                    pb = p[f"{item}_box"]
                    if pb is None:
                        assoc[item]["no_item_matched"] += 1
                    elif box_iou(pb, g[f"{item}_box"]) >= iou_item:
                        assoc[item]["correct_item"] += 1
                    else:
                        assoc[item]["wrong_item"] += 1
        for pi in fp_idx:
            for item in ITEMS:
                p = preds[pi]
                rows[item].append({"gt": None, "pred": "violation" if p["decided"] and p[f"{item}_viol"] else "ok"})
    pid_to_track = {k: c.most_common(1)[0][0] for k, c in votes.items()}
    frag = {tid: len({p for p in pids if p is not None}) for tid, pids in gt_track_pids.items()}
    switches = 0
    for pids in gt_track_pids.values():
        seq = [p for p in pids if p is not None]
        switches += sum(1 for a, b in zip(seq, seq[1:]) if a != b)
    merges = sum(1 for c in votes.values() if len(c) > 1)
    pred_inc = incidents_from_events(run["events"], video_id=vid, unclosed=run["unclosed"])
    video_end = run["video_sec"]
    inc = match_incidents(ground_truth_incidents(doc), pred_inc, video_end=video_end, tol_start=tol_start,
                          tol_end=tol_end, pid_to_track=pid_to_track)
    inc["false_alarms_per_camera_hour"] = false_alarms_per_camera_hour(inc["false_incidents"], video_end)
    return {"video_id": vid, "camera_id": doc["video"]["camera_id"], "split": doc["video"]["split"],
            "conditions": doc["video"].get("conditions", []), "gt_frames": len(gt_frames),
            "gt_frames_not_processed": not_processed,
            "person_frame": {i: person_frame_confusion(rows[i], i) for i in ITEMS},
            "person_frame_rows": {i: rows[i] for i in ITEMS},
            "breakdown": {f"{k[0]}={k[1]}": {i: person_frame_confusion(v[i], i) for i in ITEMS}
                          for k, v in rows_by.items()},
            "association": {i: dict(assoc[i]) for i in ITEMS},
            "tracking": {"gt_tracks": len(gt_track_pids), "fragmentation_extra_ids": sum(max(0, n - 1) for n in
                                                                                         frag.values()),
                         "id_switches": switches, "pred_ids_covering_multiple_gt": merges,
                         "gt_tracks_never_detected": sum(1 for n in frag.values() if n == 0)},
            "incidents": inc, "pred_incidents": pred_inc, "observed_sec": video_end}


def aggregate(results: list[dict]) -> dict:
    """Video sonuçlarını toplar (sayaçlar toplanır, oranlar yeniden hesaplanır). Koşul kırılımı video etiketlerinden."""
    def pool(rs):
        out = {}
        for item in ITEMS:
            rows = [r for x in rs for r in x["person_frame_rows"][item]]
            out[item] = person_frame_confusion(rows, item)
        gt_n = sum(x["incidents"]["gt_incidents"] for x in rs)
        det = sum(x["incidents"]["detected"] for x in rs)
        npred = sum(x["incidents"]["pred_incidents"] for x in rs)
        false = sum(x["incidents"]["false_incidents"] for x in rs)
        sec = sum(x["observed_sec"] for x in rs)
        inc = {"detected": det, "missed": gt_n - det, "false_incidents": false,
               "recall": safe_div(det, gt_n), "recall_ci95": wilson(det, gt_n),
               "precision": safe_div(npred - false, npred), "precision_ci95": wilson(npred - false, npred)}
        inc.update(gt_incidents=gt_n, pred_incidents=npred,
                   duplicates=sum(x["incidents"]["duplicates"] for x in rs),
                   false_alarms_per_camera_hour=false_alarms_per_camera_hour(false, sec), observed_hours=sec / 3600)
        return {"videos": len(rs), "person_frame": out, "incidents": inc}
    by_cond = defaultdict(list)
    for r in results:
        for c in r["conditions"] or ["(etiketsiz koşul)"]:
            by_cond[c].append(r)
    by_cam = defaultdict(list)
    for r in results:
        by_cam[r["camera_id"]].append(r)
    return {"overall": pool(results), "by_condition": {c: pool(v) for c, v in sorted(by_cond.items())},
            "by_camera": {c: pool(v) for c, v in sorted(by_cam.items())}}
