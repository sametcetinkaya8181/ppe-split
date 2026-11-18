"""YOLO biçimli etiketli veri denetimi, kaynak düzeyinde sızıntı kontrolü ve donmuş test manifesti.
cv2 yalnız görüntü boyutu / dHash için ve yalnız gerektiğinde içe aktarılır."""
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def sha256_file(path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(chunk), b""):
            h.update(b)
    return h.hexdigest()


# ---------------------------------------------------------------- etiketler
def parse_yolo_label_text(text: str, nc: int):
    """Döner: (satırlar [(cls, cx, cy, w, h)], hatalar [str]). Geçersiz satır atlanır ve hataya yazılır."""
    rows, errors = [], []
    for ln, line in enumerate(text.splitlines(), start=1):
        parts = line.split()
        if not parts:
            continue
        if len(parts) != 5:
            errors.append(f"satır {ln}: 5 alan beklenirdi, {len(parts)} var")
            continue
        try:
            c = int(parts[0])
            cx, cy, w, h = map(float, parts[1:])
        except ValueError:
            errors.append(f"satır {ln}: sayı değil")
            continue
        if not 0 <= c < nc:
            errors.append(f"satır {ln}: sınıf {c} aralık dışı (0..{nc - 1})")
            continue
        if w <= 0 or h <= 0:
            errors.append(f"satır {ln}: genişlik/yükseklik <= 0")
            continue
        if not (0 <= cx <= 1 and 0 <= cy <= 1 and w <= 1 and h <= 1):
            errors.append(f"satır {ln}: normalize koordinat [0,1] dışında")
            continue
        rows.append((c, cx, cy, w, h))
    return rows, errors


def yolo_to_xyxy(row, img_w: int, img_h: int):
    _, cx, cy, w, h = row
    return [(cx - w / 2) * img_w, (cy - h / 2) * img_h, (cx + w / 2) * img_w, (cy + h / 2) * img_h]


def split_pairs(root: Path, split: str):
    """images/<split> ve labels/<split> eşleştirmesi. Döner: (çiftler [(img, lbl|None)], sahipsiz etiketler [Path])."""
    img_dir, lbl_dir = root / "images" / split, root / "labels" / split
    imgs = sorted(p for p in img_dir.iterdir() if p.suffix.lower() in IMAGE_EXTS) if img_dir.is_dir() else []
    lbls = {p.stem: p for p in lbl_dir.glob("*.txt")} if lbl_dir.is_dir() else {}
    stems = {p.stem for p in imgs}
    pairs = [(p, lbls.get(p.stem)) for p in imgs]
    return pairs, sorted(p for s, p in lbls.items() if s not in stems)


def audit_split(root: Path, split: str, names: dict) -> dict:
    pairs, orphans = split_pairs(root, split)
    inst, imgs_with = Counter(), Counter()
    errors, missing_label, empty_label = [], 0, 0
    for img, lbl in pairs:
        if lbl is None:
            missing_label += 1
            continue
        rows, errs = parse_yolo_label_text(lbl.read_text(encoding="utf-8", errors="replace"), len(names))
        errors += [f"{lbl.name}: {e}" for e in errs]
        if not rows:
            empty_label += 1
        cls = [r[0] for r in rows]
        inst.update(cls)
        imgs_with.update(set(cls))
    return {"split": split, "images": len(pairs), "missing_label_files": missing_label,
            "empty_label_files": empty_label, "orphan_label_files": [p.name for p in orphans],
            "invalid_rows": len(errors), "invalid_examples": errors[:20],
            "instances": {names[c]: inst.get(c, 0) for c in sorted(names)},
            "images_with_class": {names[c]: imgs_with.get(c, 0) for c in sorted(names)}}


def check_class_mapping(model_names: dict, dataset_names: dict, required: dict) -> list[str]:
    """required: {"helmet": 0, ...} üretimin beklediği eşleme. Uyumsuzluk listesi (boş = uyumlu)."""
    problems = []
    md = {int(k): v for k, v in model_names.items()}
    dd = {int(k): v for k, v in dataset_names.items()}
    if md != dd:
        diff = sorted(set(md.items()) ^ set(dd.items()))
        problems.append(f"model ve veri sınıf adları farklı: {diff}")
    for name, idx in required.items():
        if md.get(idx) != name:
            problems.append(f"üretim {name}={idx} bekliyor, model {idx}={md.get(idx)!r}")
    return problems


# ---------------------------------------------------------------- sızıntı
def dhash(path, size: int = 8):
    """64 bit fark hash'i (gri, (size+1)xsize). Okunamazsa None."""
    import cv2
    im = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if im is None:
        return None
    im = cv2.resize(im, (size + 1, size), interpolation=cv2.INTER_AREA)
    bits = (im[:, 1:] > im[:, :-1]).flatten()
    return int("".join("1" if b else "0" for b in bits), 2)


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def near_duplicates(query: dict, reference: dict, max_dist: int = 4) -> list:
    """query/reference: {ad: hash}. Döner: [(q, r, mesafe)] (her q için en yakın, mesafe <= max_dist)."""
    out = []
    ref = [(k, v) for k, v in reference.items() if v is not None]
    for qk, qv in query.items():
        if qv is None:
            continue
        best = min(((hamming(qv, rv), rk) for rk, rv in ref), default=None)
        if best is not None and best[0] <= max_dist:
            out.append((qk, best[1], best[0]))
    return out


