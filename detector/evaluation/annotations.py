"""Video düzeyinde insan ground-truth şeması (ppe-eval-video/1) ve doğrulayıcı.
Kurallar: docs/MODEL_EVALUATION_PROTOCOL.md §3. Özet:
- kare indeksi 0 tabanlı ORİJİNAL video karesidir; t = frame / fps (sn).
- helmet/vest: present | missing | unknown. Kafa görünmüyorsa helmet 'missing' OLAMAZ (unknown olmalı); gövde
  görünmüyorsa vest 'missing' olamaz. Görünmeme ihlal değildir.
- incident: aynı kişinin aynı ekipman eksikliğinin kesintisiz aralığı; end=null -> video sonuna kadar sürüyor."""
from .metrics import valid_box, STATES

SCHEMA = "ppe-eval-video/1"
VISIBILITY = ("full", "partial", "heavy_occlusion", "head_hidden", "torso_hidden", "head_torso_hidden")
ITEMS = ("helmet", "vest")
REVIEW = ("ok", "disputed", "adjudicated")


def validate(doc: dict) -> tuple[list[str], list[str]]:
    """Döner: (hatalar, uyarılar). Hata varsa dosya değerlendirmede KULLANILMAZ."""
    err, warn = [], []
    if doc.get("schema") != SCHEMA:
        err.append(f"schema '{SCHEMA}' olmalı")
    v = doc.get("video") or {}
    for k in ("video_id", "camera_id", "fps", "frame_count", "width", "height", "split"):
        if v.get(k) in (None, ""):
            err.append(f"video.{k} eksik")
    if v.get("split") not in (None, "train", "val", "test"):
        err.append("video.split train|val|test olmalı")
    for k in ("site_id", "recorded_day", "license", "consent_ref"):
        if not v.get(k):
            warn.append(f"video.{k} yok (sızıntı/izin denetimi eksik kalır)")
    fps, n, W, H = v.get("fps") or 0, v.get("frame_count") or 0, v.get("width") or 0, v.get("height") or 0
    if fps and fps <= 0:
        err.append("video.fps > 0 olmalı")
    if len(doc.get("annotators") or []) < 1:
        err.append("annotators en az bir anonim annotator kimliği içermeli")

    tracks, prev_f, ann_frames = set(), -1, 0
    for i, fr in enumerate(doc.get("frames") or []):
        f = fr.get("frame")
        if not isinstance(f, int) or f < 0 or (n and f >= n):
            err.append(f"frames[{i}].frame geçersiz: {f!r}")
            continue
        if f <= prev_f:
            err.append(f"frames[{i}]: kare indeksleri artan ve tekil olmalı ({f} <= {prev_f})")
        prev_f = f
        ann_frames += 1
        if fps and fr.get("t") is not None and abs(fr["t"] - f / fps) > 0.5 / fps:
            err.append(f"frames[{i}]: t={fr['t']} kare/fps={f / fps:.3f} ile tutarsız")
        seen = set()
        for j, p in enumerate(fr.get("persons") or []):
            where = f"frames[{i}].persons[{j}]"
            tid = p.get("track_id")
            if not tid:
                err.append(f"{where}: track_id eksik")
            elif tid in seen:
                err.append(f"{where}: aynı karede yinelenen track_id {tid}")
            seen.add(tid)
            tracks.add(tid)
            b = p.get("box")
            if not (isinstance(b, list) and valid_box(b)):
                err.append(f"{where}: geçersiz box {b!r}")
            elif W and H and (b[0] < -1 or b[1] < -1 or b[2] > W + 1 or b[3] > H + 1):
                err.append(f"{where}: box kare dışında")
            vis = p.get("visibility")
            if vis not in VISIBILITY:
                err.append(f"{where}: visibility {VISIBILITY} içinden olmalı")
            for item in ITEMS:
                st = p.get(item)
                if st not in STATES:
                    err.append(f"{where}: {item} {STATES} içinden olmalı")
                hidden = ("head" if item == "helmet" else "torso")
                if st == "missing" and vis in (f"{hidden}_hidden", "head_torso_hidden"):
                    err.append(f"{where}: {item}=missing ama {hidden} görünmüyor — 'unknown' olmalı")
                ib = p.get(f"{item}_box")
                if ib is not None and not valid_box(ib):
                    err.append(f"{where}: geçersiz {item}_box")
                if st == "present" and ib is None:
                    warn.append(f"{where}: {item}=present ama {item}_box yok (eşleme doğruluğu ölçülemez)")
            if p.get("review", "ok") not in REVIEW:
                err.append(f"{where}: review {REVIEW} içinden olmalı")
    if ann_frames == 0:
        err.append("hiç etiketli kare yok")

    dur = n / fps if fps and n else None
    ids = set()
    for k, inc in enumerate(doc.get("incidents") or []):
        where = f"incidents[{k}]"
        if inc.get("incident_id") in ids or not inc.get("incident_id"):
            err.append(f"{where}: incident_id eksik/yinelenen")
        ids.add(inc.get("incident_id"))
        if inc.get("item") not in ITEMS:
            err.append(f"{where}: item helmet|vest olmalı")
        if inc.get("track_id") not in tracks:
            err.append(f"{where}: track_id karelerde yok")
        s, e = inc.get("start"), inc.get("end")
        if not isinstance(s, (int, float)) or s < 0:
            err.append(f"{where}: start geçersiz")
        elif e is not None and (not isinstance(e, (int, float)) or e <= s):
            err.append(f"{where}: end > start olmalı")
        elif dur is not None and (s > dur or (e is not None and e > dur + 1e-6)):
            err.append(f"{where}: video süresini ({dur:.2f} sn) aşıyor")
        if inc.get("certainty", "certain") not in ("certain", "uncertain"):
            err.append(f"{where}: certainty certain|uncertain olmalı")
    if not err:
        warn += _incident_consistency(doc)
    return err, warn


