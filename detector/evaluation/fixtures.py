"""Deterministik SENTETİK veri kümesi fikstürleri (FAZ 10-A.3). Gerçek görüntü, gerçek kişi, gerçek saha YOKTUR:
medya dosyaları birkaç baytlık işaretli metindir; kutular ve durumlar elle yazılmış senaryolardır.
Bu fikstürler veri hattının doğruluğunu sınar; model doğruluğu veya saha verisi ölçümü DEĞİLDİR.

  python -m evaluation data-fixtures --out <dizin> [--scenario valid]   # dizine örnek veri kümesi yazar"""
import copy
import json
from pathlib import Path

from . import datamanifest as dm

FPS, FRAMES, W, H = 25.0, 250, 1280, 720
CREATED = "2026-11-02T00:00:00Z"
POLICY = {"group_by": "camera_id", "seed": "fixture-seed-1", "ratios": {"train": 0.5, "val": 0.25, "test": 0.25}}
DHASH = {"m001": "0000000000000000", "m002": "ffffffffffffffff", "m003": "00000000ffffffff",
         "m004": "ffffffff00000000", "m005": "0f0f0f0f0f0f0f0f"}
A_BOX, A_HELMET, A_VEST = [100, 100, 220, 460], [130, 95, 190, 145], [110, 200, 210, 340]
B_BOX, B_HELMET, B_VEST = [600, 120, 720, 480], [630, 115, 690, 165], [610, 220, 710, 360]
C_BOX = [0, 200, 40, 520]
SYN_AUTH = {"status": "SYNTHETIC", "reference": None}
SYN_LIC = {"id": "synthetic-fixture", "status": "SYNTHETIC"}
FIXTURE_AUTH_REF = "FIXTURE-NOT-A-REAL-APPROVAL"


def _item(status, box=None):
    return {"status": status, "box": list(box) if box else None}


def person(tid, box, helmet, vest, *, visibility="full", occlusion="none", truncated=False, review=None,
           person_status="EVALUABLE", reason=None, confidence="high", origin="human", reentry=False):
    p = {"track_id": tid, "box": list(box), "visibility": visibility, "occlusion": occlusion, "truncated": truncated,
         "annotator_confidence": confidence, "person_status": person_status, "label_origin": origin,
         "helmet": helmet, "vest": vest}
    if reason:
        p["not_evaluable_reason"] = reason
    if reentry:
        p["reentry"] = True
    critical = any(x["status"] != "PRESENT" for x in (helmet, vest)) or confidence == "low" or origin != "human"
    p["review"] = review or ({"status": "agreed", "reviewer": "rev_B"} if critical else {"status": "pending"})
    return p


def ok_person(tid="t1", box=A_BOX, hb=A_HELMET, vb=A_VEST, **kw):
    return person(tid, box, _item("PRESENT", hb), _item("PRESENT", vb), **kw)


def document(media_id, *, camera, session, source_video, site="site_s01", tags=("daylight",), frames=None,
             incidents=(), version=1):
    return {
        "schema_version": "ppe-annotation/1", "annotation_version": version, "dataset_id": "ppe-fixture",
        "origin": "human", "annotators": ["ann_A"],
        "media": {"media_id": media_id, "source_video_id": source_video, "site_id": site, "camera_id": camera,
                  "session_id": session, "recorded_day": "2026-01-15", "fps": FPS, "frame_count": FRAMES,
                  "width": W, "height": H, "pts_mode": "cfr", "scenario_tags": list(tags),
                  "provenance": {"source_ref": f"synthetic:fixture/{media_id}", "collected_by_role": "fixture"},
                  "authorization": dict(SYN_AUTH), "license": dict(SYN_LIC)},
        "frames": frames or [], "incidents": list(incidents),
        "reviews": [{"annotation_version": version, "reviewer": "rev_B", "status": "approved",
                     "reviewed_at": "2026-11-02T00:00:00Z", "disagreement_reason": None, "resolution": None}]}


def frame(idx, persons):
    return {"frame_index": idx, "pts_sec": round(idx / FPS, 4), "persons": persons}


def incident(iid, track, vtype, start, end=None, uncertainty="certain"):
    return {"incident_id": iid, "track_id": track, "violation_type": vtype, "start_sec": start, "end_sec": end,
            "uncertainty": uncertainty, "review": {"status": "agreed", "reviewer": "rev_B"}}


