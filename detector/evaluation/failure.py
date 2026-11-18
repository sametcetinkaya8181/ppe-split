"""FAZ 10-A.5A hata analizi (saf fonksiyonlar, birim testli). Girdi: tools/reliability/failure_analysis.py'nin kare
başına tanı kaydı. Her kare:
  people  : pipeline.run_video kişi kaydı (üretim karar zinciri; kutu = tracker çıktısı) + kapı nedenleri
  raw     : YOLO ham tespitleri, tracker ÖNCESİ (NMS sonrası, conf >= CONF)            [{"c", "s", "b"}]
  tracked : tracker çıktısı, TÜM sınıflar + kimlik (Ultralytics ByteTrack sınıftan bağımsızdır) [{"id", "c", "s", "b"}]
  probe   : ayrı model örneğiyle düşük eşikli tahmin (yalnız tanı; üretimde yok)        [{"c", "s", "b"}]
Hiçbir fonksiyon doğruluk (precision/recall/yanlış alarm oranı) üretmez: ground-truth yok. "aday" ve "neden"
etiketleri sezgiseldir ve raporda öyle sunulur."""
from collections import Counter, defaultdict
from contextlib import contextmanager
from statistics import median

import ppe_core as core

from .metrics import box_iou

CLS = {"helmet": core.CLASS_HELMET, "vest": core.CLASS_VEST, "person": core.CLASS_PERSON, "no_helmet": 7}
REGION = {"helmet": core.head_region, "vest": core.torso_region}
ITEM_CAUSES = ("matched", "tracked_item_unassigned", "item_outside_region", "raw_item_dropped_by_tracker",
               "item_below_conf", "no_detection")


def _center_in(b, region) -> bool:
    return core.inside(core.center(b), region)


def best_in(dets, cls: int, region):
    """region içinde merkezi olan, cls sınıfındaki en yüksek güvenli tespit (yoksa None)."""
    c = [d for d in dets or () if d["c"] == cls and _center_in(d["b"], region)]
    return max(c, key=lambda d: d["s"], default=None)


# ---------------------------------------------------------------- E-01: eşya (baret/yelek) neden sınıflandırması
def item_cause(person: dict, fr: dict, item: str = "helmet") -> dict:
    """Değerlendirilen kişi-karede eşya kanıtının hangi katmanda kaybolduğu. Öncelik: karar zincirine yakın olandan
    uzağa (eşleşti -> tracker çıktısında var ama atanmadı -> kişi kutusunda ama bölge dışı -> ham tespitte var ama
    tracker düşürdü -> yalnız eşik altı -> hiç yok). no_helmet kanıtı ayrıca raporlanır (karara katılmaz)."""
    region = REGION[item](person["box"])
    cls = CLS[item]
    tracked_in = best_in(fr.get("tracked"), cls, region)
    raw_in = best_in(fr.get("raw"), cls, region)
    probe_in = best_in(fr.get("probe"), cls, region)
    if person.get(f"{item}_box") is not None:
        cause = "matched"
    elif tracked_in is not None:
        cause = "tracked_item_unassigned"
    elif best_in(fr.get("tracked"), cls, person["box"]) is not None:
        cause = "item_outside_region"
    elif raw_in is not None:
        cause = "raw_item_dropped_by_tracker"
    elif probe_in is not None:
        cause = "item_below_conf"
    else:
        cause = "no_detection"
    out = {"cause": cause, "raw_conf": raw_in and round(raw_in["s"], 3),
           "probe_conf": probe_in and round(probe_in["s"], 3)}
    if item == "helmet":
        head = core.head_region(person["box"])
        nh_raw, nh_probe = best_in(fr.get("raw"), CLS["no_helmet"], head), best_in(fr.get("probe"), CLS["no_helmet"],
                                                                                    head)
        out["no_helmet_raw_conf"] = nh_raw and round(nh_raw["s"], 3)
        out["no_helmet_probe_conf"] = nh_probe and round(nh_probe["s"], 3)
    return out


def item_cause_table(diag: list, item: str = "helmet", pid=None, gated_only: bool = True) -> dict:
    """Kişi-kare neden sayımı. gated_only: yalnız kapıdan geçen (karara giren) kişi-kareler."""
    causes, nh = Counter(), Counter()
    for fr in diag:
        for p in fr["people"]:
            if (pid is not None and p["pid"] != pid) or (gated_only and p.get("gate")):
                continue
            r = item_cause(p, fr, item)
            causes[r["cause"]] += 1
            if item == "helmet" and r["cause"] != "matched":
                nh["no_helmet_raw" if r["no_helmet_raw_conf"] else
                   "no_helmet_probe_only" if r["no_helmet_probe_conf"] else "no_helmet_absent"] += 1
    return {"person_frames": sum(causes.values()), "causes": {k: causes.get(k, 0) for k in ITEM_CAUSES},
            "unmatched_no_helmet_evidence": dict(nh)}


