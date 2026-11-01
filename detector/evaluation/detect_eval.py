"""Etiketli YOLO görüntü veri kümesinde nesne düzeyi değerlendirme.

1. Ultralytics model.val (mAP50, mAP50-95, sınıf AP, karışıklık matrisi, PR eğrileri) — conf=0.001 standart mAP.
2. Bağımsız çalışma noktası: model.predict üretim conf/imgsz ile; sınıf bazlı TP/FP/FN, P/R/F1, Wilson aralığı,
   güven dağılımı, FP/FN örnek listesi (yalnız dosya adı + kutu; görüntü dışa aktarılmaz).
3. Eşik taraması (yalnız val split; test split'te --allow-test-sweep olmadan reddedilir).
4. Tek kare kişi-düzeyi VEKİL ölçüm: GT kişi durumu GT kutularından türetilir (insan ilişki etiketi değildir), tahmin
   üretim match_items + PPEMonitor güvenilirlik kapısı ile; zamansal PPEMonitor/ByteTrack ÖLÇÜLMEZ."""
from collections import Counter, defaultdict
from pathlib import Path

import ppe_core as core

from .dataset import split_pairs, parse_yolo_label_text, yolo_to_xyxy
from .metrics import detection_counts, match_boxes, prf, person_frame_confusion, box_iou

CLASS_NO_HELMET = 7
SWEEP_CONF = (0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.5, 0.6, 0.7)


def write_data_yaml(root: Path, names: dict, out: Path) -> Path:
    import yaml
    out.write_text(yaml.safe_dump({"path": str(root.resolve()), "train": "images/train", "val": "images/val",
                                   "test": "images/test", "names": {int(k): v for k, v in names.items()}},
                                  allow_unicode=True, sort_keys=False), encoding="utf-8")
    return out


def ultralytics_val(model, data_yaml: Path, split: str, settings: dict, out_dir: Path) -> dict:
    res = model.val(data=str(data_yaml), split=split, imgsz=settings["imgsz"], conf=0.001, iou=0.7, batch=8,
                    device=settings["device"], plots=True, project=str(out_dir), name=f"val_{split}", exist_ok=True,
                    verbose=False, save_json=False)
    names = model.names
    box = res.box
    per = {}
    for i, c in enumerate(box.ap_class_index.tolist()):
        p, r, ap50, ap = box.class_result(i)
        per[names[int(c)]] = {"precision": float(p), "recall": float(r), "ap50": float(ap50), "ap50_95": float(ap)}
    cm = res.confusion_matrix.matrix.tolist() if getattr(res, "confusion_matrix", None) is not None else None
    return {"split": split, "conf": 0.001, "iou_nms": 0.7, "imgsz": settings["imgsz"], "mp": float(box.mp),
            "mr": float(box.mr), "map50": float(box.map50), "map50_95": float(box.map), "per_class": per,
            "confusion_matrix": cm, "confusion_labels": [names[i] for i in sorted(names)] + ["background"],
            "save_dir": str(res.save_dir)}


def load_split(root: Path, split: str, nc: int):
    import cv2
    items = []
    for img, lbl in split_pairs(root, split)[0]:
        im = cv2.imread(str(img))
        if im is None:
            continue
        h, w = im.shape[:2]
        rows, _ = parse_yolo_label_text(lbl.read_text(encoding="utf-8", errors="replace"), nc) if lbl else ([], [])
        gt = defaultdict(list)
        for r in rows:
            gt[r[0]].append(yolo_to_xyxy(r, w, h))
        items.append({"name": img.name, "path": img, "w": w, "h": h, "gt": dict(gt)})
    return items


def predict_split(model, items, settings: dict, conf_floor: float = 0.05):
    for it in items:
        r = model.predict(str(it["path"]), imgsz=settings["imgsz"], conf=conf_floor, device=settings["device"],
                          verbose=False)[0]
        pred = defaultdict(list)
        if r.boxes is not None and len(r.boxes):
            for c, b, s in zip(r.boxes.cls.cpu().numpy().astype(int), r.boxes.xyxy.cpu().numpy(),
                               r.boxes.conf.cpu().numpy()):
                pred[int(c)].append(([float(x) for x in b], float(s)))
        it["pred"] = dict(pred)
    return items


def operating_point(items, names: dict, classes, conf: float, iou: float = 0.5) -> dict:
    res = detection_counts(items, classes, iou_thr=iou, conf_thr=conf)
    return {names[c]: v for c, v in res.items()}


def threshold_sweep(items, names, classes, iou: float = 0.5, confs=SWEEP_CONF) -> dict:
    return {names[c]: [{"conf": cf, **{k: v for k, v in detection_counts(items, [c], iou, cf)[c].items()
                                       if k in ("precision", "recall", "f1", "tp", "fp", "fn")}} for cf in confs]
            for c in classes}