def leakage_report(root: Path, eval_split: str, train_split: str = "train", max_dist: int = 4) -> dict:
    """Bayt-özdeş (SHA-256), aynı dosya adı kökü ve algısal yakın kopya (dHash) kontrolü.
    Yakın kopya: aynı sahneden alınmış/yeniden boyutlandırılmış kare olabilir — insan incelemesi gerekir."""
    def collect(split):
        pairs, _ = split_pairs(root, split)
        return {p.name: p for p, _ in pairs}
    ev, tr = collect(eval_split), collect(train_split)
    ev_sha = {k: sha256_file(p) for k, p in ev.items()}
    tr_sha = defaultdict(list)
    for k, p in tr.items():
        tr_sha[sha256_file(p)].append(k)
    exact = sorted((k, tr_sha[s][0]) for k, s in ev_sha.items() if s in tr_sha)
    same_stem = sorted({Path(k).stem for k in ev} & {Path(k).stem for k in tr})
    near = near_duplicates({k: dhash(p) for k, p in ev.items()}, {k: dhash(p) for k, p in tr.items()}, max_dist)
    return {"eval_split": eval_split, "train_split": train_split, "eval_images": len(ev), "train_images": len(tr),
            "exact_duplicates": len(exact), "exact_examples": exact[:20], "same_name_stem": len(same_stem),
            "near_duplicates_dhash": len(near), "near_max_hamming": max_dist,
            "near_examples": sorted(near, key=lambda x: x[2])[:30],
            "source_metadata": "YOK — kaynak/kamera/şantiye/gün bilgisi veri kümesinde bulunmadığından kaynak düzeyinde "
                               "ayrım doğrulanamaz"}


def check_group_leakage(entries, keys=("site_id", "camera_id", "source_video", "recorded_day")) -> dict:
    """entries: [{"split": ..., "site_id": ..., ...}]. Aynı grup değeri birden fazla split'te görülürse sızıntı.
    Eksik alan 'bilinmiyor' sayılır ve ayrıca raporlanır (sızıntı olmadığı kanıtlanmış SAYILMAZ)."""
    res = {}
    for key in keys:
        seen, unknown = defaultdict(set), 0
        for e in entries:
            v = e.get(key)
            if v in (None, ""):
                unknown += 1
                continue
            seen[v].add(e["split"])
        leaks = {v: sorted(s) for v, s in seen.items() if len(s) > 1}
        res[key] = {"leaking_values": leaks, "unknown_entries": unknown,
                    "status": "LEAK" if leaks else ("UNKNOWN" if unknown else "OK")}
    return res


# ---------------------------------------------------------------- manifest (donmuş test seti)
def build_manifest(root: Path, splits, *, dataset_name: str, extra: dict | None = None) -> dict:
    files = []
    for split in splits:
        pairs, _ = split_pairs(root, split)
        for img, lbl in pairs:
            files.append({"split": split, "image": img.relative_to(root).as_posix(), "image_sha256": sha256_file(img),
                          "label": lbl.relative_to(root).as_posix() if lbl else None,
                          "label_sha256": sha256_file(lbl) if lbl else None})
    body = json.dumps(files, sort_keys=True).encode()
    return {"schema": "ppe-eval-manifest/1", "dataset": dataset_name, "splits": list(splits),
            "file_count": len(files), "content_sha256": hashlib.sha256(body).hexdigest(), "files": files,
            **(extra or {})}


def verify_manifest(root: Path, manifest: dict) -> list[str]:
    """Donmuş manifest ile diskteki veri arasındaki farklar (boş = tutarlı)."""
    problems = []
    if manifest.get("schema") != "ppe-eval-manifest/1":
        problems.append("manifest şeması tanınmıyor")
    listed = set()
    for f in manifest.get("files", []):
        for k in ("image", "label"):
            rel = f.get(k)
            if rel is None:
                continue
            listed.add(rel)
            p = root / rel
            if not p.exists():
                problems.append(f"eksik dosya: {rel}")
            elif sha256_file(p) != f.get(f"{k}_sha256"):
                problems.append(f"içerik değişmiş: {rel}")
    for split in manifest.get("splits", []):
        pairs, _ = split_pairs(root, split)
        for img, lbl in pairs:
            for p in (img, lbl):
                if p is not None and p.relative_to(root).as_posix() not in listed:
                    problems.append(f"manifestte olmayan dosya: {p.relative_to(root).as_posix()}")
    body = json.dumps(manifest.get("files", []), sort_keys=True).encode()
    if hashlib.sha256(body).hexdigest() != manifest.get("content_sha256"):
        problems.append("manifest içerik özeti tutmuyor (manifest elle değiştirilmiş)")
    return problems