def _incident_consistency(doc) -> list[str]:
    """Incident aralığındaki karelerde aynı kişi aynı ekipman için 'present' etiketliyse uyarı (ikinci göz gerekir)."""
    fps = doc["video"]["fps"]
    out = []
    for inc in doc.get("incidents") or []:
        s, e = inc["start"], inc["end"] if inc.get("end") is not None else float("inf")
        for fr in doc["frames"]:
            t = fr["frame"] / fps
            if s <= t <= e:
                for p in fr.get("persons") or []:
                    if p["track_id"] == inc["track_id"] and p[inc["item"]] == "present":
                        out.append(f"{inc['incident_id']}: t={t:.2f} karesinde {inc['item']}=present (incident içi)")
                        break
    return out[:50]


def frames_by_index(doc) -> dict:
    return {fr["frame"]: fr.get("persons") or [] for fr in doc.get("frames") or []}


def ground_truth_incidents(doc) -> list[dict]:
    vid = doc["video"]["video_id"]
    return [{"incident_id": i["incident_id"], "video_id": vid, "track_id": i.get("track_id"), "item": i["item"],
             "start": float(i["start"]), "end": None if i.get("end") is None else float(i["end"]),
             "certainty": i.get("certainty", "certain")} for i in doc.get("incidents") or []]


def example_document() -> dict:
    """Şema örneği (tamamen sentetik; gerçek kişi/görüntü içermez)."""
    return {
        "schema": SCHEMA,
        "video": {"video_id": "v0001", "camera_id": "cam_a", "site_id": "site_01", "recorded_day": "2026-01-15",
                  "fps": 25.0, "frame_count": 250, "width": 1920, "height": 1080, "split": "test",
                  "conditions": ["daylight", "far_workers"], "license": "internal-consented",
                  "consent_ref": "KVKK-AYD-0001"},
        "annotators": ["ann_A", "ann_B"],
        "frames": [
            {"frame": 49, "t": 1.96, "persons": [
                {"track_id": "p1", "box": [100, 100, 220, 460], "visibility": "full", "helmet": "present",
                 "helmet_box": [130, 90, 190, 140], "vest": "present", "vest_box": [110, 200, 210, 340],
                 "review": "ok"}]},
            {"frame": 149, "t": 5.96, "persons": [
                {"track_id": "p1", "box": [140, 100, 260, 460], "visibility": "full", "helmet": "missing",
                 "helmet_box": None, "vest": "present", "vest_box": [150, 200, 250, 340], "review": "adjudicated"}]},
        ],
        "incidents": [{"incident_id": "g1", "track_id": "p1", "item": "helmet", "start": 4.0, "end": None,
                       "certainty": "certain"}],
    }
