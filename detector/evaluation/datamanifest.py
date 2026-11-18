"""Veri kümesi manifesti `ppe-dataset-manifest/1`, deterministik grup ayrımı, test dondurma, sızıntı denetimi ve
doğrulama raporu (FAZ 10-A.3). Kurallar: docs/data/DATASET_SPEC.md, docs/data/SPLIT_PROTOCOL.md.

- Yollar manifest köküne göre GÖRELİ POSIX yoludur; mutlak yol, '..', sembolik bağ reddedilir (kök dışına erişim yok).
- Doğrulayıcı hiçbir dosyayı değiştirmez; rapor yalnız manifest içeriğinden türetilir (zaman damgası içermez) ->
  aynı girdi aynı bayt dizisini üretir.
- Durum: FAIL (hata) > BLOCKED (izin/lisans/inceleme/dondurma eksik) > PASS. Gerçek saha veri kümesi ayrıca
  `real_site_dataset`: READY yalnız PASS + sentetik olmayan + izinli/lisanslı verilerde."""
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from . import datacontract as dc
from .dataset import hamming, sha256_file

SCHEMA = "ppe-dataset-manifest/1"
VALIDATOR = "ppe-data-validator/1"
SPLITS = ("train", "val", "test")
CLASSIFICATIONS = ("SYNTHETIC", "PERSONAL_DATA", "LICENSED_PUBLIC")
LEVELS = ("site_id", "camera_id", "session_id", "source_video_id")     # kaba -> ince
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
DHASH_RE = re.compile(r"^[0-9a-f]{16}$")
ITEM_META = ("site_id", "camera_id", "session_id", "source_video_id", "recorded_day")
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".m4v"}


def production_label_map() -> dict:
    import ppe_core as core
    return {"helmet": core.CLASS_HELMET, "vest": core.CLASS_VEST, "Person": core.CLASS_PERSON}


def canonical_sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


# ---------------------------------------------------------------- güvenli yol
def safe_path(root: Path, rel) -> tuple[Path | None, str | None]:
    """Döner (yol, None) veya (None, neden). Kök dışına çıkan, mutlak, '..' içeren veya sembolik bağ yolu reddedilir."""
    if not isinstance(rel, str) or not rel or "\\" in rel or rel.startswith("/") or re.match(r"^[A-Za-z]:", rel):
        return None, "göreli POSIX yolu olmalı"
    parts = rel.split("/")                       # PurePosixPath '.' bölümlerini sessizce atar; ham bölümler denetlenir
    if any(p in ("..", ".", "") for p in parts):
        return None, "'..', '.' veya boş bölüm içeremez"
    root_r = root.resolve()
    cur = root
    for part in parts:
        cur = cur / part
        if cur.is_symlink():
            return None, "sembolik bağ reddedildi"
    p = (root / rel).resolve()
    try:
        p.relative_to(root_r)
    except ValueError:
        return None, "kök dışına çıkıyor"
    return p, None