def confidence_distribution(items, classes, conf: float, iou: float = 0.5, bins=(0.25, 0.4, 0.55, 0.7, 0.85, 1.01)):
    out = {}
    for c in classes:
        tp, fp = Counter(), Counter()
        for it in items:
            preds = [p for p in it["pred"].get(c, []) if p[1] >= conf]
            m, f, _ = match_boxes(it["gt"].get(c, []), preds, iou)
            for pi, _, _ in m:
                tp[_bin(preds[pi][1], bins)] += 1
            for pi in f:
                fp[_bin(preds[pi][1], bins)] += 1
        out[c] = {"tp": dict(sorted(tp.items())), "fp": dict(sorted(fp.items()))}
    return out


def _bin(x, bins):
    lo = 0.0
    for b in bins:
        if x < b:
            return f"[{lo:.2f},{min(b, 1.0):.2f})"
        lo = b
    return f">={lo:.2f}"


def error_examples(items, names, classes, conf: float, iou: float = 0.5, limit: int = 15) -> dict:
    """FP (yüksek güvenli sahte) ve FN (kaçırılan, büyüklüğüyle) örnekleri — görüntü değil, kimlik + kutu."""
    out = {}
    for c in classes:
        fps, fns = [], []
        for it in items:
            preds = [p for p in it["pred"].get(c, []) if p[1] >= conf]
            gts = it["gt"].get(c, [])
            m, f, n = match_boxes(gts, preds, iou)
            for pi in f:
                best = max((box_iou(preds[pi][0], g) for g in gts), default=0.0)
                fps.append({"image": it["name"], "conf": round(preds[pi][1], 3), "box": [round(x) for x in
                                                                                         preds[pi][0]],
                            "best_iou_same_class": round(best, 2),
                            "kind": "localization" if best >= 0.1 else "spurious"})
            for gi in n:
                g = gts[gi]
                lower = [p for p in it["pred"].get(c, []) if p[1] < conf and box_iou(p[0], g) >= iou]
                other = [(names[oc], round(p[1], 2)) for oc, ps in it["pred"].items() if oc != c
                         for p in ps if p[1] >= conf and box_iou(p[0], g) >= iou]
                fns.append({"image": it["name"], "box_h_px": round(g[3] - g[1]), "box": [round(x) for x in g],
                            "below_conf_hit": round(max(p[1] for p in lower), 3) if lower else None,
                            "confused_as": other[:3]})
        fps.sort(key=lambda x: -x["conf"])
        fns.sort(key=lambda x: x["box_h_px"])
        out[names[c]] = {"fp_count": len(fps), "fn_count": len(fns), "fp_examples": fps[:limit],
                         "fn_examples": fns[:limit],
                         "fn_by_size": dict(Counter(_size_bin(x["box_h_px"]) for x in fns)),
                         "fn_with_lower_conf_hit": sum(1 for x in fns if x["below_conf_hit"] is not None),
                         "fn_confused_as_other_class": sum(1 for x in fns if x["confused_as"])}
    return out


def _size_bin(h):
    return "<16px" if h < 16 else "16-32px" if h < 32 else "32-64px" if h < 64 else ">=64px"


def recall_by_size(items, names, classes, conf: float, iou: float = 0.5) -> dict:
    out = {}
    for c in classes:
        hit, tot = Counter(), Counter()
        for it in items:
            preds = [p for p in it["pred"].get(c, []) if p[1] >= conf]
            gts = it["gt"].get(c, [])
            m, _, _ = match_boxes(gts, preds, iou)
            got = {gi for _, gi, _ in m}
            for gi, g in enumerate(gts):
                b = _size_bin(g[3] - g[1])
                tot[b] += 1
                hit[b] += gi in got
        out[names[c]] = {b: {"gt": tot[b], "recall": hit[b] / tot[b] if tot[b] else None}
                         for b in ("<16px", "16-32px", "32-64px", ">=64px")}
    return out


