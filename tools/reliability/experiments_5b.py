"""FAZ 10-A.5B kontrollü ML ve karar deneyleri (üretim modeli, eşikler, PPEMonitor, IncidentTracker, tracker
yapılandırması ve dashboard DEĞİŞMEZ; tüm varyantlar ayrı, isimli ve çevrimdışıdır).

  python tools/reliability/experiments_5b.py cache                     # GPU: tespit önbelleği (+kırpıntı doğrulama,
                                                                       #      sentetik bozulmalar)
  python tools/reliability/experiments_5b.py run <önbellek klasörü>    # çevrimdışı deneyler -> results.json, RESULTS.md

Önbellek: V1-V3 kare başına TÜM sınıflar conf >= 0.05 (tam hassasiyet; pipeline.run_video ile aynı kare okuma:
grab x stride + retrieve), Person >= 0.25 kutuları için ikinci aşama kırpıntı doğrulaması (ayrı model örneği, aynı
best.pt) ve sentetik bozulmalar (küçültme = uzak çalışan vekili, karartma = düşük ışık vekili). Bozulmalar YALNIZ
yazılım/yöntem duyarlılığı içindir; saha doğruluğu değildir.

Çalıştırma: ByteTrack önbellekten yeniden oynatılır (üretim girdisi FAZ 10-A.5A izini birebir vermelidir), karar
katmanı evaluation.experiments.ExperimentalMonitor ile (varsayılan = PPEMonitor, birebir). GT yok -> doğruluk yok."""
import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DET = ROOT / "detector"
sys.path.insert(0, str(DET))
sys.path.insert(0, str(HERE))

VIDEOS = {"V1": DET / "videos" / "16533594_1920_1080_25fps.mp4",
          "V2": DET / "videos" / "5434223-hd_1920_1080_24fps.mp4",
          "V3": DET / "videos" / "8965526-uhd_2160_3840_25fps.mp4"}
FA_DEFAULT = HERE / "output" / "20261102T181114Z_failure_analysis"
CACHE_CONF = 0.05
CROP_PAD = 0.25
DEGRADATIONS = {"orig": None, "scale0.50": ("scale", 0.50), "scale0.33": ("scale", 0.33), "dark0.35": ("dark", 0.35)}
TRACK_BUFFER_CALLS = 30


def log(msg):
    from common import utc
    print(f"[{utc()}] {msg}", flush=True)


# ---------------------------------------------------------------- önbellek (GPU)
def _degrade(frame, spec):
    import cv2
    import numpy as np
    if spec is None:
        return frame
    kind, v = spec
    if kind == "dark":
        return cv2.convertScaleAbs(frame, alpha=v, beta=0)
    H, W = frame.shape[:2]
    small = cv2.resize(frame, (int(W * v), int(H * v)), interpolation=cv2.INTER_AREA)
    canvas = np.full_like(frame, 114)
    y0, x0 = (H - small.shape[0]) // 2, (W - small.shape[1]) // 2
    canvas[y0:y0 + small.shape[0], x0:x0 + small.shape[1]] = small
    return canvas


def _crop_conf(verifier, img, box, settings) -> float:
    """Kişi kutusunu %25 bağlamla kırp, aynı modelle yeniden tahmin et; kırpıntıdaki kutuyla IoU >= 0.5 en yüksek
    Person güveni (yoksa 0). Aynı ağırlıklar: bağımsız bir doğrulayıcı DEĞİL, yalnız çözünürlük etkisi."""
    from evaluation.metrics import box_iou
    H, W = img.shape[:2]
    x1, y1, x2, y2 = box
    pw, ph = (x2 - x1) * CROP_PAD, (y2 - y1) * CROP_PAD
    cx1, cy1 = int(max(0, x1 - pw)), int(max(0, y1 - ph))
    cx2, cy2 = int(min(W, x2 + pw)), int(min(H, y2 + ph))
    if cx2 - cx1 < 16 or cy2 - cy1 < 16:
        return 0.0
    r = verifier.predict(img[cy1:cy2, cx1:cx2], conf=CACHE_CONF, imgsz=settings["imgsz"], device=settings["device"],
                         classes=[6], verbose=False)[0]
    ref = [x1 - cx1, y1 - cy1, x2 - cx1, y2 - cy1]
    best = 0.0
    for b, s in zip(r.boxes.xyxy.cpu().numpy().tolist(), r.boxes.conf.cpu().numpy().tolist()):
        if box_iou(ref, b) >= 0.5:
            best = max(best, float(s))
    return best


