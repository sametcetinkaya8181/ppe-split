"""Veri kümesi etiket sözleşmesi `ppe-annotation/1` (FAZ 10-A.3) ve belge doğrulayıcısı.

Belge = bir kaynak medyanın (video veya kare dizisi) insan etiketleri. Kurallar: docs/data/DATASET_SPEC.md,
docs/data/LABELING_GUIDE.md. FAZ 8 değerlendirme hattı `ppe-eval-video/1` bekler; `to_eval_video` dönüştürür.

Doğrulayıcı ham veriyi DEĞİŞTİRMEZ ve hiçbir etiketi düzeltmez; yalnız bulguları döner:
  error   -> belge kullanılamaz (FAIL)
  blocker -> yapı doğru ama kullanım engelli (ör. insan incelemesi eksik, izin/lisans doğrulanmadı) (BLOCKED)
  warning -> insan gözü gerekir; durumu değiştirmez
Yalnız standart kütüphane."""
import re
from datetime import date, datetime

from .metrics import valid_box

SCHEMA = "ppe-annotation/1"
EVAL_SCHEMA = "ppe-eval-video/1"

# Ekipman durumu. UNKNOWN: değerlendirilebilir kişide ilgili bölge (kafa/gövde) bu karede görünmüyor.
# NOT_EVALUABLE: kişinin tamamı veri kümesinin değerlendirilebilirlik ölçütünü karşılamıyor (çok küçük, ağır
# bulanık, karanlık, büyük kısmı kadraj dışı). İkisi de ihlal DEĞİLDİR ve uygun DA değildir; metriklerde ayrı sayılır.
STATUSES = ("PRESENT", "MISSING", "UNKNOWN", "NOT_EVALUABLE")
PERSON_STATUSES = ("EVALUABLE", "NOT_EVALUABLE")
NOT_EVALUABLE_REASONS = ("too_small", "motion_blur", "low_light", "truncated", "crowd_overlap", "other")
VISIBILITY = ("full", "partial", "heavy_occlusion", "head_hidden", "torso_hidden", "head_torso_hidden")
OCCLUSION = ("none", "partial", "heavy")
CONFIDENCE = ("high", "medium", "low")
ITEMS = ("helmet", "vest")
VIOLATIONS = {"missing_helmet": "helmet", "missing_vest": "vest"}
LABEL_ORIGINS = ("human", "model_prelabel")
PERSON_REVIEW = ("pending", "agreed", "disputed", "adjudicated")
DOC_REVIEW = ("approved", "changes_requested")
AUTHORIZATION = ("APPROVED", "PENDING", "REJECTED", "WITHDRAWN", "SYNTHETIC")
LICENSE = ("VERIFIED", "LICENSE_UNVERIFIED", "SYNTHETIC")
UNCERTAINTY = ("certain", "uncertain")

ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
SITE_RE = re.compile(r"^site_[A-Za-z0-9]{1,16}$")            # anonim saha kimliği (adres/ad değil)
PERSON_ROLE_RE = re.compile(r"^(ann|rev|adj)_[A-Za-z0-9]{1,16}$")   # anonim etiketleyici/inceleyici kimliği
# Kişi kimliği veya biyometri taşıyabilecek alan adları: belgenin HİÇBİR yerinde bulunamaz.
FORBIDDEN_KEYS = {"name", "full_name", "person_name", "employee_id", "worker_id", "national_id", "tc", "tc_kimlik",
                  "face", "face_box", "face_embedding", "embedding", "biometric", "phone", "email", "address",
                  "plate", "license_plate"}
HEAD_HIDDEN = {"head_hidden", "head_torso_hidden"}
TORSO_HIDDEN = {"torso_hidden", "head_torso_hidden"}
ASSOC_MARGIN = 0.10                                          # PPE kutusu merkezi kişi kutusunun %10 genişletilmiş hali içinde