# ---------------------------------------------------------------- tek kare kişi-düzeyi vekil
def derive_person_states(gt: dict) -> list[dict]:
    """GT kutularından kişi başına helmet/vest durumu (insan ilişki etiketi YOK; muhafazakâr geometrik türetme):
    - baş bölgesi: kişi kutusunun x aralığı ±%10, üst %-15..%30; gövde: x ±%10, %15..%85 (üretimden bağımsız).
    - helmet: bölgede helmet -> present; no_helmet -> missing; ikisi birden / hiçbiri / kutu birden çok kişiye
      düşüyor -> unknown. vest: vest -> present; veri kümesinde 'no_vest' sınıfı yok -> aksi halde unknown."""
    persons = gt.get(core.CLASS_PERSON, [])

    def zone(p, part):
        x1, y1, x2, y2 = p
        w, h = x2 - x1, y2 - y1
        if part == "head":
            return (x1 - 0.1 * w, y1 - 0.15 * h, x2 + 0.1 * w, y1 + 0.30 * h)
        return (x1 - 0.1 * w, y1 + 0.15 * h, x2 + 0.1 * w, y1 + 0.85 * h)

    def owners(box, part):
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        return [i for i, p in enumerate(persons) if (lambda z: z[0] <= cx <= z[2] and z[1] <= cy <= z[3])(zone(p, part))]

    out = [{"box": p, "helmet": set(), "vest": set(), "ambiguous_head": False, "ambiguous_torso": False}
           for p in persons]
    for cls, key, part in ((core.CLASS_HELMET, "helmet", "head"), (CLASS_NO_HELMET, "no_helmet", "head"),
                           (core.CLASS_VEST, "vest", "torso")):
        for b in gt.get(cls, []):
            own = owners(b, part)
            if len(own) == 1:
                out[own[0]]["helmet" if part == "head" else "vest"].add(key)
            elif len(own) > 1:
                for i in own:
                    out[i][f"ambiguous_{part}"] = True
    res = []
    for o in out:
        hs = o["helmet"]
        helmet = ("unknown" if o["ambiguous_head"] or len(hs) != 1 else
                  "present" if hs == {"helmet"} else "missing")
        vest = "unknown" if o["ambiguous_torso"] else ("present" if o["vest"] else "unknown")
        res.append({"box": o["box"], "helmet": helmet, "vest": vest})
    return res


def person_proxy(items, conf: float, iou_person: float = 0.5) -> dict:
    """Tek kare: tahmin kişileri (conf >= üretim CONF) + match_items + PPEMonitor güvenilirlik kapısı.
    Tek karede PPEMonitor oranı = 0/1 olduğundan 'violation' = güvenilir kişide ekipman eşleşmesi yok."""
    rows = {"helmet": [], "vest": []}
    gated, gt_states = Counter(), Counter()
    for it in items:
        gts = derive_person_states(it["gt"])
        for g in gts:
            gt_states[f"helmet={g['helmet']}"] += 1
            gt_states[f"vest={g['vest']}"] += 1
        P = [(i, b, s) for i, (b, s) in enumerate(p for p in it["pred"].get(core.CLASS_PERSON, []) if p[1] >= conf)]
        helmets = [b for b, s in it["pred"].get(core.CLASS_HELMET, []) if s >= conf]
        vests = [b for b, s in it["pred"].get(core.CLASS_VEST, []) if s >= conf]
        hm = core.match_items(P, helmets, core.head_region)
        vm = core.match_items(P, vests, core.torso_region)
        W, H = it["w"], it["h"]

        def state(i, box, s, m):
            x1, y1, x2, y2 = box
            reliable = (s >= core.PERSON_CONF_MIN and (y2 - y1) >= core.MIN_PERSON_H_PX and x1 > core.EDGE_MARGIN_PX
                        and y1 > core.EDGE_MARGIN_PX and x2 < W - core.EDGE_MARGIN_PX and y2 < H - core.EDGE_MARGIN_PX)
            if not reliable:
                return "undecided"
            return "ok" if i in m else "violation"
        matches, fp_idx, _ = match_boxes([g["box"] for g in gts], [(b, s) for _, b, s in P], iou_person)
        pair = {gi: pi for pi, gi, _ in matches}
        for gi, g in enumerate(gts):
            pi = pair.get(gi)
            for item, m in (("helmet", hm), ("vest", vm)):
                if pi is None:
                    pred = "not_detected"
                else:
                    pred = state(*P[pi], m)
                    if pred == "undecided":
                        gated[item] += 1
                rows[item].append({"gt": g[item], "pred": pred})
        for pi in fp_idx:
            for item, m in (("helmet", hm), ("vest", vm)):
                rows[item].append({"gt": None, "pred": state(*P[pi], m)})
    return {"gt_state_counts": dict(gt_states), "gated_unreliable": dict(gated),
            "helmet": person_frame_confusion(rows["helmet"], "helmet"),
            "vest": person_frame_confusion(rows["vest"], "vest"),
            "vest_note": "GT'de 'no_vest' sınıfı yok: yelek EKSİKLİĞİ GT'si kurulamaz; vest precision/recall "
                         "NOT EVALUATED (yalnız present -> ok/violation dağılımı bilgi amaçlı)"}


def summarize_prf(d: dict) -> dict:
    return {k: v for k, v in d.items() if k in ("tp", "fp", "fn", "precision", "recall", "f1", "fnr", "recall_ci95",
                                                "precision_ci95", "gt_instances")}


__all__ = ["write_data_yaml", "ultralytics_val", "load_split", "predict_split", "operating_point", "threshold_sweep",
           "confidence_distribution", "error_examples", "recall_by_size", "derive_person_states", "person_proxy",
           "summarize_prf", "prf"]