def cache_video(model, verifier, path: Path, settings: dict, spec) -> dict:
    import cv2
    import numpy as np
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    if not fps or fps != fps or fps > 120:
        fps = 25.0
    W, H = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    stride = int(settings["vid_stride"])
    dets, off, ts, crops = [], [0], [], []
    idx = 0
    t0 = time.perf_counter()
    while True:
        ok = all(cap.grab() for _ in range(stride))
        if not ok:
            break
        ok, frame = cap.retrieve()
        if not ok or frame is None:
            break
        idx += 1
        img = _degrade(frame, spec)
        r = model.predict(img, conf=CACHE_CONF, imgsz=settings["imgsz"], device=settings["device"], verbose=False)[0]
        d = r.boxes.data.cpu().numpy().astype(np.float32).reshape(-1, 6)        # x1 y1 x2 y2 conf cls (NMS sırası)
        c = np.full(len(d), np.nan, dtype=np.float32)
        for j in range(len(d)):
            if int(d[j, 5]) == 6 and d[j, 4] >= 0.25:
                c[j] = _crop_conf(verifier, img, d[j, :4].tolist(), settings)
        dets.append(d)
        crops.append(c)
        off.append(off[-1] + len(d))
        ts.append(idx * stride / fps)                   # detector._process ile aynı zaman formülü
    cap.release()
    return {"dets": np.concatenate(dets) if dets else np.zeros((0, 6), np.float32), "off": np.asarray(off),
            "t": np.asarray(ts), "crop": np.concatenate(crops) if crops else np.zeros(0, np.float32),
            "meta": {"fps": fps, "width": W, "height": H, "stride": stride, "frames": idx,
                     "sec": round(time.perf_counter() - t0, 1)}}


def cmd_cache(a) -> int:
    import numpy as np
    from common import stamp
    from ultralytics import YOLO
    from evaluation import cli as ev
    from evaluation.isolation import SECRET_KEYS
    import os
    settings = {**ev.production_settings(), "vid_stride": a.stride}
    leaked = [k for k in SECRET_KEYS if k in os.environ]
    if leaked:                                          # isolation.scrub_process_env çalışmadıysa durdur
        log(f"BLOCKED: değerlendirme ortamında gizli anahtar: {leaked}")
        return 4
    env = ev.environment(settings)
    out = HERE / "output" / f"{stamp()}_experiments_5b_cache"
    out.mkdir(parents=True)
    model, verifier = YOLO(settings["model_path"]), YOLO(settings["model_path"])
    manifest = {"schema": "ppe-exp5b-cache/1", "settings": settings, "cache_conf": CACHE_CONF, "crop_pad": CROP_PAD,
                "degradations": DEGRADATIONS, "model_sha256": env["checkpoint"]["sha256"],
                "tracker_config": env["tracker_config"], "decision_parameters": env["decision_parameters"],
                "git_head": env["git_head"], "eval_env_secret_keys_present": leaked,
                "versions": {k: env[k] for k in ("python", "torch", "cuda", "gpu", "ultralytics", "opencv")},
                "videos": {}}
    for key, path in VIDEOS.items():
        manifest["videos"][key] = {"file": path.name, "variants": {}}
        for dname, spec in DEGRADATIONS.items():
            if dname != "orig" and key not in a.degrade_videos:
                continue
            log(f"önbellek {key} {dname}")
            r = cache_video(model, verifier, path, settings, spec)
            np.savez_compressed(out / f"cache_{key}_{dname}.npz", dets=r["dets"], off=r["off"], t=r["t"],
                                crop=r["crop"])
            manifest["videos"][key]["variants"][dname] = r["meta"]
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1, default=str),
                                       encoding="utf-8")
    log(f"bitti: {out}")
    return 0