def base_documents() -> dict:
    """4 medya, 4 kamera, tek anonim saha. m001 uygun; m002 baret eksik; m003 yelek eksik; m004 (test) kafa
    örtülü (UNKNOWN), kadraj kenarındaki değerlendirilemeyen kişi ve doğru eşlenmiş iki kişi."""
    miss_h = person("t1", A_BOX, _item("MISSING"), _item("PRESENT", A_VEST))
    miss_v = person("t1", A_BOX, _item("PRESENT", A_HELMET), _item("MISSING"))
    hidden = person("t1", A_BOX, _item("UNKNOWN"), _item("PRESENT", A_VEST), visibility="head_hidden",
                    occlusion="partial")
    edge = person("t3", C_BOX, _item("NOT_EVALUABLE"), _item("NOT_EVALUABLE"), visibility="partial", truncated=True,
                  person_status="NOT_EVALUABLE", reason="truncated")
    return {
        "m001": document("m001", camera="cam_a", session="sess_a1", source_video="sv_a1", frames=[
            frame(0, [ok_person()]), frame(50, [ok_person()]), frame(100, [ok_person()])]),
        "m002": document("m002", camera="cam_b", session="sess_b1", source_video="sv_b1", frames=[
            frame(0, [ok_person()]), frame(50, [copy.deepcopy(miss_h)]), frame(100, [copy.deepcopy(miss_h)])],
            incidents=[incident("i1", "t1", "missing_helmet", 2.0)]),
        "m003": document("m003", camera="cam_c", session="sess_c1", source_video="sv_c1", tags=("low_light",), frames=[
            frame(0, [ok_person()]), frame(50, [copy.deepcopy(miss_v)]), frame(100, [ok_person()])],
            incidents=[incident("i2", "t1", "missing_vest", 2.0, 3.0)]),
        "m004": document("m004", camera="cam_d", session="sess_d1", source_video="sv_d1",
                         tags=("occlusion", "edge_of_frame", "multi_person"), frames=[
            frame(0, [ok_person(), ok_person("t2", B_BOX, B_HELMET, B_VEST), copy.deepcopy(edge)]),
            frame(50, [copy.deepcopy(hidden), ok_person("t2", B_BOX, B_HELMET, B_VEST)])]),
    }


SPLIT = {"m001": "train", "m002": "train", "m003": "val", "m004": "test", "m005": "train"}


def media_bytes(media_id: str) -> bytes:
    return f"PPE-SYNTHETIC-MEDIA v1 {media_id}\n".encode()


def write_dataset(root: Path, docs: dict, *, splits=None, classification="SYNTHETIC", media=None, freeze=True,
                  dhash=None) -> dict:
    """Dosyaları yazar, manifest üretir (ve test kümesini dondurur), root/manifest.json'a yazar."""
    root = Path(root)
    (root / "media").mkdir(parents=True, exist_ok=True)
    (root / "annotations").mkdir(parents=True, exist_ok=True)
    splits, media, dhash = splits or SPLIT, media or {}, DHASH if dhash is None else dhash
    items = []
    for mid, doc in sorted(docs.items()):
        (root / "media" / f"{mid}.bin").write_bytes(media.get(mid, media_bytes(mid)))
        (root / "annotations" / f"{mid}.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1,
                                                                     sort_keys=True), encoding="utf-8")
        items.append({"media_id": mid, "media_path": f"media/{mid}.bin", "annotation_path": f"annotations/{mid}.json",
                      "split": splits[mid], "dhash": [dhash[mid]] if dhash.get(mid) else None})
    for it in items:
        if it["dhash"] is None:
            del it["dhash"]
    man = dm.build_manifest(root, items, dataset_id="ppe-fixture", dataset_version="1.0.0", created_at=CREATED,
                            data_classification=classification, split_policy=POLICY,
                            description="SENTETİK fikstür — gerçek veri değildir", compute_dhash=False)
    if freeze:
        man = dm.freeze_test(man, frozen_at=CREATED, approved_by_role="fixture")
    save(root, man)
    return man


def save(root: Path, man: dict):
    (Path(root) / "manifest.json").write_text(json.dumps(man, ensure_ascii=False, indent=1, sort_keys=True),
                                              encoding="utf-8")


def reseal(man: dict) -> dict:
    """Elle değiştirilen manifestin kayıt özetini yeniden hesaplar (yalnız tek bir bozulmayı yalıtmak için)."""
    man["content_sha256"] = dm.canonical_sha(man["items"])
    return man


def _real(docs, lic_status="VERIFIED", auth_status="APPROVED"):
    for d in docs.values():
        d["media"]["authorization"] = {"status": auth_status, "reference": FIXTURE_AUTH_REF}
        d["media"]["license"] = {"id": "fixture-license", "status": lic_status}