def item_series(diag: list, pid: int, item: str = "helmet") -> list:
    """Bir kişinin kare kare eşya kanıtı (rapor ve inceleme için)."""
    rows = []
    for fr in diag:
        for p in fr["people"]:
            if p["pid"] == pid:
                rows.append({"t": round(fr["t"], 2), "frame": fr["frame"], "conf": round(p["conf"], 3),
                             "gate": p.get("gate") or [], "status": p["status"],
                             "rate": round(p["h_rate" if item == "helmet" else "v_rate"], 2),
                             **item_cause(p, fr, item)})
    return rows


# ---------------------------------------------------------------- iz özellikleri (E-02, E-04)
def class_history(diag: list) -> dict:
    """tracker kimliği -> sınıf sayımı (tüm sınıflar aynı ByteTrack örneğinde)."""
    h = defaultdict(Counter)
    for fr in diag:
        for d in fr.get("tracked") or ():
            h[int(d["id"])][int(d["c"])] += 1
    return {k: dict(v) for k, v in sorted(h.items())}


def raw_support(box, raw, cls: int = CLS["person"], iou: float = 0.5) -> float | None:
    """Tracker kutusuyla örtüşen aynı sınıf ham tespitin güveni (yoksa None)."""
    best = None
    for d in raw or ():
        if d["c"] == cls and box_iou(box, d["b"]) >= iou and (best is None or d["s"] > best):
            best = d["s"]
    return best


def person_tracks(diag: list, frame_wh) -> dict:
    W, H = frame_wh
    acc = defaultdict(lambda: {"t": [], "conf": [], "h": [], "aspect": [], "gate": Counter(), "decided": 0,
                               "helmet": 0, "vest": 0, "raw_support": 0, "boxes": []})
    for fr in diag:
        for p in fr["people"]:
            a = acc[p["pid"]]
            x1, y1, x2, y2 = p["box"]
            a["t"].append(fr["t"]); a["conf"].append(p["conf"]); a["h"].append(y2 - y1)
            a["aspect"].append((x2 - x1) / max(1.0, y2 - y1)); a["boxes"].append(p["box"])
            for g in p.get("gate") or ["evaluated"]:
                a["gate"][g] += 1
            a["decided"] += bool(p["decided"])
            a["helmet"] += p.get("helmet_box") is not None
            a["vest"] += p.get("vest_box") is not None
            a["raw_support"] += raw_support(p["box"], fr.get("raw")) is not None
    out = {}
    for pid, a in sorted(acc.items()):
        n = len(a["t"])
        out[pid] = {"pid": pid, "frames": n, "first_t": round(a["t"][0], 2), "last_t": round(a["t"][-1], 2),
                    "duration_sec": round(a["t"][-1] - a["t"][0], 2),
                    "conf_median": round(median(a["conf"]), 3), "conf_min": round(min(a["conf"]), 3),
                    "conf_max": round(max(a["conf"]), 3), "height_px_median": round(median(a["h"]), 1),
                    "height_rel_median": round(median(a["h"]) / H, 3), "aspect_wh_median": round(median(a["aspect"]), 3),
                    "gate": dict(a["gate"]), "decided_frames": a["decided"],
                    "helmet_matched_ratio": round(a["helmet"] / n, 3), "vest_matched_ratio": round(a["vest"] / n, 3),
                    "raw_person_support_ratio": round(a["raw_support"] / n, 3), "first_conf": round(a["conf"][0], 3),
                    "first_box": [round(x, 1) for x in a["boxes"][0]], "last_box": [round(x, 1) for x in a["boxes"][-1]]}
    return out


def _cdist(a, b) -> float:
    (ax, ay), (bx, by) = core.center(a), core.center(b)
    return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5


def _h(b) -> float:
    return max(1.0, b[3] - b[1])


def touches_edge(b, frame_wh, margin=None) -> list:
    W, H = frame_wh
    m = core.EDGE_MARGIN_PX if margin is None else margin
    return [s for s, hit in (("left", b[0] <= m), ("top", b[1] <= m), ("right", b[2] >= W - m),
                             ("bottom", b[3] >= H - m)) if hit]