# ---------------------------------------------------------------- deney tanımları
H1 = {"helmet_policy": "conflict_aware"}
H1B = {"helmet_occlusion_gate": True}
A3 = {"edge_policy": "item_specific"}
A4 = {"handoff": {"max_gap_sec": 2.5, "max_center_dist_h": 1.0}}
DECISION_VARIANTS = {                                   # tek değişken (taban: üretim karar mantığı, T0 takip)
    "E01_H1_conflict_aware": ("E-01", H1),
    "E01_H1_floor0.05": ("E-01", {**H1, "evidence_floor": 0.05}),
    "E01_H1b_head_occlusion_gate": ("E-01", H1B),
    "E01_H2_verified_absence": ("E-01", {"helmet_policy": "verified_absence"}),
    "E01_H5_graded_evidence": ("E-01", {"helmet_policy": "graded"}),
    "E01_H6_helmet_min_obs16": ("E-01", {"helmet_min_obs": 16}),
    "E01_H6_helmet_min_obs24": ("E-01", {"helmet_min_obs": 24}),
    "E02_P1_median0.45": ("E-02", {"track_gate": {"kind": "rolling_median", "tau": 0.45}}),
    "E02_P1_median0.50": ("E-02", {"track_gate": {"kind": "rolling_median", "tau": 0.50}}),
    "E02_P1_median0.55": ("E-02", {"track_gate": {"kind": "rolling_median", "tau": 0.55}}),
    "E02_P2_gated_ratio0.6": ("E-02", {"track_gate": {"kind": "gated_ratio", "rho": 0.6}}),
    "E02_P3_min_span4s": ("E-02", {"params": {"MIN_SPAN_SEC": 4.0}}),
    "E02_P4_aspect0.55": ("E-02", {"track_gate": {"kind": "aspect", "max_aspect": 0.55}}),
    "E02_P5_crop_verify0.5": ("E-02", {"crop_verify": {"tau": 0.5}}),
    "E02_P5_crop_verify0.6": ("E-02", {"crop_verify": {"tau": 0.6}}),
    "E02_ref_person_conf_min0.50": ("E-02", {"params": {"PERSON_CONF_MIN": 0.50}}),
    "E03_A3_item_specific_edge": ("E-03", A3),
    "E04_A4_handoff": ("E-04", A4),
}
COMBINED_VARIANTS = {                                   # birleşik (ayrı değerlendirilir)
    "C1_H1+H1b": {**H1, **H1B},
    "C2_A3+H1+H1b": {**A3, **H1, **H1B},
    "C3_A3+H5": {**A3, "helmet_policy": "graded"},
    "C4_A3+H6_16": {**A3, "helmet_min_obs": 16},
    "C5_P5crop0.5+A4": {"crop_verify": {"tau": 0.5}, **A4},
}
TRACKER_VARIANTS = {                                    # (track_offline kwargs, karar eşya kaynağı)
    "T0_production": ({}, "tracked"),
    "T0b_items_raw_control": ({}, "raw"),
    "T1_person_only": ({"classes": {6}}, "raw"),
    "T2_low_score_stage_conf0.10": ({"conf_in": 0.10}, "tracked"),
    "T3_person_only+low_score": ({"classes": {6}, "conf_in": 0.10}, "raw"),
    "T4_track_buffer60": ({"track_buffer": 60}, "tracked"),
    "T5_match_thresh0.9": ({"match_thresh": 0.9}, "tracked"),
    # birleşik (ayrı değerlendirilir): Kalman kontrolü parçalanmaların IoU x skor < 0.2 kaynaklı olduğunu gösterdi
    "T6_person_only+match0.9": ({"classes": {6}, "match_thresh": 0.9}, "raw"),
    "T7_person_only+match0.9+low_score": ({"classes": {6}, "match_thresh": 0.9, "conf_in": 0.10}, "raw"),
}
A4_TRACKERS = ("T0_production", "T1_person_only", "T3_person_only+low_score", "T6_person_only+match0.9")
ROBUSTNESS_VARIANTS = ["E02_P1_median0.45", "E02_P1_median0.50", "E02_P1_median0.55", "E02_P2_gated_ratio0.6",
                       "E02_P4_aspect0.55", "E02_P5_crop_verify0.5", "E02_P5_crop_verify0.6",
                       "E02_ref_person_conf_min0.50", "E01_H1_conflict_aware", "E01_H6_helmet_min_obs16"]