class Findings:
    """Kod + konum + açıklama; makine tarafından okunabilir."""

    def __init__(self):
        self.errors, self.blockers, self.warnings = [], [], []

    def _add(self, bucket, code, where, msg):
        bucket.append({"code": code, "where": where, "msg": msg})

    def error(self, code, where, msg):
        self._add(self.errors, code, where, msg)

    def block(self, code, where, msg):
        self._add(self.blockers, code, where, msg)

    def warn(self, code, where, msg):
        self._add(self.warnings, code, where, msg)

    def extend(self, other, prefix=""):
        for mine, theirs in ((self.errors, other.errors), (self.blockers, other.blockers),
                             (self.warnings, other.warnings)):
            mine += [{**x, "where": prefix + x["where"]} for x in theirs]

    @property
    def status(self) -> str:
        return "FAIL" if self.errors else "BLOCKED" if self.blockers else "PASS"

    def as_dict(self) -> dict:
        return {"status": self.status, "errors": self.errors, "blockers": self.blockers, "warnings": self.warnings}


def _iso_day(v) -> bool:
    try:
        date.fromisoformat(v)
        return True
    except (TypeError, ValueError):
        return False


def _iso_utc(v) -> bool:
    if not isinstance(v, str) or not v.endswith("Z"):
        return False
    try:
        datetime.fromisoformat(v[:-1] + "+00:00")
        return True
    except ValueError:
        return False