# ---------------------------------------------------------------- algısal hash
def media_dhash(path: Path, keyframes: int = 5) -> list[str] | None:
    """Görüntü: tek dHash; video: eşit aralıklı keyframes adet kare dHash'i (hex16). cv2 yoksa / okunamazsa None."""
    try:
        import cv2
    except ImportError:
        return None

    def dh(img):
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
        g = cv2.resize(g, (9, 8), interpolation=cv2.INTER_AREA)
        return f"{int(''.join('1' if b else '0' for b in (g[:, 1:] > g[:, :-1]).flatten()), 2):016x}"
    if path.suffix.lower() in VIDEO_EXTS:
        cap = cv2.VideoCapture(str(path))
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        out = []
        for k in range(keyframes):
            cap.set(cv2.CAP_PROP_POS_FRAMES, (k * max(n - 1, 0)) // max(keyframes - 1, 1))
            ok, img = cap.read()
            if ok:
                out.append(dh(img))
        cap.release()
        return out or None
    img = cv2.imread(str(path))
    return None if img is None else [dh(img)]


# ---------------------------------------------------------------- ayrım (split)
def assign_splits(entries: list[dict], *, group_by: str, seed: str, ratios: dict) -> dict:
    """Deterministik grup ayrımı: aynı group_by değerine sahip tüm kayıtlar aynı split'e gider. Gruplar
    sha256(seed|grup) sırasıyla dolaşılır ve hedef orana en uzak split'e atanır. Girdi sırası sonucu değiştirmez.
    Döner: {media_id: split}."""
    if group_by not in LEVELS:
        raise ValueError(f"group_by {LEVELS} içinden olmalı")
    if set(ratios) != set(SPLITS) or abs(sum(ratios.values()) - 1) > 1e-6 or min(ratios.values()) < 0:
        raise ValueError("ratios train/val/test ve toplam 1 olmalı")
    groups = defaultdict(list)
    for e in entries:
        if not e.get(group_by):
            raise ValueError(f"{e.get('media_id')}: {group_by} eksik — grup bilinmeden ayrım yapılmaz")
        groups[e[group_by]].append(e["media_id"])
    order = sorted(groups, key=lambda g: (hashlib.sha256(f"{seed}|{g}".encode()).hexdigest(), g))
    total = sum(len(v) for v in groups.values())
    count = {s: 0 for s in SPLITS}
    out = {}
    for g in order:
        s = max(SPLITS, key=lambda s: (ratios[s] * total - count[s], -SPLITS.index(s)))
        for mid in sorted(groups[g]):
            out[mid] = s
        count[s] += len(groups[g])
    return out


def test_items_sha(items: list[dict]) -> str:
    return canonical_sha(sorted((i for i in items if i.get("split") == "test"), key=lambda i: i["media_id"]))


def freeze_test(manifest: dict, *, frozen_at: str, approved_by_role: str) -> dict:
    """Test kümesini dondurur (özet kaydı). Değişen her test kaydı sonraki doğrulamada E_TEST_CHANGED üretir."""
    out = dict(manifest)
    out["test_freeze"] = {"frozen": True, "test_items_sha256": test_items_sha(manifest["items"]),
                          "frozen_at": frozen_at, "approved_by_role": approved_by_role}
    return out


# ---------------------------------------------------------------- sayımlar
def label_counts(docs_by_split: dict) -> dict:
    out = {}
    for split in SPLITS:
        c = Counter()
        for doc in docs_by_split.get(split, []):
            for fr in doc.get("frames") or []:
                c["frames"] += 1
                for p in fr.get("persons") or []:
                    c["persons"] += 1
                    c[f"person_{p.get('person_status')}"] += 1
                    for item in dc.ITEMS:
                        c[f"{item}_{(p.get(item) or {}).get('status')}"] += 1
            for inc in doc.get("incidents") or []:
                c[f"incident_{inc.get('violation_type')}"] += 1
            c["media"] += 1
        out[split] = dict(sorted(c.items()))
    return out


def scenario_distribution(docs_by_split: dict) -> dict:
    return {s: dict(sorted(Counter(t for d in docs_by_split.get(s, [])
                                   for t in (d.get("media") or {}).get("scenario_tags", [])).items()))
            for s in SPLITS}


def _load_doc(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, ValueError) as exc:
        return None, type(exc).__name__


# ---------------------------------------------------------------- manifest üretimi
def build_manifest(root: Path, items: list[dict], *, dataset_id: str, dataset_version: str, created_at: str,
                   data_classification: str, split_policy: dict, description: str = "",
                   compute_dhash: bool = True) -> dict:
    """items: [{media_id, media_path, annotation_path, split, source_ref?}] — metadata etiket belgesinden alınır.
    created_at çağıran tarafından verilir (deterministik)."""
    root = Path(root)
    out_items, docs_by_split = [], defaultdict(list)
    for it in sorted(items, key=lambda i: i["media_id"]):
        mp, why = safe_path(root, it["media_path"])
        ap, why2 = safe_path(root, it["annotation_path"])
        if mp is None or ap is None:
            raise ValueError(f"{it['media_id']}: {why or why2}")
        doc, err = _load_doc(ap)
        if doc is None:
            raise ValueError(f"{it['media_id']}: etiket okunamadı ({err})")
        m = doc.get("media") or {}
        rec = {"media_id": it["media_id"], "media_path": it["media_path"], "media_sha256": sha256_file(mp),
               "annotation_path": it["annotation_path"], "annotation_sha256": sha256_file(ap),
               "annotation_version": doc.get("annotation_version"), "split": it["split"],
               "source_ref": it.get("source_ref") or (m.get("provenance") or {}).get("source_ref"),
               "authorization": m.get("authorization"), "license": m.get("license"),
               **{k: m.get(k) for k in ITEM_META}}
        dh = it.get("dhash") if "dhash" in it else (media_dhash(mp) if compute_dhash else None)
        if dh is not None:
            rec["dhash"] = dh
        out_items.append(rec)
        docs_by_split[it["split"]].append(doc)
    return {"schema_version": SCHEMA, "dataset_id": dataset_id, "dataset_version": dataset_version,
            "annotation_schema_version": dc.SCHEMA, "created_at": created_at, "description": description,
            "data_classification": data_classification, "label_map": production_label_map(),
            "split_policy": split_policy, "items": out_items,
            "label_counts": label_counts(docs_by_split), "scenario_distribution": scenario_distribution(docs_by_split),
            "content_sha256": canonical_sha(out_items), "test_freeze": {"frozen": False}}


# ---------------------------------------------------------------- doğrulama
def validate_dataset(root: Path, manifest: dict, *, max_hamming: int = 4) -> dict:
    root = Path(root)
    f = dc.Findings()
    if not isinstance(manifest, dict):
        f.error("E_MANIFEST", "$", "manifest nesne olmalı")
        return _report(manifest or {}, f, {})
    if manifest.get("schema_version") != SCHEMA:
        f.error("E_MANIFEST", "$.schema_version", f"'{SCHEMA}' olmalı")
    if not dc.ID_RE.match(str(manifest.get("dataset_id", ""))):
        f.error("E_MANIFEST", "$.dataset_id", "geçersiz")
    if not SEMVER_RE.match(str(manifest.get("dataset_version", ""))):
        f.error("E_MANIFEST", "$.dataset_version", "MAJOR.MINOR.PATCH olmalı")
    if manifest.get("annotation_schema_version") != dc.SCHEMA:
        f.error("E_VERSION_MISMATCH", "$.annotation_schema_version", f"'{dc.SCHEMA}' bekleniyor")
    if not dc._iso_utc(manifest.get("created_at")):
        f.error("E_TIMESTAMP", "$.created_at", "UTC ISO-8601 (…Z)")
    cls = manifest.get("data_classification")
    if cls not in CLASSIFICATIONS:
        f.error("E_MANIFEST", "$.data_classification", f"{CLASSIFICATIONS} içinden olmalı")
    if manifest.get("label_map") != production_label_map():
        f.error("E_CLASS_MAP", "$.label_map", f"üretim eşlemesi {production_label_map()} ile uyuşmuyor")
    sp = manifest.get("split_policy") or {}
    if sp.get("group_by") not in LEVELS or not sp.get("seed"):
        f.error("E_SPLIT_POLICY", "$.split_policy", f"group_by {LEVELS} ve seed gerekli")
    items = manifest.get("items")
    if not isinstance(items, list) or not items:
        f.error("E_MANIFEST", "$.items", "boş")
        return _report(manifest, f, {})

    synthetic = cls == "SYNTHETIC"
    docs_by_split, seen_ids, by_sha, by_ann = defaultdict(list), set(), defaultdict(list), set()
    for it in sorted(items, key=lambda i: str(i.get("media_id"))):
        mid = it.get("media_id")
        w = f"items[{mid}]"
        if not mid or not dc.ID_RE.match(str(mid)) or mid in seen_ids:
            f.error("E_MANIFEST", w, "media_id eksik/geçersiz/yinelenen")
        seen_ids.add(mid)
        if it.get("split") not in SPLITS:
            f.error("E_SPLIT", w, f"split {SPLITS} içinden olmalı")
        for k in ("media_sha256", "annotation_sha256"):
            if not HEX64_RE.match(str(it.get(k) or "")):
                f.error("E_HASH_MISSING", f"{w}.{k}", "SHA-256 eksik/geçersiz")
        for k in ("authorization", "license"):
            if not isinstance(it.get(k), dict):
                f.error("E_AUTHORIZATION" if k == "authorization" else "E_LICENSE", f"{w}.{k}", "izin/lisans durumu eksik")
        if not it.get("source_ref"):
            f.error("E_PROVENANCE", f"{w}.source_ref", "köken referansı eksik")
        auth, lic = it.get("authorization") or {}, it.get("license") or {}
        if synthetic and (auth.get("status") != "SYNTHETIC" or lic.get("status") != "SYNTHETIC"):
            f.error("E_AUTHORIZATION", w, "SYNTHETIC veri kümesinde yalnız sentetik kayıt olabilir")
        if not synthetic and auth.get("status") == "SYNTHETIC":
            f.error("E_AUTHORIZATION", w, "gerçek veri kümesinde sentetik kayıt")
        dh = it.get("dhash")
        if dh is None:
            if synthetic:
                f.warn("W_NO_PHASH", w, "algısal hash yok: yakın kopya sızıntısı denetlenemedi")
            else:
                f.block("B_NO_PHASH", w, "algısal hash yok: yakın kopya sızıntısı denetlenemedi")
        elif not (isinstance(dh, list) and dh and all(isinstance(x, str) and DHASH_RE.match(x) for x in dh)):
            f.error("E_MANIFEST", f"{w}.dhash", "hex16 listesi olmalı")

        mp, why = safe_path(root, it.get("media_path"))
        if mp is None:
            f.error("E_PATH", f"{w}.media_path", why)
        elif not mp.is_file():
            f.error("E_FILE_MISSING", f"{w}.media_path", "dosya yok")
        elif HEX64_RE.match(str(it.get("media_sha256") or "")) and sha256_file(mp) != it["media_sha256"]:
            f.error("E_HASH_MISMATCH", f"{w}.media_path", "medya içeriği manifestle uyuşmuyor")
        if it.get("media_sha256"):
            by_sha[it["media_sha256"]].append((mid, it.get("split")))
        ap, why = safe_path(root, it.get("annotation_path"))
        if ap is None:
            f.error("E_PATH", f"{w}.annotation_path", why)
            continue
        if it["annotation_path"] in by_ann:
            f.error("E_MANIFEST", f"{w}.annotation_path", "aynı etiket dosyası birden çok kayıtta")
        by_ann.add(it["annotation_path"])
        if not ap.is_file():
            f.error("E_FILE_MISSING", f"{w}.annotation_path", "dosya yok")
            continue
        if HEX64_RE.match(str(it.get("annotation_sha256") or "")) and sha256_file(ap) != it["annotation_sha256"]:
            f.error("E_HASH_MISMATCH", f"{w}.annotation_path", "etiket içeriği manifestle uyuşmuyor")
        doc, err = _load_doc(ap)
        if doc is None:
            f.error("E_SCHEMA", f"{w}.annotation_path", f"JSON okunamadı ({err})")
            continue
        f.extend(dc.validate_document(doc, synthetic_ok=synthetic), prefix=f"{w}:")
        m = doc.get("media") or {}
        if m.get("media_id") != mid:
            f.error("E_MANIFEST", w, f"etiketin media_id'si {m.get('media_id')!r}")
        for k in ITEM_META:
            if it.get(k) != m.get(k):
                f.error("E_MANIFEST", f"{w}.{k}", f"manifest {it.get(k)!r} ≠ etiket {m.get(k)!r}")
        if it.get("annotation_version") != doc.get("annotation_version"):
            f.error("E_VERSION_MISMATCH", w, f"manifest sürümü {it.get('annotation_version')!r}, etiket "
                                             f"{doc.get('annotation_version')!r}")
        if auth != (m.get("authorization") or {}) or lic != (m.get("license") or {}):
            f.error("E_AUTHORIZATION", w, "izin/lisans manifest ile etikette farklı")
        if it.get("split") in SPLITS:
            docs_by_split[it["split"]].append(doc)

    _leakage(items, sp.get("group_by"), by_sha, max_hamming, f)
    if manifest.get("label_counts") != label_counts(docs_by_split) or \
            manifest.get("scenario_distribution") != scenario_distribution(docs_by_split):
        f.error("E_MANIFEST_TAMPERED", "$.label_counts", "sayımlar etiketlerden yeniden hesaplananla uyuşmuyor")
    if manifest.get("content_sha256") != canonical_sha(items):
        f.error("E_MANIFEST_TAMPERED", "$.content_sha256", "kayıt listesi özeti uyuşmuyor (manifest elle değişmiş)")
    tf = manifest.get("test_freeze") or {}
    if tf.get("frozen"):
        if tf.get("test_items_sha256") != test_items_sha(items):
            f.error("E_TEST_CHANGED", "$.test_freeze", "dondurulmuş test kümesi değişmiş")
    else:
        f.block("B_TEST_NOT_FROZEN", "$.test_freeze", "test kümesi dondurulmadı")
    return _report(manifest, f, {s: len(v) for s, v in docs_by_split.items()})


def _leakage(items, group_by, by_sha, max_hamming, f: dc.Findings):
    for sha, uses in sorted(by_sha.items()):
        if len(uses) > 1:
            splits = sorted({s for _, s in uses if s})
            ids = sorted(str(m) for m, _ in uses)
            if len(splits) > 1:
                f.error("E_LEAKAGE_DUPLICATE", "items", f"aynı medya farklı split'lerde: {ids} {splits}")
            else:
                f.error("E_DUPLICATE_MEDIA", "items", f"aynı medya birden çok kayıtta: {ids}")
    if group_by in LEVELS:
        start = LEVELS.index(group_by)
        for lvl_i, key in enumerate(LEVELS):
            seen = defaultdict(set)
            for it in items:
                if it.get(key) and it.get("split") in SPLITS:
                    seen[it[key]].add(it["split"])
            for val, splits in sorted(seen.items()):
                if len(splits) > 1:
                    msg = f"{key}={val} birden çok split'te: {sorted(splits)}"
                    if lvl_i >= start:
                        f.error("E_LEAKAGE", f"items.{key}", msg)
                    else:
                        f.warn("W_GROUP_SHARED", f"items.{key}", msg + f" (group_by={group_by}; daha kaba düzey)")
    hashed = sorted(((str(it.get("media_id")), it.get("split"), it["dhash"]) for it in items
                     if isinstance(it.get("dhash"), list) and all(isinstance(x, str) and DHASH_RE.match(x)
                                                                  for x in it["dhash"])), key=lambda x: x[0])
    for i in range(len(hashed)):
        for j in range(i + 1, len(hashed)):
            (a, sa, ha), (b, sb, hb) = hashed[i], hashed[j]
            d = min(hamming(int(x, 16), int(y, 16)) for x in ha for y in hb)
            if d <= max_hamming:
                if sa != sb:
                    f.error("E_NEAR_DUPLICATE", "items", f"{a}({sa}) ~ {b}({sb}) dHash mesafe {d}: sızıntı adayı, "
                                                         "insan incelemesi")
                else:
                    f.warn("W_NEAR_DUPLICATE", "items", f"{a} ~ {b} ({sa}) dHash mesafe {d}")


def _report(manifest: dict, f: dc.Findings, media_per_split: dict) -> dict:
    status = f.status
    reasons = []
    if manifest.get("data_classification") == "SYNTHETIC":
        reasons.append("SYNTHETIC: sentetik veri gerçek saha veri kümesi değildir")
    if status != "PASS":
        reasons.append(f"doğrulama durumu {status}")
    return {"validator": VALIDATOR, "dataset_id": manifest.get("dataset_id"),
            "dataset_version": manifest.get("dataset_version"),
            "data_classification": manifest.get("data_classification"),
            "manifest_content_sha256": manifest.get("content_sha256"), "status": status,
            "real_site_dataset": "BLOCKED" if reasons else "READY", "real_site_reasons": reasons,
            "media_per_split": {s: media_per_split.get(s, 0) for s in SPLITS},
            "counts": {"errors": len(f.errors), "blockers": len(f.blockers), "warnings": len(f.warnings)},
            "codes": dict(sorted(Counter(x["code"] for x in f.errors + f.blockers + f.warnings).items())),
            "errors": f.errors, "blockers": f.blockers, "warnings": f.warnings}


def attach_validation(manifest: dict, report: dict) -> dict:
    """Manifest kopyasına doğrulama sonucunu ekler (content_sha256 kayıt listesinden hesaplandığı için değişmez)."""
    return {**manifest, "validation": {"validator": report["validator"], "status": report["status"],
                                       "report_sha256": canonical_sha(report)}}


# ---------------------------------------------------------------- rapor
def render_json(report: dict) -> str:
    return json.dumps(report, ensure_ascii=False, indent=1, sort_keys=True) + "\n"


def render_markdown(report: dict, limit: int = 200) -> str:
    L = [f"# Veri kümesi doğrulama raporu — {report.get('dataset_id')} {report.get('dataset_version')}", "",
         f"- Doğrulayıcı: `{report['validator']}`",
         f"- Manifest içerik özeti: `{report.get('manifest_content_sha256')}`",
         f"- Sınıflandırma: **{report.get('data_classification')}**",
         f"- **Durum: {report['status']}**",
         f"- Gerçek saha veri kümesi: **{report['real_site_dataset']}**"
         + (f" ({'; '.join(report['real_site_reasons'])})" if report["real_site_reasons"] else ""),
         f"- Medya / split: " + ", ".join(f"{k} {v}" for k, v in report["media_per_split"].items()),
         f"- Hata {report['counts']['errors']}, engel {report['counts']['blockers']}, uyarı "
         f"{report['counts']['warnings']}", ""]
    for title, key in (("Hatalar (FAIL)", "errors"), ("Engeller (BLOCKED)", "blockers"), ("Uyarılar", "warnings")):
        rows = report[key]
        L += [f"## {title}", ""]
        if not rows:
            L += ["Yok.", ""]
            continue
        L += ["| Kod | Konum | Açıklama |", "|---|---|---|"]
        L += [f"| `{r['code']}` | `{r['where']}` | {str(r['msg']).replace('|', '/')} |" for r in rows[:limit]]
        if len(rows) > limit:
            L.append(f"| … | | {len(rows) - limit} kayıt daha (JSON raporunda) |")
        L.append("")
    L.append("Bu rapor model doğruluğu ölçümü değildir; yalnız veri ve etiket bütünlüğünü denetler.")
    return "\n".join(L) + "\n"