# ---------------------------------------------------------------- çalıştırma (CPU)
def load_cache(d: Path, key: str, dname: str = "orig"):
    import numpy as np
    z = np.load(d / f"cache_{key}_{dname}.npz")
    dets, off, ts, crop = z["dets"], z["off"], z["t"], z["crop"]
    frames, crops = [], []
    for i in range(len(ts)):
        rows = dets[off[i]:off[i + 1]]
        frames.append({"t": float(ts[i]), "dets": [{"c": int(r[5]), "s": float(r[4]), "b": [float(x) for x in r[:4]]}
                                                   for r in rows]})
        cc = crop[off[i]:off[i + 1]]
        crops.append({j: float(cc[j]) for j in range(len(cc)) if cc[j] == cc[j]})
    return frames, crops


def _summ(res: dict, spans=None) -> dict:
    from evaluation import experiments as ex
    inc = ex.incidents(res)
    fc = res.get("frame_counts") or {}
    out = {"incidents": [{k: i[k] for k in ("pid", "start", "end", "close", "duration", "evidence")} for i in inc],
           "n_incidents": len(inc), "n_starts": sum(1 for e in res["events"] if e["type"] == "start"),
           "statuses": res["statuses"], "aliases": res.get("aliases") or [], "verify_rejects": res.get("verify_rejects")}
    if fc:
        out.update(helmet_gated=fc.get("helmet_gated", 0), vest_gated=fc.get("vest_gated", 0),
                   bottom_edge_only=fc.get("bottom_edge_only", 0), dropped_by_track_gate=fc.get("dropped_by_track_gate", 0))
    if spans is not None:
        ctl = [s for t, _, s in res["status_rows"] if not ex.interval_mask(t, spans)]
        out["control"] = {"person_frames": len(ctl), "IHLAL": ctl.count("IHLAL"), "BELIRSIZ": ctl.count("BELIRSIZ"),
                          "UYGUN": ctl.count("UYGUN"), "ANALIZ": ctl.count("ANALIZ")}
    return out


def _spans(res: dict, end_t: float) -> list:
    from evaluation import experiments as ex
    return [(i["start"], i["end"] if i["end"] is not None else end_t) for i in ex.incidents(res) if i["start"] is not None]


def run_video_experiments(cache: Path, key: str, meta: dict, fa_dir: Path) -> dict:
    from evaluation import experiments as ex
    from evaluation import failure as fa
    v = meta["videos"][key]["variants"]["orig"]
    wh = (v["width"], v["height"])
    buffer_sec = TRACK_BUFFER_CALLS * v["stride"] / v["fps"]
    frames, crops = load_cache(cache, key)
    times = [f["t"] for f in frames]
    out = {"frame_wh": wh, "frames": len(frames)}
    # 1. takip eşdeğerliği ve üretim olayları
    t0 = ex.track_offline(frames, wh=wh)
    fa_m = json.loads((fa_dir / "manifest.json").read_text(encoding="utf-8"))
    diag = [json.loads(x) for x in (fa_dir / f"diag_{key}.jsonl").read_text(encoding="utf-8").splitlines() if x]
    out["tracker_equivalence_vs_10A5A"] = ex.tracked_equivalence(t0["tracked"], diag)
    prod_events = fa_m["videos"][key]["events"]
    base_frames = ex.decision_frames(frames, t0["tracked"], items="tracked", crop=crops)
    base = ex.run_decision(base_frames, wh, production=True)
    default = ex.run_decision(base_frames, wh, {})
    out["baseline_matches_production_events"] = ex.event_key(base["events"]) == ex.event_key(prod_events)
    out["experimental_default_matches_production"] = ex.event_key(default["events"]) == ex.event_key(base["events"])
    spans = _spans(base, times[-1])
    out["baseline"] = _summ(default, spans)
    out["baseline_spans"] = spans
    # 2. karar varyantları (tek değişken) + birleşikler
    out["decision_variants"] = {n: {"error": e, "config": c, **_summ(ex.run_decision(base_frames, wh, c), spans)}
                                for n, (e, c) in DECISION_VARIANTS.items()}
    out["combined_variants"] = {n: {"config": c, **_summ(ex.run_decision(base_frames, wh, c), spans)}
                                for n, c in COMBINED_VARIANTS.items()}
    # 3. E-02 iz düzeyi güven kararlılığı (tüm kişi izleri)
    tracks = {}
    for fr in base_frames:
        for pid, box, conf in fr["persons"]:
            tracks.setdefault(int(pid), []).append((fr["t"], conf, (box[2] - box[0]) / max(1.0, box[3] - box[1]),
                                                    fr["crop"].get(int(pid))))
    out["person_track_stability"] = {pid: _track_stats(rows) for pid, rows in sorted(tracks.items())}
    # 4. takip varyantları (karar: üretim PPEMonitor; yalnız takip değişir)
    tv = {}
    for n, (kw, items) in TRACKER_VARIANTS.items():
        tr = ex.track_offline(frames, wh=wh, **kw)
        dfr = ex.decision_frames(frames, tr["tracked"], items=items, crop=crops)
        r = ex.run_decision(dfr, wh, production=True)
        tv[n] = {"config": {**{k: sorted(x) if isinstance(x, set) else x for k, x in kw.items()}, "items": items},
                 **_summ(r, spans),
                 "tracking": ex.tracking_summary(tr["tracked"], times, wh, buffer_sec),
                 "new_person_tracks": tr["new_tracks"]}
        if n in A4_TRACKERS:
            tv[n]["with_A4_handoff"] = _summ(ex.run_decision(dfr, wh, A4), spans)
    out["tracker_variants"] = tv
    out["fragment_kalman_check"] = _kalman_check(tv["T0_production"]["new_person_tracks"],
                                                 fa.fragment_candidates(fa.person_tracks(diag, wh),
                                                                        max_gap_sec=buffer_sec + 0.5,
                                                                        t0=diag[0]["t"]))
    return out


