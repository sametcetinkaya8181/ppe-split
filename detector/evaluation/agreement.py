"""Etiketleyiciler arası uyum (inter-annotator agreement), FAZ 10-A.3. Aynı medyanın iki bağımsız
`ppe-annotation/1` belgesini karşılaştırır. Kutu uyumu ve ekipman durumu uyumu AYRI raporlanır.
Anlaşmazlıklar listelenir; çoğunluk/otomatik karar VERİLMEZ (çözüm adjudicator'ındır).
Prosedür: docs/data/LABELING_GUIDE.md §6."""
from collections import Counter

from .datacontract import ITEMS, STATUSES
from .metrics import match_boxes, safe_div


def cohen_kappa(pairs, categories) -> float | None:
    """pairs: [(a, b)]. Beklenen uyum 1 ise (tek kategori) None döner (tanımsız; 1 veya 0 uydurulmaz)."""
    n = len(pairs)
    if not n:
        return None
    po = sum(1 for a, b in pairs if a == b) / n
    ca, cb = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    pe = sum(ca[c] * cb[c] for c in categories) / (n * n)
    return None if pe >= 1 else (po - pe) / (1 - pe)


def compare(doc_a: dict, doc_b: dict, *, iou_thr: float = 0.5) -> dict:
    if (doc_a.get("media") or {}).get("media_id") != (doc_b.get("media") or {}).get("media_id"):
        raise ValueError("farklı medya karşılaştırılamaz")
    fa = {fr["frame_index"]: fr.get("persons") or [] for fr in doc_a.get("frames") or []}
    fb = {fr["frame_index"]: fr.get("persons") or [] for fr in doc_b.get("frames") or []}
    common = sorted(set(fa) & set(fb))
    n_a = n_b = matched = 0
    ious, status_pairs = [], {i: [] for i in ITEMS}
    disagreements = []
    for f in common:
        pa, pb = fa[f], fb[f]
        n_a, n_b = n_a + len(pa), n_b + len(pb)
        matches, only_b, only_a = match_boxes([p["box"] for p in pa], [(p["box"], 1.0) for p in pb], iou_thr)
        for bi, ai, iou in matches:
            matched += 1
            ious.append(iou)
            for item in ITEMS:
                sa, sb = pa[ai][item]["status"], pb[bi][item]["status"]
                status_pairs[item].append((sa, sb))
                if sa != sb:
                    disagreements.append({"frame_index": f, "kind": "status", "item": item,
                                          "track_a": pa[ai]["track_id"], "track_b": pb[bi]["track_id"],
                                          "a": sa, "b": sb})
        for ai in only_a:
            disagreements.append({"frame_index": f, "kind": "person_only_in_a", "track_a": pa[ai]["track_id"]})
        for bi in only_b:
            disagreements.append({"frame_index": f, "kind": "person_only_in_b", "track_b": pb[bi]["track_id"]})
    status = {}
    for item in ITEMS:
        pairs = status_pairs[item]
        conf = Counter(f"{a}->{b}" for a, b in pairs)
        status[item] = {"n": len(pairs), "raw_agreement": safe_div(sum(1 for a, b in pairs if a == b), len(pairs)),
                        "cohen_kappa": cohen_kappa(pairs, STATUSES), "confusion": dict(sorted(conf.items())),
                        "missing_disagreements": sum(1 for a, b in pairs if (a == "MISSING") != (b == "MISSING"))}
    return {"media_id": doc_a["media"]["media_id"], "annotators": [doc_a.get("annotators"), doc_b.get("annotators")],
            "frames_compared": len(common), "frames_only_a": len(set(fa) - set(fb)),
            "frames_only_b": len(set(fb) - set(fa)), "iou_threshold": iou_thr,
            "box": {"persons_a": n_a, "persons_b": n_b, "matched": matched,
                    "f1": safe_div(2 * matched, n_a + n_b), "mean_iou": safe_div(sum(ious), len(ious)),
                    "min_iou": min(ious) if ious else None},
            "status": status, "disagreements": disagreements,
            "resolution": "ADJUDICATION_REQUIRED" if disagreements else "NONE_NEEDED"}