def _p(docs, mid, fi, pi):
    return docs[mid]["frames"][fi]["persons"][pi]


# Etiket düzeyi senaryolar: (etiket belgesi değişikliği, -, sınıflandırma, beklenen durum, beklenen kodlar)
SCENARIOS = {
    "valid": (None, None, "SYNTHETIC", "PASS", set()),
    "occluded_head_marked_missing": (
        lambda d: _p(d, "m004", 1, 0)["helmet"].update(status="MISSING"), None, "SYNTHETIC", "FAIL",
        {"E_STATUS_CONTRADICTION"}),
    "edge_person_without_reason": (
        lambda d: _p(d, "m004", 0, 2).pop("not_evaluable_reason"), None, "SYNTHETIC", "FAIL",
        {"E_STATUS_CONTRADICTION"}),
    "evaluable_person_item_not_evaluable": (
        lambda d: _p(d, "m001", 0, 0).update(helmet=_item("NOT_EVALUABLE"),
                                             review={"status": "agreed", "reviewer": "rev_B"}),
        None, "SYNTHETIC", "FAIL", {"E_STATUS_CONTRADICTION"}),
    "wrong_person_association": (
        lambda d: _p(d, "m004", 0, 0)["helmet"].update(box=list(B_HELMET)), None, "SYNTHETIC", "FAIL",
        {"E_ASSOCIATION"}),
    "invalid_bbox": (
        lambda d: _p(d, "m001", 0, 0).update(box=[220, 100, 100, 460]), None, "SYNTHETIC", "FAIL", {"E_BOX_INVALID"}),
    "track_reentry_unflagged": (
        lambda d: d["m002"]["frames"][1].update(persons=[]), None, "SYNTHETIC", "FAIL", {"E_TRACK_REENTRY"}),
    "incident_timestamp_mismatch": (
        lambda d: d["m002"]["incidents"][0].update(start_sec=0.0, end_sec=1.0), None, "SYNTHETIC", "FAIL",
        {"E_INCIDENT_SPAN"}),
    "model_prelabel_unreviewed": (
        lambda d: _p(d, "m001", 0, 0).update(label_origin="model_prelabel", review={"status": "pending"}),
        None, "SYNTHETIC", "FAIL", {"E_MODEL_AS_GT", "B_REVIEW_MISSING"}),
    "privacy_field": (
        lambda d: _p(d, "m001", 0, 0).update(name="X"), None, "SYNTHETIC", "FAIL", {"E_PRIVACY_FIELD"}),
    "critical_review_pending": (
        lambda d: _p(d, "m002", 1, 0).update(review={"status": "pending"}), None, "SYNTHETIC", "BLOCKED",
        {"B_REVIEW_MISSING"}),
    "dispute_unresolved": (
        lambda d: _p(d, "m002", 1, 0).update(review={"status": "disputed", "reviewer": "rev_B",
                                                     "reason": "baret mi şapka mı belirsiz"}),
        None, "SYNTHETIC", "BLOCKED", {"B_DISPUTE_UNRESOLVED"}),
    "review_stale_after_edit": (
        lambda d: d["m001"].update(annotation_version=2), None, "SYNTHETIC", "BLOCKED", {"B_REVIEW_STALE"}),
    "missing_license_approval": (
        lambda d: _real(d, lic_status="LICENSE_UNVERIFIED"), None, "PERSONAL_DATA", "BLOCKED", {"B_LICENSE"}),
    "authorization_pending": (
        lambda d: _real(d, auth_status="PENDING"), None, "PERSONAL_DATA", "BLOCKED", {"B_AUTHORIZATION"}),
    "authorization_withdrawn": (
        lambda d: _real(d, auth_status="WITHDRAWN"), None, "PERSONAL_DATA", "FAIL", {"E_WITHDRAWN"}),
    "license_field_missing": (
        lambda d: d["m001"]["media"].pop("license"), None, "SYNTHETIC", "FAIL", {"E_LICENSE", "E_AUTHORIZATION"}),
}


def _dup_same_split(root, docs):
    docs["m005"] = document("m005", camera="cam_e", session="sess_e1", source_video="sv_e1",
                            frames=[frame(0, [ok_person()])])
    return {"media": {"m005": media_bytes("m001")}}


def _dup_across(root, docs):
    docs["m005"] = document("m005", camera="cam_e", session="sess_e1", source_video="sv_e1",
                            frames=[frame(0, [ok_person()])])
    return {"media": {"m005": media_bytes("m004")}}