def _track_stats(rows) -> dict:
    from statistics import median
    confs = [c for _, c, _, _ in rows]
    roll = [median(confs[max(0, i - 24):i + 1]) for i in range(7, len(confs))]
    crops = [x for *_, x in rows if x is not None]
    return {"frames": len(rows), "first_t": round(rows[0][0], 2), "last_t": round(rows[-1][0], 2),
            "conf_median": round(median(confs), 3), "conf_min": round(min(confs), 3),
            "rolling_median_min": round(min(roll), 3) if roll else None,
            "rolling_median_max": round(max(roll), 3) if roll else None,
            "gated_conf_ratio": round(sum(c >= 0.40 for c in confs) / len(confs), 3),
            "aspect_median": round(median(a for _, _, a, _ in rows), 3),
            "crop_conf_median": round(median(crops), 3) if crops else None,
            "crop_conf_min": round(min(crops), 3) if crops else None}


def _kalman_check(new_tracks: list, frags: list) -> list:
    """FAZ 10-A.5A parçalanma adayları için: yeni iz doğduğunda kayıp izin Kalman tahmini kutusuyla IoU × güven
    (ByteTrack ilk aşama eşleşmesi için >= 0.2 gerekir; kayıp izler yalnız ilk aşamada aranır)."""
    by_pid = {n["pid"]: n for n in new_tracks}
    out = []
    for f in frags:
        n = by_pid.get(f["next_pid"])
        out.append({"prev_pid": f["prev_pid"], "next_pid": f["next_pid"], "gap_sec": f["gap_sec"],
                    "iou_last_box_x_conf_5A": f["iou_x_conf"],
                    "kalman_best_lost_pid": n and n["best_lost_pid"], "kalman_iou": n and n["best_lost_iou"],
                    "kalman_iou_x_conf": n and n["iou_x_conf"],
                    "kalman_says_matchable": bool(n and n["best_lost_pid"] == f["prev_pid"] and n["iou_x_conf"] >= 0.2)})
    return out