def track_losses(diag: list, frame_wh, buffer_sec: float) -> list:
    """Video bitmeden kaybolan her kişi izi için kayıp sonrası kanıt (buffer_sec penceresi, ByteTrack kayıp tamponu):
    - id_continued_as_other_class: aynı tracker kimliği başka sınıfla sürdü (sınıftan bağımsız eşleştirme)
    - raw_person_near_after_loss: son kutuyla örtüşen ham kişi tespiti vardı (tracker ilişkilendiremedi / başka
      kimliğe verdi) -> ilişkilendirme hatası adayı
    - otherwise: tespit kesintisi (örtülme/kadraj dışı/model kaçırması ayrılamaz; GT yok)
    Ek: son karede başka kişi kutusuyla en yüksek IoU (örtülme vekili), son kutunun kadraj kenarına teması."""
    pids_last = {}
    for i, fr in enumerate(diag):
        for p in fr["people"]:
            pids_last[p["pid"]] = (i, p["box"])
    end_t = diag[-1]["t"] if diag else 0.0
    out = []
    for pid, (i, box) in sorted(pids_last.items()):
        t_last = diag[i]["t"]
        if i == len(diag) - 1:
            continue
        others = [p["box"] for p in diag[i]["people"] if p["pid"] != pid]
        after = [fr for fr in diag[i + 1:] if fr["t"] - t_last <= buffer_sec]
        other_cls = Counter(int(d["c"]) for fr in after for d in fr.get("tracked") or () if int(d["id"]) == pid)
        raw_near = [fr for fr in after if any(d["c"] == CLS["person"] and box_iou(box, d["b"]) >= 0.3
                                              for d in fr.get("raw") or ())]
        taken_by = Counter(p["pid"] for fr in after for p in fr["people"] if p["pid"] != pid and box_iou(box, p["box"]) >= 0.3)
        kind = ("id_continued_as_other_class" if other_cls else
                "raw_person_near_after_loss" if raw_near else "detection_gap")
        out.append({"pid": pid, "last_t": round(t_last, 2), "video_end_t": round(end_t, 2), "kind": kind,
                    "other_class_frames": dict(other_cls), "raw_person_near_frames": len(raw_near),
                    "window_frames": len(after), "box_taken_by_pids": dict(taken_by),
                    "max_iou_other_person_at_loss": round(max((box_iou(box, o) for o in others), default=0.0), 3),
                    "edge_at_loss": touches_edge(box, frame_wh)})
    return out


def fragment_candidates(tracks: dict, *, max_gap_sec: float, max_center_dist_h: float = 1.0, t0: float = 0.0) -> list:
    """Biri bittikten sonra kısa sürede ve yakın konumda başlayan kişi izi çiftleri = AYNI KİŞİ ADAYI. GT olmadan
    kimlik değişimi KESİN ölçülemez; bu yalnız sezgisel bir aday listesidir."""
    out = []
    for b in sorted(tracks.values(), key=lambda x: x["first_t"]):
        if b["first_t"] <= t0 + 1e-6:
            continue
        for a in tracks.values():
            gap = b["first_t"] - a["last_t"]
            if a["pid"] == b["pid"] or gap <= 0 or gap > max_gap_sec:
                continue
            d = _cdist(a["last_box"], b["first_box"]) / max(_h(a["last_box"]), _h(b["first_box"]))
            if d <= max_center_dist_h:
                iou = box_iou(a["last_box"], b["first_box"])
                # ByteTrack fuse_score maliyeti: 1 - IoU*skor; match_thresh 0.8 -> IoU*skor >= 0.2 gerekir
                out.append({"prev_pid": a["pid"], "next_pid": b["pid"], "gap_sec": round(gap, 2),
                            "center_dist_rel_h": round(d, 2), "iou_last_first": round(iou, 3),
                            "next_first_conf": b["first_conf"], "iou_x_conf": round(iou * b["first_conf"], 3)})
    return out


def concurrent_duplicates(diag: list, contain: float = 0.8) -> dict:
    """Aynı karede iki kişi izi büyük ölçüde örtüşüyorsa (kesişim / küçük alan >= contain) çift tespit adayı."""
    pairs = Counter()
    for fr in diag:
        ps = fr["people"]
        for i in range(len(ps)):
            for j in range(i + 1, len(ps)):
                a, b = ps[i]["box"], ps[j]["box"]
                ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
                small = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
                if small > 0 and ix / small >= contain:
                    pairs[tuple(sorted((ps[i]["pid"], ps[j]["pid"])))] += 1
    return {f"{a}-{b}": n for (a, b), n in sorted(pairs.items())}


def max_concurrent(diag: list) -> int:
    return max((len(fr["people"]) for fr in diag), default=0)


