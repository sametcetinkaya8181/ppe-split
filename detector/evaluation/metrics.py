"""Saf metrik hesapları (numpy/torch gerektirmez). Sıfır payda -> None (0 veya 1 uydurulmaz).

Üç ayrı düzey ASLA birleştirilmez:
- nesne (kutu) düzeyi: detection_counts / prf
- kişi-kare düzeyi: person_frame_confusion (aynı kişinin ardışık kareleri bağımsız örnek DEĞİLDİR; güven aralığı
  yalnız gösterge amaçlıdır, etkin örneklem kişi/incident sayısıdır)
- incident düzeyi: match_incidents"""
import math

STATES = ("present", "missing", "unknown")


def safe_div(n, d):
    return None if not d else n / d


def prf(tp: int, fp: int, fn: int, tn: int | None = None) -> dict:
    p, r = safe_div(tp, tp + fp), safe_div(tp, tp + fn)
    f1 = None if p is None or r is None or (p + r) == 0 else 2 * p * r / (p + r)
    out = {"tp": tp, "fp": fp, "fn": fn, "precision": p, "recall": r, "f1": f1,
           "fnr": safe_div(fn, tp + fn), "precision_ci95": wilson(tp, tp + fp), "recall_ci95": wilson(tp, tp + fn)}
    if tn is not None:
        out.update(tn=tn, specificity=safe_div(tn, tn + fp), fpr=safe_div(fp, tn + fp))
    return out


def wilson(k: int, n: int, z: float = 1.96):
    """Oran için Wilson %95 aralığı; n=0 -> None."""
    if n <= 0:
        return None
    ph = k / n
    den = 1 + z * z / n
    c = (ph + z * z / (2 * n)) / den
    h = z * math.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n)) / den
    return [max(0.0, c - h), min(1.0, c + h)]


def percentile(values, q: float):
    """Doğrusal enterpolasyonlu yüzdelik; boş -> None."""
    v = sorted(x for x in values if x is not None)
    if not v:
        return None
    k = (len(v) - 1) * q / 100.0
    lo, hi = math.floor(k), math.ceil(k)
    return v[lo] if lo == hi else v[lo] + (v[hi] - v[lo]) * (k - lo)


def summary_stats(values) -> dict:
    v = [x for x in values if x is not None]
    return {"n": len(v), "mean": (sum(v) / len(v)) if v else None, "p50": percentile(v, 50),
            "p95": percentile(v, 95), "max": max(v) if v else None}


# ---------------------------------------------------------------- kutular
def valid_box(b) -> bool:
    return (len(b) == 4 and all(isinstance(x, (int, float)) and math.isfinite(x) for x in b)
            and b[2] > b[0] and b[3] > b[1])


def box_iou(a, b) -> float:
    ix1, iy1, ix2, iy2 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    if inter <= 0:
        return 0.0
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def match_boxes(gts, preds, iou_thr: float = 0.5):
    """Açgözlü eşleme (COCO tarzı): tahminler güven sırasına göre, her GT en fazla bir kez.
    gts: [box], preds: [(box, conf)]. Döner: (eşleşmeler [(pred_i, gt_i, iou)], fp pred indeksleri, fn gt indeksleri)."""
    order = sorted(range(len(preds)), key=lambda i: -preds[i][1])
    used, matches, fps = set(), [], []
    for pi in order:
        best, bi = iou_thr, None
        for gi, g in enumerate(gts):
            if gi in used:
                continue
            iou = box_iou(preds[pi][0], g)
            if iou >= best:
                best, bi = iou, gi
        if bi is None:
            fps.append(pi)
        else:
            used.add(bi)
            matches.append((pi, bi, best))
    return matches, fps, [gi for gi in range(len(gts)) if gi not in used]


def detection_counts(images, classes, iou_thr: float = 0.5, conf_thr: float = 0.25) -> dict:
    """images: [{"gt": {cls: [box]}, "pred": {cls: [(box, conf)]}}]. Sınıf başına tp/fp/fn + prf."""
    out = {}
    for c in classes:
        tp = fp = fn = 0
        for im in images:
            preds = [p for p in im.get("pred", {}).get(c, []) if p[1] >= conf_thr]
            m, f, n = match_boxes(im.get("gt", {}).get(c, []), preds, iou_thr)
            tp, fp, fn = tp + len(m), fp + len(f), fn + len(n)
        out[c] = prf(tp, fp, fn)
        out[c]["gt_instances"] = tp + fn
    return out


# ---------------------------------------------------------------- kişi-kare düzeyi
def person_frame_confusion(rows, item: str) -> dict:
    """rows: [{"gt": present|missing|unknown, "pred": "violation"|"ok"|"undecided"|"not_detected"}], yalnız GT kişiler
    için; ayrıca GT'de karşılığı olmayan tahmin kişileri {"gt": None, "pred": ...}.
    Pozitif sınıf = ekipman EKSİK (ihlal).
      GT missing + violation -> TP; + ok -> FN; + undecided/not_detected -> FN (ayrı sayaçla)
      GT present + violation -> FP; + ok -> TN; + undecided/not_detected -> abstain (TN sayılmaz)
      GT unknown -> değerlendirme dışı (violation ise ayrıca sayılır; FP sayılmaz)
      GT yok (fazla kişi tespiti) + violation -> FP (fp_unmatched)"""
    c = {k: 0 for k in ("tp", "fp", "fn", "tn", "fn_undecided", "fn_not_detected", "abstain_present",
                        "excluded_unknown", "violation_on_unknown", "fp_unmatched")}
    for r in rows:
        gt, pred = r["gt"], r["pred"]
        if gt is None:
            if pred == "violation":
                c["fp"] += 1
                c["fp_unmatched"] += 1
            continue
        if gt not in STATES:
            raise ValueError(f"geçersiz GT durumu: {gt!r}")
        if gt == "unknown":
            c["excluded_unknown"] += 1
            c["violation_on_unknown"] += pred == "violation"
        elif gt == "missing":
            if pred == "violation":
                c["tp"] += 1
            else:
                c["fn"] += 1
                if pred == "undecided":
                    c["fn_undecided"] += 1
                elif pred == "not_detected":
                    c["fn_not_detected"] += 1
        else:
            if pred == "violation":
                c["fp"] += 1
            elif pred == "ok":
                c["tn"] += 1
            else:
                c["abstain_present"] += 1
    out = prf(c["tp"], c["fp"], c["fn"], c["tn"])
    out.update({k: v for k, v in c.items() if k not in out})
    out["item"] = item
    out["note"] = "kişi-kare örnekleri bağımsız değildir; aralık yalnız gösterge"
    return out