def run_robustness(cache: Path, key: str, meta: dict) -> dict:
    """Sentetik bozulmalarda (uzak/karanlık vekili) E-02/E-01 adaylarının kişi-kare kapsamını ne kadar azalttığı.
    V1/V2'deki kişiler AI gözlemine göre gerçek çalışanlardır (GT yok); V3'te makine izi de vardır."""
    from evaluation import experiments as ex
    out = {}
    for dname, m in meta["videos"][key]["variants"].items():
        if dname == "orig":
            continue
        wh = (m["width"], m["height"])
        frames, crops = load_cache(cache, key, dname)
        tr = ex.track_offline(frames, wh=wh)
        dfr = ex.decision_frames(frames, tr["tracked"], items="tracked", crop=crops)
        base = ex.run_decision(dfr, wh, {})
        row = {"baseline": _summ(base), "variants": {}}
        tracks = {}
        for fr in dfr:
            for pid, box, conf in fr["persons"]:
                tracks.setdefault(int(pid), []).append((fr["t"], conf, (box[2] - box[0]) / max(1.0, box[3] - box[1]),
                                                        fr["crop"].get(int(pid))))
        row["person_track_stability"] = {pid: _track_stats(r) for pid, r in sorted(tracks.items()) if len(r) >= 8}
        bg = base["frame_counts"]["helmet_gated"]
        for n in ROBUSTNESS_VARIANTS:
            r = ex.run_decision(dfr, wh, DECISION_VARIANTS[n][1])
            s = _summ(r)
            s["helmet_gated_retained_vs_baseline"] = round(s["helmet_gated"] / bg, 3) if bg else None
            s["decided_frames"] = sum(v for k, v in r["statuses"].items() if k != "ANALIZ")
            row["variants"][n] = s
        row["baseline_decided_frames"] = sum(v for k, v in base["statuses"].items() if k != "ANALIZ")
        out[dname] = row
    return out


def run_synthetic() -> dict:
    from evaluation import experiments as ex
    variants = {"BASE": None, **{n: c for n, (_, c) in DECISION_VARIANTS.items()}, **COMBINED_VARIANTS}
    rows = {}
    for sc in ex.synthetic_scenarios():
        rows[sc["name"]] = {"kind": sc["kind"], "desc": sc["desc"], "expect_incidents": sc["expect_incidents"],
                            "results": {n: {k: v for k, v in ex.scenario_outcome(sc, c or {}, production=c is None)
                                            .items() if k != "statuses"} for n, c in variants.items()}}
    summary = {}
    for n in variants:
        res = [(r["kind"], r["results"][n]) for r in rows.values()]
        summary[n] = {"must_alarm_missed": [s for s, r in rows.items() if r["kind"] == "must_alarm"
                                            and not r["results"][n]["ok"]],
                      "should_not_alarm_alarmed": [s for s, r in rows.items() if r["kind"] == "should_not_alarm"
                                                   and not r["results"][n]["ok"]],
                      "incident_count_wrong": [s for s, r in rows.items() if r["kind"] == "incident_count"
                                               and not r["results"][n]["ok"]],
                      "ok": sum(1 for _, r in res if r["ok"]), "total": len(res)}
    return {"scenarios": rows, "summary": summary}


def run_tracker_synthetic() -> dict:
    """Takip mantığı senaryoları (sentetik tespit dizileri; yalnız ByteTrack davranışı)."""
    from evaluation import experiments as ex
    out = {}
    for name, (frames, desc) in tracker_scenarios().items():
        out[name] = {"desc": desc}
        for vn, kw in (("T0_production", {}), ("T1_person_only", {"classes": {6}}),
                       ("T2_low_score_stage", {"conf_in": 0.10}),
                       ("T3_person_only+low_score", {"classes": {6}, "conf_in": 0.10}),
                       ("T6_person_only+match0.9", {"classes": {6}, "match_thresh": 0.9})):
            tr = ex.track_offline(frames, **kw)
            ids = [[d["id"] for d in f if d["c"] == 6] for f in tr["tracked"]]
            pids = sorted({i for f in ids for i in f if i is not None})
            other = sorted({d["c"] for f in tr["tracked"] for d in f if d["id"] in pids and d["c"] != 6})
            out[name][vn] = {"person_ids": pids, "person_id_used_by_classes": other}
            if any("gt" in d for f in frames for d in f["dets"]):
                out[name][vn].update(synthetic_id_metrics(frames, tr["tracked"]))
    return out