def privacy_scan(obj, f: Findings, where="$"):
    """Yasak alan adlarını özyinelemeli arar (izleme/kimliklendirme verisi etikete giremez)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).lower() in FORBIDDEN_KEYS:
                f.error("E_PRIVACY_FIELD", f"{where}.{k}", "kişi kimliği/biyometri alanı yasak (izleme/kimliklendirme "
                                                          "kapsam dışı)")
            privacy_scan(v, f, f"{where}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            privacy_scan(v, f, f"{where}[{i}]")


def associated(item_box, person_box, margin: float = ASSOC_MARGIN) -> bool:
    x1, y1, x2, y2 = person_box
    mx, my = (x2 - x1) * margin, (y2 - y1) * margin
    cx, cy = (item_box[0] + item_box[2]) / 2, (item_box[1] + item_box[3]) / 2
    return x1 - mx <= cx <= x2 + mx and y1 - my <= cy <= y2 + my


def is_critical(p: dict) -> bool:
    """İkinci insan kontrolü zorunlu etiket: ihlal/belirsizlik içeren, düşük güvenli veya modelden önetiketli."""
    return (any((p.get(i) or {}).get("status") in ("MISSING", "UNKNOWN", "NOT_EVALUABLE") for i in ITEMS)
            or p.get("annotator_confidence") == "low" or p.get("label_origin") == "model_prelabel")


def _check_review(rv, critical: bool, annotators: set, f: Findings, where: str):
    if not isinstance(rv, dict) or rv.get("status") not in PERSON_REVIEW:
        f.error("E_REVIEW_INVALID", where, f"review.status {PERSON_REVIEW} içinden olmalı")
        return
    st = rv["status"]
    if st == "agreed":
        if not rv.get("reviewer") or rv["reviewer"] in annotators:
            f.error("E_REVIEW_NOT_INDEPENDENT", where, "agreed: bağımsız reviewer (etiketleyiciden farklı) gerekli")
    elif st == "adjudicated":
        if not rv.get("adjudicator") or rv["adjudicator"] in annotators or not rv.get("resolution"):
            f.error("E_REVIEW_NOT_INDEPENDENT", where, "adjudicated: etiketleyiciden farklı adjudicator ve "
                                                       "resolution gerekli")
    elif st == "disputed":
        if not rv.get("reason"):
            f.error("E_REVIEW_INVALID", where, "disputed: disagreement reason gerekli")
        f.block("B_DISPUTE_UNRESOLVED", where, "anlaşmazlık çözülmedi (otomatik çoğunluk kararı verilmez)")
    elif st == "pending" and critical:
        f.block("B_REVIEW_MISSING", where, "kritik etiket ikinci insan incelemesi bekliyor")
    for k in ("reviewer", "adjudicator"):
        if rv.get(k) and not PERSON_ROLE_RE.match(rv[k]):
            f.error("E_ID_FORMAT", f"{where}.{k}", "anonim rol kimliği olmalı (ann_/rev_/adj_ + kısa kod)")


def validate_document(doc: dict, *, synthetic_ok: bool = True) -> Findings:
    """`ppe-annotation/1` belgesini doğrular. synthetic_ok=False: SYNTHETIC izin/lisans engel sayılır."""
    f = Findings()
    if not isinstance(doc, dict):
        f.error("E_SCHEMA", "$", "belge nesne olmalı")
        return f
    privacy_scan(doc, f)
    if doc.get("schema_version") != SCHEMA:
        f.error("E_SCHEMA", "$.schema_version", f"'{SCHEMA}' olmalı")
    if not isinstance(doc.get("annotation_version"), int) or doc["annotation_version"] < 1:
        f.error("E_SCHEMA", "$.annotation_version", "pozitif tamsayı olmalı")
    if doc.get("origin") != "human":
        f.error("E_MODEL_AS_GT", "$.origin", "ground-truth belgesi insan etiketi olmalı (model tahmini GT değildir)")
    annotators = doc.get("annotators") or []
    if not annotators:
        f.error("E_SCHEMA", "$.annotators", "en az bir anonim etiketleyici")
    for a in annotators:
        if not isinstance(a, str) or not PERSON_ROLE_RE.match(a):
            f.error("E_ID_FORMAT", "$.annotators", f"anonim rol kimliği olmalı: {a!r}")
    ann_set = set(annotators)

    m = doc.get("media") or {}
    for k in ("media_id", "source_video_id", "site_id", "camera_id", "session_id", "recorded_day", "fps",
              "frame_count", "width", "height", "pts_mode"):
        if m.get(k) in (None, ""):
            f.error("E_MEDIA_META", f"$.media.{k}", "eksik")
    for k in ("media_id", "source_video_id", "camera_id", "session_id"):
        if m.get(k) and not ID_RE.match(str(m[k])):
            f.error("E_ID_FORMAT", f"$.media.{k}", "yalnız harf/rakam/_/- (en çok 40)")
    if m.get("site_id") and not SITE_RE.match(str(m["site_id"])):
        f.error("E_ID_FORMAT", "$.media.site_id", "anonim saha kimliği 'site_<kod>' olmalı (ad/adres yazılmaz)")
    if m.get("recorded_day") and not _iso_day(m["recorded_day"]):
        f.error("E_TIMESTAMP", "$.media.recorded_day", "YYYY-MM-DD olmalı")
    if m.get("pts_mode") not in (None, "cfr", "vfr"):
        f.error("E_MEDIA_META", "$.media.pts_mode", "cfr|vfr")
    fps, n = m.get("fps") or 0, m.get("frame_count") or 0
    W, H = m.get("width") or 0, m.get("height") or 0
    for k, v in (("fps", fps), ("frame_count", n), ("width", W), ("height", H)):
        if not isinstance(v, (int, float)) or v <= 0:
            f.error("E_MEDIA_META", f"$.media.{k}", "pozitif sayı olmalı")
    if not isinstance(m.get("scenario_tags", []), list):
        f.error("E_MEDIA_META", "$.media.scenario_tags", "liste olmalı")
    prov = m.get("provenance") or {}
    if not prov.get("source_ref"):
        f.error("E_PROVENANCE", "$.media.provenance.source_ref", "köken referansı eksik")
    auth, lic = m.get("authorization") or {}, m.get("license") or {}
    if auth.get("status") not in AUTHORIZATION:
        f.error("E_AUTHORIZATION", "$.media.authorization.status", f"{AUTHORIZATION} içinden olmalı (eksik izin durumu)")
    if lic.get("status") not in LICENSE or not lic.get("id"):
        f.error("E_LICENSE", "$.media.license", f"id ve status {LICENSE} gerekli")
    authorization_checks(auth, lic, f, "$.media", synthetic_ok)

    frames = doc.get("frames") or []
    if not frames:
        f.error("E_EMPTY", "$.frames", "hiç etiketli kare yok")
    prev_i, prev_t = -1, -1.0
    present_by_frame, track_last_seen = [], {}
    for i, fr in enumerate(frames):
        w = f"$.frames[{i}]"
        fi, pts = fr.get("frame_index"), fr.get("pts_sec")
        if not isinstance(fi, int) or fi < 0 or (n and fi >= n):
            f.error("E_TIMESTAMP", w, f"frame_index geçersiz: {fi!r}")
            continue
        if fi <= prev_i:
            f.error("E_TIMESTAMP", w, "frame_index artan ve tekil olmalı")
        if not isinstance(pts, (int, float)) or pts < 0 or pts <= prev_t:
            f.error("E_TIMESTAMP", w, f"pts_sec >= 0 ve artan olmalı: {pts!r}")
        elif m.get("pts_mode") == "cfr" and fps and abs(pts - fi / fps) > 0.5 / fps:
            f.error("E_TIMESTAMP", w, f"pts_sec={pts} ile frame_index/fps={fi / fps:.3f} tutarsız (cfr)")
        prev_i, prev_t = fi, pts if isinstance(pts, (int, float)) else prev_t
        persons = fr.get("persons")
        if persons is None:
            f.error("E_EMPTY", w, "persons alanı yok (kişi yoksa boş liste yazılır)")
            persons = []
        ids = set()
        for j, p in enumerate(persons):
            _check_person(p, f"{w}.persons[{j}]", W, H, ann_set, f)
            tid = p.get("track_id")
            if tid in ids:
                f.error("E_TRACK", f"{w}.persons[{j}]", f"aynı karede yinelenen track_id {tid}")
            ids.add(tid)
        present_by_frame.append((i, ids))
        _check_cross_association(persons, w, f)
    _check_reentry(frames, present_by_frame, f)
    _check_incidents(doc, frames, fps, n, ann_set, f)
    _check_doc_reviews(doc, ann_set, f)
    return f


def authorization_checks(auth: dict, lic: dict, f: Findings, where: str, synthetic_ok: bool):
    a, l_ = auth.get("status"), lic.get("status")
    if a == "WITHDRAWN":
        f.error("E_WITHDRAWN", f"{where}.authorization", "izin geri çekildi: kayıt veri kümesinden çıkarılmalı")
    elif a in ("PENDING", "REJECTED"):
        f.block("B_AUTHORIZATION", f"{where}.authorization", f"izin durumu {a} (DATA_COLLECTION_BLOCKED)")
    elif a == "APPROVED" and not auth.get("reference"):
        f.error("E_AUTHORIZATION", f"{where}.authorization.reference", "APPROVED için onay referansı gerekli")
    if l_ == "LICENSE_UNVERIFIED":
        f.block("B_LICENSE", f"{where}.license", "LICENSE_UNVERIFIED")
    if (a == "SYNTHETIC") != (l_ == "SYNTHETIC") and a in AUTHORIZATION and l_ in LICENSE:
        f.error("E_AUTHORIZATION", where, "SYNTHETIC izin ve lisans birlikte kullanılır")
    if a == "SYNTHETIC" and not synthetic_ok:
        f.block("B_SYNTHETIC", where, "sentetik kayıt gerçek saha veri kümesinde kullanılamaz")


def _check_person(p, w, W, H, annotators, f: Findings):
    tid = p.get("track_id")
    if not isinstance(tid, str) or not ID_RE.match(tid):
        f.error("E_TRACK", w, "track_id video-yerel kısa kimlik olmalı (kişi kimliği değildir)")
    b = p.get("box")
    box_ok = isinstance(b, list) and valid_box(b)
    if not box_ok:
        f.error("E_BOX_INVALID", f"{w}.box", f"geçersiz kişi kutusu {b!r}")
    elif W and H and (b[0] < -1 or b[1] < -1 or b[2] > W + 1 or b[3] > H + 1):
        f.error("E_BOX_INVALID", f"{w}.box", "kutu kare dışında")
    vis = p.get("visibility")
    for k, allowed in (("visibility", VISIBILITY), ("occlusion", OCCLUSION),
                       ("annotator_confidence", CONFIDENCE), ("person_status", PERSON_STATUSES),
                       ("label_origin", LABEL_ORIGINS)):
        if p.get(k) not in allowed:
            f.error("E_SCHEMA", f"{w}.{k}", f"{allowed} içinden olmalı")
    if not isinstance(p.get("truncated"), bool):
        f.error("E_SCHEMA", f"{w}.truncated", "true/false olmalı")
    pst = p.get("person_status")
    if pst == "NOT_EVALUABLE" and p.get("not_evaluable_reason") not in NOT_EVALUABLE_REASONS:
        f.error("E_STATUS_CONTRADICTION", w, f"NOT_EVALUABLE kişi için not_evaluable_reason {NOT_EVALUABLE_REASONS}")
    for item in ITEMS:
        iw = f"{w}.{item}"
        it = p.get(item)
        if not isinstance(it, dict) or it.get("status") not in STATUSES:
            f.error("E_SCHEMA", iw, f"{{status: {STATUSES}, box}} olmalı")
            continue
        st, ib = it["status"], it.get("box")
        hidden = vis in (HEAD_HIDDEN if item == "helmet" else TORSO_HIDDEN)
        if pst == "NOT_EVALUABLE" and st != "NOT_EVALUABLE":
            f.error("E_STATUS_CONTRADICTION", iw, "değerlendirilemeyen kişide ekipman da NOT_EVALUABLE olmalı")
        if pst == "EVALUABLE" and st == "NOT_EVALUABLE":
            f.error("E_STATUS_CONTRADICTION", iw, "NOT_EVALUABLE yalnız kişi düzeyinde verilir; bölge görünmüyorsa UNKNOWN")
        if hidden and st in ("MISSING", "PRESENT"):
            region = "kafa" if item == "helmet" else "gövde"
            f.error("E_STATUS_CONTRADICTION", iw, f"{region} görünmüyor ({vis}) ama {st}: UNKNOWN olmalı "
                                                  "(görünmeme ihlal değildir)")
        if st == "PRESENT":
            if not (isinstance(ib, list) and valid_box(ib)):
                f.error("E_BOX_INVALID", iw, "PRESENT ekipman için geçerli kutu gerekli")
            elif box_ok and not associated(ib, b):
                f.error("E_ASSOCIATION", iw, "ekipman kutusu bu kişinin kutusu içinde değil (yanlış kişi ilişkisi)")
        elif st == "MISSING" and ib is not None:
            f.error("E_STATUS_CONTRADICTION", iw, "MISSING ekipmanda kutu olamaz")
        elif st in ("UNKNOWN", "NOT_EVALUABLE") and ib is not None:
            f.warn("W_BOX_ON_UNKNOWN", iw, f"{st} durumunda kutu var; ikinci göz önerilir")
    if p.get("label_origin") == "model_prelabel" and (p.get("review") or {}).get("status") not in ("agreed",
                                                                                                   "adjudicated"):
        f.error("E_MODEL_AS_GT", w, "model ön etiketi insan onayı olmadan ground-truth sayılamaz")
    _check_review(p.get("review"), is_critical(p), annotators, f, f"{w}.review")


def _check_cross_association(persons, w, f: Findings):
    """Kalabalık sahne: ekipman merkezi kendi kişisine yalnız pay (margin) sayesinde, başka bir kişinin kutusuna ise
    doğrudan düşüyorsa ilişki belirsizdir -> ikinci göz (uyarı). Kendi kişisine hiç düşmüyorsa _check_person hata verir."""
    boxes = [q.get("box") if isinstance(q.get("box"), list) and valid_box(q["box"]) else None for q in persons]
    for j, p in enumerate(persons):
        for item in ITEMS:
            ib = (p.get(item) or {}).get("box")
            if boxes[j] is None or not (isinstance(ib, list) and valid_box(ib)) or associated(ib, boxes[j], 0.0):
                continue
            if any(k != j and b is not None and associated(ib, b, 0.0) for k, b in enumerate(boxes)):
                f.warn("W_ASSOCIATION_AMBIGUOUS", f"{w}.persons[{j}].{item}",
                       "ekipman başka bir kişinin kutusunda; ilişkiyi ikinci göz doğrulasın")


def _check_reentry(frames, present_by_frame, f: Findings):
    """Bir track etiketli karelerde görünüp kaybolup yeniden görünürse ilk dönüş kaydında reentry=true olmalı."""
    seen, last_idx = set(), {}
    for pos, (i, ids) in enumerate(present_by_frame):
        persons = frames[i].get("persons") or []
        for p in persons:
            tid = p.get("track_id")
            if tid is None:
                continue
            gap = tid in last_idx and last_idx[tid] < pos - 1
            if p.get("reentry") and tid not in seen:
                f.error("E_TRACK_REENTRY", f"$.frames[{i}]", f"{tid}: ilk görünümde reentry=true olamaz")
            if gap and not p.get("reentry"):
                f.error("E_TRACK_REENTRY", f"$.frames[{i}]", f"{tid}: etiketli karelerde kaybolup döndü; reentry=true "
                                                             "işaretli değil (aynı kişi olduğundan emin değilseniz yeni "
                                                             "track_id)")
            seen.add(tid)
            last_idx[tid] = pos


def _check_incidents(doc, frames, fps, n, annotators, f: Findings):
    dur = n / fps if fps and n else None
    track_frames = {}
    for fr in frames:
        for p in fr.get("persons") or []:
            track_frames.setdefault(p.get("track_id"), []).append((fr.get("pts_sec"), p))
    ids = set()
    for k, inc in enumerate(doc.get("incidents") or []):
        w = f"$.incidents[{k}]"
        iid = inc.get("incident_id")
        if not iid or iid in ids or not ID_RE.match(str(iid)):
            f.error("E_INCIDENT", w, "incident_id eksik/yinelenen/geçersiz")
        ids.add(iid)
        item = VIOLATIONS.get(inc.get("violation_type"))
        if item is None:
            f.error("E_INCIDENT", w, f"violation_type {tuple(VIOLATIONS)} içinden olmalı")
            continue
        tid = inc.get("track_id")
        if tid not in track_frames:
            f.error("E_TRACK", w, f"track_id {tid!r} karelerde yok")
            continue
        s, e = inc.get("start_sec"), inc.get("end_sec")
        if not isinstance(s, (int, float)) or s < 0:
            f.error("E_INCIDENT_SPAN", w, "start_sec geçersiz")
            continue
        if e is not None and (not isinstance(e, (int, float)) or e <= s):
            f.error("E_INCIDENT_SPAN", w, "end_sec > start_sec olmalı (sürüyorsa null)")
            continue
        if dur is not None and (s > dur or (e is not None and e > dur + 1e-6)):
            f.error("E_INCIDENT_SPAN", w, f"video süresini ({dur:.2f} sn) aşıyor")
            continue
        if inc.get("uncertainty") not in UNCERTAINTY:
            f.error("E_SCHEMA", f"{w}.uncertainty", f"{UNCERTAINTY} içinden olmalı")
        hi = float("inf") if e is None else e
        inside = [p for t, p in track_frames[tid] if isinstance(t, (int, float)) and s <= t <= hi]
        if not any((p.get(item) or {}).get("status") == "MISSING" for p in inside):
            f.error("E_INCIDENT_SPAN", w, f"aralıkta bu kişi için {item}=MISSING etiketli kare yok (zaman uyuşmazlığı)")
        if any((p.get(item) or {}).get("status") == "PRESENT" for p in inside):
            f.warn("W_INCIDENT_PRESENT", w, f"aralık içinde {item}=PRESENT kare var; sınırları ikinci göz kontrol etsin")
        _check_review(inc.get("review"), True, annotators, f, f"{w}.review")


def _check_doc_reviews(doc, annotators, f: Findings):
    ver = doc.get("annotation_version")
    reviews = doc.get("reviews") or []
    approved = []
    for k, r in enumerate(reviews):
        w = f"$.reviews[{k}]"
        if r.get("status") not in DOC_REVIEW:
            f.error("E_REVIEW_INVALID", w, f"status {DOC_REVIEW} içinden olmalı")
        if not r.get("reviewer") or not PERSON_ROLE_RE.match(r["reviewer"]) or r["reviewer"] in annotators:
            f.error("E_REVIEW_NOT_INDEPENDENT", w, "bağımsız, anonim reviewer gerekli")
        if not _iso_utc(r.get("reviewed_at")):
            f.error("E_TIMESTAMP", w, "reviewed_at UTC ISO-8601 (…Z) olmalı")
        rv = r.get("annotation_version")
        if not isinstance(rv, int) or (isinstance(ver, int) and rv > ver):
            f.error("E_VERSION_MISMATCH", w, f"incelenen sürüm {rv!r}, belge sürümü {ver!r}")
        if r.get("status") == "changes_requested" and not r.get("disagreement_reason"):
            f.error("E_REVIEW_INVALID", w, "changes_requested için disagreement_reason gerekli")
        if r.get("status") == "approved":
            approved.append(rv)
    if not approved:
        f.block("B_REVIEW_MISSING", "$.reviews", "belge düzeyinde bağımsız onay yok")
    elif isinstance(ver, int) and max(a for a in approved if isinstance(a, int)) != ver:
        f.block("B_REVIEW_STALE", "$.reviews", f"son onay sürüm {max(approved)}; belge sürüm {ver} — yeniden inceleme")


# ---------------------------------------------------------------- FAZ 8 değerlendirme hattına dönüştürme
_EVAL_STATUS = {"PRESENT": "present", "MISSING": "missing", "UNKNOWN": "unknown", "NOT_EVALUABLE": "unknown"}
_EVAL_REVIEW = {"agreed": "ok", "adjudicated": "adjudicated"}


def to_eval_video(doc: dict, split: str) -> dict:
    """`ppe-annotation/1` -> `ppe-eval-video/1` (evaluation.pipeline.evaluate_against_gt girdisi).
    Yalnız hatasız ve engelsiz (PASS) belge dönüştürülür; NOT_EVALUABLE, FAZ 8 metriklerinde 'unknown' gibi
    değerlendirme dışıdır ama özgün durum `gt_status_v1a3` alanında korunur (kapsam raporu için)."""
    fnd = validate_document(doc)
    if fnd.status != "PASS":
        raise ValueError(f"belge dönüştürülemez ({fnd.status}): "
                         + "; ".join(x["code"] for x in (fnd.errors or fnd.blockers)[:5]))
    if split not in ("train", "val", "test"):
        raise ValueError("split train|val|test")
    m = doc["media"]
    frames = []
    for fr in doc["frames"]:
        persons = []
        for p in fr["persons"]:
            q = {"track_id": p["track_id"], "box": list(p["box"]), "visibility": p["visibility"],
                 "review": _EVAL_REVIEW.get(p["review"]["status"], "ok"),
                 "gt_status_v1a3": {i: p[i]["status"] for i in ITEMS}}
            for item in ITEMS:
                q[item] = _EVAL_STATUS[p[item]["status"]]
                q[f"{item}_box"] = list(p[item]["box"]) if p[item]["status"] == "PRESENT" else None
            persons.append(q)
        frames.append({"frame": fr["frame_index"], "t": fr["pts_sec"], "persons": persons})
    return {"schema": EVAL_SCHEMA,
            "video": {"video_id": m["media_id"], "camera_id": m["camera_id"], "site_id": m["site_id"],
                      "recorded_day": m["recorded_day"], "fps": m["fps"], "frame_count": m["frame_count"],
                      "width": m["width"], "height": m["height"], "split": split,
                      "conditions": list(m.get("scenario_tags", [])), "license": m["license"]["id"],
                      "consent_ref": m["authorization"].get("reference") or m["authorization"]["status"]},
            "annotators": list(doc["annotators"]), "frames": frames,
            "incidents": [{"incident_id": i["incident_id"], "track_id": i["track_id"],
                           "item": VIOLATIONS[i["violation_type"]], "start": float(i["start_sec"]),
                           "end": None if i.get("end_sec") is None else float(i["end_sec"]),
                           "certainty": i["uncertainty"]} for i in doc.get("incidents") or []]}