def _leak(root, docs):
    docs["m004"]["media"].update(camera_id="cam_a", session_id="sess_a1", source_video_id="sv_a1")
    return {}


def _near(root, docs):
    return {"dhash": {**DHASH, "m004": "0000000000000003"}}          # m001'e 2 bit uzaklıkta


def _items(fn):
    def mut(man):
        fn(man)
        reseal(man)
    return mut


BUILD_SCENARIOS = {          # medya/ayrım düzeyi senaryolar: (yazım öncesi değişiklik, beklenen durum, kodlar)
    "duplicate_source_frame": (_dup_same_split, "FAIL", {"E_DUPLICATE_MEDIA"}),
    "duplicate_across_splits": (_dup_across, "FAIL", {"E_LEAKAGE_DUPLICATE"}),
    "train_test_leakage": (_leak, "FAIL", {"E_LEAKAGE"}),
    "near_duplicate_across_splits": (_near, "FAIL", {"E_NEAR_DUPLICATE"}),
}

MANIFEST_SCENARIOS = {       # yazım sonrası manifest bozulmaları
    "annotation_version_mismatch": (_items(lambda m: m["items"][0].update(annotation_version=2)), "FAIL",
                                    {"E_VERSION_MISMATCH"}),
    "missing_source_hash": (_items(lambda m: m["items"][0].pop("media_sha256")), "FAIL", {"E_HASH_MISSING"}),
    "path_traversal": (_items(lambda m: m["items"][0].update(media_path="../outside.bin")), "FAIL", {"E_PATH"}),
    "absolute_path": (_items(lambda m: m["items"][0].update(media_path="/etc/hosts")), "FAIL", {"E_PATH"}),
    "invalid_split": (_items(lambda m: m["items"][0].update(split="holdout")), "FAIL",
                      {"E_SPLIT", "E_MANIFEST_TAMPERED"}),
    "wrong_class_map": (lambda m: m["label_map"].update(helmet=1), "FAIL", {"E_CLASS_MAP"}),
    "manifest_counts_tampered": (lambda m: m["label_counts"]["train"].update(persons=999), "FAIL",
                                 {"E_MANIFEST_TAMPERED"}),
    "manifest_items_tampered": (lambda m: m["items"][0].update(source_ref="synthetic:edited"), "FAIL",
                                {"E_MANIFEST_TAMPERED"}),
    "test_changed_after_freeze": (_items(lambda m: next(i for i in m["items"] if i["split"] == "test").update(
        source_ref="synthetic:fixture/m004-edited")), "FAIL", {"E_TEST_CHANGED"}),
    "test_not_frozen": (lambda m: m.update(test_freeze={"frozen": False}), "BLOCKED", {"B_TEST_NOT_FROZEN"}),
}


def all_scenarios() -> list[str]:
    return sorted(set(SCENARIOS) | set(BUILD_SCENARIOS) | set(MANIFEST_SCENARIOS))


def build_scenario(name: str, root: Path) -> tuple[dict, str, set]:
    """Senaryoyu root'a yazar. Döner: (manifest, beklenen durum, beklenen hata/engel kodlarının TAM kümesi;
    uyarılar dahil değildir)."""
    docs = base_documents()
    kw = {}
    if name in SCENARIOS:
        doc_mut, _, cls, status, codes = SCENARIOS[name]
        if doc_mut:
            doc_mut(docs)
        kw["classification"] = cls
        man = write_dataset(root, docs, **kw)
        return man, status, codes
    if name in BUILD_SCENARIOS:
        fn, status, codes = BUILD_SCENARIOS[name]
        man = write_dataset(root, docs, **fn(root, docs))
        return man, status, codes
    if name in MANIFEST_SCENARIOS:
        fn, status, codes = MANIFEST_SCENARIOS[name]
        man = write_dataset(root, docs)
        fn(man)
        save(root, man)
        return man, status, codes
    raise KeyError(name)


def agreement_pair() -> tuple[dict, dict]:
    """m002 için iki bağımsız etiket: B, t=4.0 karesinde bareti UNKNOWN demiş ve kutular birkaç piksel kaymış."""
    a = base_documents()["m002"]
    b = copy.deepcopy(a)
    b["annotators"] = ["ann_C"]
    for fr in b["frames"]:
        for p in fr["persons"]:
            p["box"] = [p["box"][0] + 4, p["box"][1] + 2, p["box"][2] + 4, p["box"][3] + 2]
    b["frames"][2]["persons"][0]["helmet"] = _item("UNKNOWN")
    b["frames"][2]["persons"][0]["visibility"] = "head_hidden"
    return a, b