def tracker_scenarios() -> dict:
    """12 fps kişi tespit dizileri. Kutular sabit hızla sağa kayar."""
    def person(i, x0=600, dx=4):
        x = x0 + dx * i
        return [float(x), 200.0, float(x + 200), 700.0]

    def seq(n, fn):
        return [{"t": (i + 1) / 12, "dets": fn(i)} for i in range(n)]

    occl = seq(72, lambda i: [] if 30 <= i < 48 else [{"c": 6, "s": 0.85, "b": person(i)}])
    lowsc = seq(72, lambda i: [{"c": 6, "s": 0.15 if 30 <= i < 48 else 0.85, "b": person(i)}])
    hijack = seq(60, lambda i: [{"c": 6, "s": 0.85, "b": person(i)}] if i < 30 else
                 [{"c": 2, "s": 0.8, "b": [person(i)[0] + 10, 260.0, person(i)[2] - 10, 650.0]}] +
                 ([{"c": 6, "s": 0.85, "b": person(i)}] if i >= 34 else []))
    shape = seq(60, lambda i: [{"c": 6, "s": 0.72, "b": person(i) if i < 30 else
                                [person(i)[0] - 60, 330.0, person(i)[2] + 60, 700.0]}])
    def crossing(gap, speed=12, hide=None):
        """İki kişi karşılıklı yürüyüp kesişir; hide=(başla, bitir): öndeki kişi B bu karelerde tespit edilmiyor."""
        def fn(i):
            a = [400.0 + speed * i, 200.0, 600.0 + speed * i, 700.0]
            b = [1400.0 - speed * i, 220.0 + gap, 1600.0 - speed * i, 700.0 + gap]
            out = [{"c": 6, "s": 0.85, "b": a, "gt": "A"}]
            if not (hide and hide[0] <= i < hide[1]):
                out.append({"c": 6, "s": 0.8, "b": b, "gt": "B"})
            return out
        return seq(84, fn)

    return {"occlusion_1.5s_no_detection": (occl, "kişi 1.5 sn hiç tespit edilmiyor (tampon 30 çağrı içinde)"),
            "occlusion_1.5s_low_score_0.15": (lowsc, "örtülmede kişi yalnız 0.15 güvenle tespit ediliyor"),
            "vest_takes_over_box": (hijack, "kişi 4 kare kayboluyor, aynı yerde yelek kutusu var, kişi geri geliyor"),
            "abrupt_shape_change": (shape, "kişi eğiliyor: kutu en/boyu bir karede 0.4 -> 0.9"),
            "two_people_crossing": (crossing(0), "iki kişi aynı hizada kesişiyor (sentetik GT kimlikli)"),
            "two_people_crossing_slow": (crossing(10, speed=6), "iki kişi yavaş kesişiyor (uzun örtüşme)"),
            "crossing_with_occlusion": (crossing(0, hide=(36, 46)),
                                        "kesişmede B 10 kare görünmüyor, sonra yeniden beliriyor")}


def synthetic_id_metrics(frames, tracked) -> dict:
    """YALNIZ sentetik GT kimlikleri için: GT kişi başına farklı tahmin kimliği sayısı ve kimlik değişimi."""
    from evaluation.metrics import box_iou
    seqs = {}
    for fr, tr in zip(frames, tracked):
        for d in fr["dets"]:
            if "gt" not in d:
                continue
            best = max(((box_iou(d["b"], x["b"]), x["id"]) for x in tr if x["c"] == 6 and x["id"] is not None),
                       default=(0.0, None))
            seqs.setdefault(d["gt"], []).append(best[1] if best[0] >= 0.5 else None)
    seen = {g: [x for x in s if x is not None] for g, s in seqs.items()}
    ids = {g: sorted(set(s)) for g, s in seen.items()}
    return {"pred_ids_per_gt": ids,
            "id_switches": sum(sum(1 for a, b in zip(s, s[1:]) if a != b) for s in seen.values()),
            "ids_shared_between_gt": len(set.intersection(*(set(v) for v in ids.values()))) if len(ids) > 1 else 0}