# ---------------------------------------------------------------- incident düzeyi
def _overlap(a0, a1, b0, b1) -> bool:
    return a0 <= b1 and b0 <= a1


def match_incidents(gt, pred, *, video_end: float, tol_start: float = 1.0, tol_end: float = 2.0,
                    pid_to_track: dict | None = None) -> dict:
    """gt: [{"incident_id", "video_id", "track_id"?, "item", "start", "end"|None}]
    pred: [{"incident_id", "video_id", "pid"?, "items": set|list, "start", "end"|None, "closed_by": "end"|"stale"|
            "run_end"|None}]
    Eşleşme: aynı video, ortak ekipman, zaman örtüşmesi (GT aralığı tol_start önce / tol_end sonra genişletilir) ve
    pid_to_track verilmişse tahmin kişisinin baskın GT izi == GT track_id. Aynı GT'ye ikinci+ tahmin = duplicate
    (yanlış alarm sayılmaz, ayrı raporlanır). Hiçbir GT'ye uymayan tahmin = yanlış incident."""
    gt_sorted = sorted(gt, key=lambda g: (g["video_id"], g["start"]))
    matched: dict[str, list] = {g["incident_id"]: [] for g in gt_sorted}
    false = []
    for p in sorted(pred, key=lambda p: (p["video_id"], p["start"])):
        p_end = p["end"] if p.get("end") is not None else video_end
        cands = []
        for g in gt_sorted:
            if g["video_id"] != p["video_id"] or g["item"] not in set(p["items"]):
                continue
            g_end = g["end"] if g.get("end") is not None else video_end
            if not _overlap(p["start"], p_end, g["start"] - tol_start, g_end + tol_end):
                continue
            if pid_to_track is not None and g.get("track_id") is not None:
                if pid_to_track.get((p["video_id"], p.get("pid"))) != g["track_id"]:
                    continue
            cands.append((abs(p["start"] - g["start"]), g["incident_id"]))
        if cands:
            matched[min(cands)[1]].append(p)
        else:
            false.append(p)
    latencies, end_errors, duplicates, missed = [], [], 0, []
    by_id = {g["incident_id"]: g for g in gt_sorted}
    for gid, ps in matched.items():
        g = by_id[gid]
        if not ps:
            missed.append(gid)
            continue
        duplicates += len(ps) - 1
        first = min(ps, key=lambda p: p["start"])
        latencies.append(first["start"] - g["start"])
        last = max(ps, key=lambda p: p["start"])
        if g.get("end") is not None and last.get("closed_by") == "end" and last.get("end") is not None:
            end_errors.append(last["end"] - g["end"])
    n_gt, n_pred = len(gt_sorted), len(pred)
    detected = n_gt - len(missed)
    unclosed = sum(1 for p in pred if p.get("closed_by") in ("stale", "run_end", None))
    return {"gt_incidents": n_gt, "pred_incidents": n_pred, "detected": detected, "missed": len(missed),
            "missed_ids": missed, "false_incidents": len(false), "duplicates": duplicates,
            "recall": safe_div(detected, n_gt), "recall_ci95": wilson(detected, n_gt),
            "precision": safe_div(n_pred - len(false), n_pred), "precision_ci95": wilson(n_pred - len(false), n_pred),
            "start_latency_sec": summary_stats(latencies), "end_error_sec": summary_stats(end_errors),
            "unclosed_pred_incidents": unclosed, "tol_start": tol_start, "tol_end": tol_end}


def false_alarms_per_camera_hour(false_incidents: int, observed_seconds: float):
    return None if observed_seconds <= 0 else false_incidents / (observed_seconds / 3600.0)


def incidents_from_events(events, *, video_id: str, unclosed=()) -> list[dict]:
    """Üretim olay akışından (enrich edilmiş start/ongoing/end + IncidentTracker.unclosed) tahmin incident'ları.
    Orphan ongoing/end kendi başına incident açmaz (sayılır, raporda ayrı)."""
    inc = {}
    for e in events:
        iid = e["incident_id"]
        if e["type"] == "start":
            inc[iid] = {"incident_id": iid, "video_id": video_id, "pid": int(e["pid"]), "start": float(e["t"]),
                        "end": None, "closed_by": None, "items": set()}
        cur = inc.get(iid)
        if cur is None or e.get("orphan"):
            continue
        if e.get("missing_helmet"):
            cur["items"].add("helmet")
        if e.get("missing_vest"):
            cur["items"].add("vest")
        if e["type"] == "end":
            cur["end"], cur["closed_by"] = float(e["t"]), "end"
    for u in unclosed:
        cur = inc.get(u["incident_id"])
        if cur is not None and cur["closed_by"] is None:
            cur["closed_by"] = u["reason"]
            cur["end"] = u.get("t")
    out = list(inc.values())
    for p in out:
        p["items"] = sorted(p["items"])
    return out