# ---------------------------------------------------------------- E-03: kapsam
def coverage_breakdown(diag: list, frame_wh) -> dict:
    """Kişi-kare durumları: kapı nedeni birleşimleri, kenar tarafları, kapıdan geçip henüz karar verilmemiş (ısınma:
    MIN_OBS/MIN_SPAN) ve karar verilmiş kareler."""
    combos, sides, edge_only_sides = Counter(), Counter(), Counter()
    total = warm = decided_gated = 0
    for fr in diag:
        for p in fr["people"]:
            total += 1
            g = tuple(p.get("gate") or ())
            combos["+".join(g) or "evaluated"] += 1
            if "edge" in g:
                s = touches_edge(p["box"], frame_wh)
                for x in s:
                    sides[x] += 1
                if g == ("edge",):
                    edge_only_sides["+".join(s)] += 1
            if not g:
                if p["decided"]:
                    decided_gated += 1
                else:
                    warm += 1
    return {"person_frames": total, "by_gate": dict(combos), "edge_sides": dict(sides),
            "edge_only_by_sides": dict(edge_only_sides), "gated_in_warmup": warm, "gated_decided": decided_gated}


# ---------------------------------------------------------------- karar katmanı yeniden oynatma (yalnız ne-olurdu)
@contextmanager
def _params(**kw):
    old = {k: getattr(core, k) for k in kw}
    try:
        for k, v in kw.items():
            setattr(core, k, v)
        yield
    finally:
        for k, v in old.items():
            setattr(core, k, v)


def _clip_box(box, frame_wh, sides):
    W, H = frame_wh
    m = core.EDGE_MARGIN_PX + 1
    x1, y1, x2, y2 = box
    if "left" in sides:
        x1 = max(x1, m)
    if "top" in sides:
        y1 = max(y1, m)
    if "right" in sides:
        x2 = min(x2, W - m)
    if "bottom" in sides:
        y2 = min(y2, H - m)
    return [x1, y1, x2, y2]


def item_boxes_from(source: str, min_conf: float = 0.0):
    """Eşya kutusu kaynağı (ne-olurdu): 'raw' = tracker ÖNCESİ ham tespitler; 'raw+probe' = ham + eşik altı sonda."""
    def fn(fr, cls):
        dets = list(fr.get("raw") or ()) + (list(fr.get("probe") or ()) if source == "raw+probe" else [])
        return [d["b"] for d in dets if d["c"] == cls and d["s"] >= min_conf]
    return fn


def replay(diag: list, frame_wh, *, edge_exempt=(), items=None, **params) -> dict:
    """Tracker ve eşleştirme çıktısı SABİT tutularak yalnız PPEMonitor yeniden oynatılır. Değişiklik yoksa üretim
    olaylarını birebir vermelidir (doğrulama). edge_exempt: kenar kapısından muaf tutulan taraflar (kutu yalnız kapı
    için içeri kırpılır; eşleştirme izden gelir). items: eşya kutusu kaynağı (item_boxes_from); verilirse eşleştirme
    core.match_items ile yeniden yapılır. params: ppe_core sabitleri (geçici)."""
    with _params(**params):
        mon = core.PPEMonitor()
        events, gated, decided = [], 0, 0
        for fr in diag:
            persons = [(p["pid"], _clip_box(p["box"], frame_wh, edge_exempt) if edge_exempt else p["box"], p["conf"])
                       for p in fr["people"]]
            if items is None:
                hm = {p["pid"]: p["helmet_box"] for p in fr["people"] if p.get("helmet_box") is not None}
                vm = {p["pid"]: p["vest_box"] for p in fr["people"] if p.get("vest_box") is not None}
            else:
                real = [(p["pid"], p["box"], p["conf"]) for p in fr["people"]]
                hm = core.match_items(real, items(fr, CLS["helmet"]), core.head_region)
                vm = core.match_items(real, items(fr, CLS["vest"]), core.torso_region)
            st, ev = mon.update(fr["t"], frame_wh, persons, hm, vm)
            events += ev
            decided += sum(1 for pid, *_ in persons if mon.state.get(int(pid), {}).get("decided"))
            W, H = frame_wh
            gated += sum(1 for _, b, c in persons
                         if c >= core.PERSON_CONF_MIN and b[3] - b[1] >= core.MIN_PERSON_H_PX
                         and b[0] > core.EDGE_MARGIN_PX and b[1] > core.EDGE_MARGIN_PX
                         and b[2] < W - core.EDGE_MARGIN_PX and b[3] < H - core.EDGE_MARGIN_PX)
    return {"events": [{k: e[k] for k in ("type", "pid", "t", "missing")} for e in events],
            "starts": [(e["pid"], e["t"], e["missing"]) for e in events if e["type"] == "start"],
            "gated_person_frames": gated, "decided_person_frames": decided}


def event_key(events) -> list:
    return [(e["type"], int(e["pid"]), round(float(e["t"]), 1)) for e in events]