def cmd_run(a) -> int:
    from common import stamp
    cache = Path(a.cache).resolve()
    meta = json.loads((cache / "manifest.json").read_text(encoding="utf-8"))
    out = HERE / "output" / f"{stamp()}_experiments_5b"
    out.mkdir(parents=True)
    res = {"schema": "ppe-exp5b/1", "cache": cache.name, "cache_manifest": {k: meta[k] for k in (
        "settings", "model_sha256", "git_head", "versions", "cache_conf", "eval_env_secret_keys_present")},
           "variants": {"decision": {n: {"error": e, "config": c} for n, (e, c) in DECISION_VARIANTS.items()},
                        "combined": COMBINED_VARIANTS,
                        "tracker": {n: {"kwargs": {k: sorted(x) if isinstance(x, set) else x for k, x in kw.items()},
                                        "items": it} for n, (kw, it) in TRACKER_VARIANTS.items()}},
           "videos": {}, "robustness": {}}
    for key in meta["videos"]:
        log(f"deneyler {key}")
        res["videos"][key] = run_video_experiments(cache, key, meta, Path(a.fa).resolve())
        if len(meta["videos"][key]["variants"]) > 1:
            res["robustness"][key] = run_robustness(cache, key, meta)
    log("sentetik senaryolar")
    res["synthetic"] = run_synthetic()
    res["tracker_synthetic"] = run_tracker_synthetic()
    (out / "results.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (out / "RESULTS.md").write_text(render(res), encoding="utf-8")
    log(f"bitti: {out}")
    return 0


def _inc(s) -> str:
    return "; ".join(f"{i['pid']}@{i['start']}-{i['end']}({i['close']})" for i in s["incidents"]) or "—"


def render(res: dict) -> str:
    L = ["# FAZ 10-A.5B deney sonuçları (otomatik)", "",
         "GT yok: yalnız taban ↔ aday farkı. Hiçbir satır doğruluk/precision/recall değildir.", ""]
    for key, v in res["videos"].items():
        L += [f"## {key}", "", f"- takip eşdeğerliği (10-A.5A): {json.dumps(v['tracker_equivalence_vs_10A5A'])}",
              f"- taban = üretim olayları: {v['baseline_matches_production_events']}; deney monitörü varsayılanı = "
              f"üretim: {v['experimental_default_matches_production']}", f"- taban incident: {_inc(v['baseline'])}", "",
              "| varyant | incident | baret kapısı | IHLAL/BELIRSIZ/UYGUN/ANALIZ | kontrol IHLAL/BELIRSIZ |",
              "|---|---|---|---|---|"]
        rows = [("BASE", v["baseline"])] + list(v["decision_variants"].items()) + list(v["combined_variants"].items())
        for n, s in rows:
            st = s["statuses"]
            L.append(f"| {n} | {_inc(s)} | {s.get('helmet_gated')} | {st.get('IHLAL', 0)}/{st.get('BELIRSIZ', 0)}/"
                     f"{st.get('UYGUN', 0)}/{st.get('ANALIZ', 0)} | {s['control']['IHLAL']}/{s['control']['BELIRSIZ']} |")
        L += ["", "| takip varyantı | incident | kişi kimliği | başka sınıfla | parçalanma adayı | çift | en çok eşzamanlı |",
              "|---|---|---|---|---|---|---|"]
        for n, s in v["tracker_variants"].items():
            t = s["tracking"]
            L.append(f"| {n} | {_inc(s)} | {t['person_ids']} | {t['person_ids_with_other_classes']} | "
                     f"{t['fragment_pairs']} | {t['concurrent_duplicates']} | {t['max_concurrent_persons']} |")
        L += ["", f"Kalman kontrolü: {json.dumps(v['fragment_kalman_check'])}", ""]
    L += ["## Sentetik senaryolar (yazılım mantığı)", "", "| varyant | kaçırılan must_alarm | gereksiz alarm | incident sayısı yanlış |",
          "|---|---|---|---|"]
    for n, s in res["synthetic"]["summary"].items():
        L.append(f"| {n} | {s['must_alarm_missed']} | {s['should_not_alarm_alarmed']} | {s['incident_count_wrong']} |")
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("cache")
    c.add_argument("--stride", type=int, default=2)
    c.add_argument("--degrade-videos", nargs="*", default=["V1", "V2", "V3"])
    r = sub.add_parser("run")
    r.add_argument("cache")
    r.add_argument("--fa", default=str(FA_DEFAULT), help="FAZ 10-A.5A tanı koşusu (eşdeğerlik için)")
    a = ap.parse_args()
    sys.exit(cmd_cache(a) if a.cmd == "cache" else cmd_run(a))


if __name__ == "__main__":
    main()
