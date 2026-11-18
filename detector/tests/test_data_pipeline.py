"""FAZ 10-A.3 veri hattı: etiket sözleşmesi (ppe-annotation/1), manifest (ppe-dataset-manifest/1), deterministik
ayrım ve dondurma, sızıntı/yakın kopya, inceleme ve izin kuralları, uyum (IAA), FAZ 8 değerlendirmesine dönüştürme,
gizlilik ve yol güvenliği, rapor determinizmi, CLI. Tümü SENTETİK; GPU/model/gerçek veri kullanılmaz."""
import copy
import hashlib
import json
import os
import random

import pytest

from evaluation import agreement, cli, datacontract as dc, datamanifest as dm, fixtures as fx
from evaluation import annotations as ann
from evaluation.pipeline import evaluate_against_gt

SCHEMAS = dc.__file__.rsplit(os.sep, 1)[0] + os.sep + "schemas"


def _validate(root):
    return dm.validate_dataset(root, json.loads((root / "manifest.json").read_text(encoding="utf-8")))


def _tree_digest(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


# ---------------------------------------------------------------- senaryolar (beklenen sonuçlar deterministik)
@pytest.mark.parametrize("name", fx.all_scenarios())
def test_scenario_expected_status_and_exact_codes(tmp_path, name):
    _, status, codes = fx.build_scenario(name, tmp_path)
    rep = _validate(tmp_path)
    got = {c for c in rep["codes"] if c[0] in "EB"}
    assert (rep["status"], got) == (status, codes), json.dumps(rep["errors"] + rep["blockers"], ensure_ascii=False)
    assert rep["real_site_dataset"] == "BLOCKED"                 # sentetik ya da hatalı: asla gerçek veri kümesi


def test_valid_fixture_covers_required_label_cases(tmp_path):
    man = fx.write_dataset(tmp_path, fx.base_documents())
    c = man["label_counts"]
    assert c["train"]["helmet_MISSING"] >= 1 and c["train"]["incident_missing_helmet"] == 1     # baret eksik
    assert c["val"]["vest_MISSING"] == 1 and c["val"]["incident_missing_vest"] == 1           # yelek eksik
    assert c["test"]["helmet_UNKNOWN"] == 1                                                    # kafa örtülü
    assert c["test"]["person_NOT_EVALUABLE"] == 1 and c["test"]["helmet_NOT_EVALUABLE"] == 1   # kadraj kenarı
    assert c["test"]["persons"] == 5 and c["train"]["helmet_PRESENT"] >= 4                     # çok kişi, uygun
    assert man["scenario_distribution"]["test"] == {"edge_of_frame": 1, "multi_person": 1, "occlusion": 1}


# ---------------------------------------------------------------- değişmezlik ve determinizm
def test_validator_never_modifies_files(tmp_path):
    fx.build_scenario("invalid_bbox", tmp_path)
    before = _tree_digest(tmp_path)
    _validate(tmp_path)
    assert _tree_digest(tmp_path) == before


def test_manifest_and_report_are_deterministic(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    fx.write_dataset(a, fx.base_documents())
    fx.write_dataset(b, fx.base_documents())
    assert (a / "manifest.json").read_bytes() == (b / "manifest.json").read_bytes()
    ra, rb = _validate(a), _validate(b)
    assert dm.render_json(ra) == dm.render_json(rb) and dm.render_markdown(ra) == dm.render_markdown(rb)
    assert not {"generated_at", "created_at", "timestamp"} & set(ra)                    # rapor zaman damgası taşımaz


def test_item_order_does_not_change_manifest(tmp_path):
    docs = fx.base_documents()
    m1 = fx.write_dataset(tmp_path / "x", docs)
    rev = dict(reversed(list(docs.items())))
    m2 = fx.write_dataset(tmp_path / "y", rev)
    assert m1["content_sha256"] == m2["content_sha256"]


# ---------------------------------------------------------------- izin / gerçek veri durumu
def test_real_dataset_ready_only_with_approval_license_and_review(tmp_path):
    docs = fx.base_documents()
    fx._real(docs)                                     # sahte onay referansı: yalnız mantık testi, gerçek onay değil
    fx.write_dataset(tmp_path, docs, classification="PERSONAL_DATA")
    rep = _validate(tmp_path)
    assert rep["status"] == "PASS" and rep["real_site_dataset"] == "READY"
    docs2 = fx.base_documents()
    fx._real(docs2)
    fx.write_dataset(tmp_path / "nophash", docs2, classification="PERSONAL_DATA", dhash={})
    rep2 = _validate(tmp_path / "nophash")
    assert rep2["status"] == "BLOCKED" and "B_NO_PHASH" in rep2["codes"]       # yakın kopya denetlenemedi


def test_synthetic_record_blocked_in_real_document():
    doc = fx.base_documents()["m001"]
    f = dc.validate_document(doc, synthetic_ok=False)
    assert f.status == "BLOCKED" and {b["code"] for b in f.blockers} == {"B_SYNTHETIC"}


def test_model_predictions_are_not_ground_truth():
    doc = fx.base_documents()["m001"]
    doc["origin"] = "model"
    assert "E_MODEL_AS_GT" in {e["code"] for e in dc.validate_document(doc).errors}


def test_privacy_fields_rejected_anywhere_and_ids_must_be_anonymous():
    doc = fx.base_documents()["m002"]
    doc["incidents"][0]["review"]["face_embedding"] = [0.1]
    doc["media"]["site_id"] = "Ankara Kuzey Şantiyesi"
    doc["annotators"] = ["ayse.yilmaz@example.com"]
    codes = {e["code"] for e in dc.validate_document(doc).errors}
    assert {"E_PRIVACY_FIELD", "E_ID_FORMAT"} <= codes


def test_unknown_and_not_evaluable_are_distinct():
    doc = fx.base_documents()["m004"]
    edge, hidden = doc["frames"][0]["persons"][2], doc["frames"][1]["persons"][0]
    assert (edge["person_status"], edge["helmet"]["status"]) == ("NOT_EVALUABLE", "NOT_EVALUABLE")
    assert (hidden["person_status"], hidden["helmet"]["status"]) == ("EVALUABLE", "UNKNOWN")
    bad = copy.deepcopy(doc)
    bad["frames"][1]["persons"][0]["helmet"]["status"] = "NOT_EVALUABLE"       # bölge görünmüyor -> UNKNOWN olmalı
    assert "E_STATUS_CONTRADICTION" in {e["code"] for e in dc.validate_document(bad).errors}
    ev = dc.to_eval_video(doc, "test")
    ps = ev["frames"][0]["persons"][2]
    assert ps["helmet"] == "unknown" and ps["gt_status_v1a3"]["helmet"] == "NOT_EVALUABLE"   # ayrım korunur


def test_timestamp_and_frame_rules():
    doc = fx.base_documents()["m001"]
    doc["frames"][1]["pts_sec"] = 3.0                  # cfr: 50/25 = 2.0 olmalı
    doc["frames"][2]["frame_index"] = 300              # frame_count 250
    errs = [e for e in dc.validate_document(doc).errors if e["code"] == "E_TIMESTAMP"]
    assert len(errs) == 2


def test_adjudication_requires_independent_adjudicator_and_resolution():
    doc = fx.base_documents()["m002"]
    p = doc["frames"][1]["persons"][0]
    p["review"] = {"status": "adjudicated", "adjudicator": "ann_A"}            # etiketleyici kendini hakemleyemez
    assert "E_REVIEW_NOT_INDEPENDENT" in {e["code"] for e in dc.validate_document(doc).errors}
    p["review"] = {"status": "adjudicated", "adjudicator": "adj_C", "resolution": "kask görünür; MISSING doğru"}
    assert dc.validate_document(doc).status == "PASS"


# ---------------------------------------------------------------- ayrım (split) ve dondurma
def _entries(n_cams=9, per=4):
    return [{"media_id": f"m{c:02d}{k}", "camera_id": f"cam_{c}"} for c in range(n_cams) for k in range(per)]


def test_split_is_deterministic_grouped_and_order_independent():
    ratios = {"train": 0.6, "val": 0.2, "test": 0.2}
    e = _entries()
    a = dm.assign_splits(e, group_by="camera_id", seed="s1", ratios=ratios)
    shuffled = e[:]
    random.Random(3).shuffle(shuffled)
    assert dm.assign_splits(shuffled, group_by="camera_id", seed="s1", ratios=ratios) == a
    by_cam = {}
    for x in e:
        by_cam.setdefault(x["camera_id"], set()).add(a[x["media_id"]])
    assert all(len(s) == 1 for s in by_cam.values())                         # kamera tek split'te
    counts = {s: sum(1 for v in a.values() if v == s) for s in dm.SPLITS}
    assert counts == {"train": 20, "val": 8, "test": 8}
    assert dm.assign_splits(e, group_by="camera_id", seed="s2", ratios=ratios) != a


def test_split_refuses_unknown_group_and_bad_ratios():
    with pytest.raises(ValueError):
        dm.assign_splits([{"media_id": "m1"}], group_by="camera_id", seed="s", ratios={"train": 1, "val": 0, "test": 0})
    with pytest.raises(ValueError):
        dm.assign_splits(_entries(), group_by="camera_id", seed="s", ratios={"train": 0.9, "val": 0.2, "test": 0})


def test_freeze_detects_test_change_only(tmp_path):
    man = fx.write_dataset(tmp_path, fx.base_documents())
    sha = man["test_freeze"]["test_items_sha256"]
    other = copy.deepcopy(man)
    next(i for i in other["items"] if i["split"] == "train")["source_ref"] = "synthetic:changed"
    assert dm.test_items_sha(other["items"]) == sha                # train değişikliği test özetini etkilemez
    next(i for i in other["items"] if i["split"] == "test")["split"] = "val"
    assert dm.test_items_sha(other["items"]) != sha


# ---------------------------------------------------------------- yol güvenliği
@pytest.mark.parametrize("rel", ["../x", "a/../../x", "/etc/passwd", "C:/Windows/x", "a\\b", "", "./a", None])
def test_safe_path_rejects_escape(tmp_path, rel):
    assert dm.safe_path(tmp_path, rel)[0] is None


def test_safe_path_rejects_symlink(tmp_path):
    (tmp_path / "real.bin").write_bytes(b"x")
    try:
        os.symlink(tmp_path / "real.bin", tmp_path / "link.bin")
    except (OSError, NotImplementedError):
        pytest.skip("sembolik bağ oluşturulamadı (Windows yetkisi)")
    assert dm.safe_path(tmp_path, "link.bin")[0] is None
    assert dm.safe_path(tmp_path, "real.bin")[0] is not None


# ---------------------------------------------------------------- algısal hash (gerçek görüntü değil; üretilmiş desen)
def test_media_dhash_on_generated_images(tmp_path):
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    grad = np.tile(np.arange(0, 256, 4, dtype=np.uint8), (48, 1))
    cv2.imwrite(str(tmp_path / "a.png"), grad)
    cv2.imwrite(str(tmp_path / "b.png"), cv2.resize(grad, (128, 96)))           # yeniden boyutlandırılmış kopya
    cv2.imwrite(str(tmp_path / "c.png"), grad[:, ::-1].copy())                  # ters desen
    a, b, c = (dm.media_dhash(tmp_path / f"{x}.png")[0] for x in "abc")
    from evaluation.dataset import hamming
    assert hamming(int(a, 16), int(b, 16)) <= 4 < hamming(int(a, 16), int(c, 16))


# ---------------------------------------------------------------- uyum (IAA)
def test_agreement_separates_box_and_status_and_never_decides():
    a, b = fx.agreement_pair()
    r = agreement.compare(a, b)
    assert r["box"]["matched"] == 3 and r["box"]["f1"] == 1.0 and r["box"]["mean_iou"] < 1.0
    h = r["status"]["helmet"]
    assert h["n"] == 3 and h["raw_agreement"] == pytest.approx(2 / 3) and h["missing_disagreements"] == 1
    assert r["status"]["vest"]["raw_agreement"] == 1.0
    assert r["disagreements"] == [{"frame_index": 100, "kind": "status", "item": "helmet", "track_a": "t1",
                                   "track_b": "t1", "a": "MISSING", "b": "UNKNOWN"}]
    assert r["resolution"] == "ADJUDICATION_REQUIRED"


def test_cohen_kappa_edge_cases():
    assert agreement.cohen_kappa([], dc.STATUSES) is None
    assert agreement.cohen_kappa([("PRESENT", "PRESENT")] * 4, dc.STATUSES) is None     # tek kategori: tanımsız
    assert agreement.cohen_kappa([("PRESENT", "PRESENT"), ("MISSING", "MISSING")], dc.STATUSES) == 1.0
    assert agreement.cohen_kappa([("PRESENT", "MISSING"), ("MISSING", "PRESENT")], dc.STATUSES) == -1.0


# ---------------------------------------------------------------- FAZ 8 değerlendirme entegrasyonu
def _person(pid, viol):
    return {"pid": pid, "box": [100.0, 100.0, 220.0, 460.0], "conf": 0.9, "decided": True, "helmet_viol": viol,
            "vest_viol": False, "helmet_box": None if viol else list(map(float, fx.A_HELMET)),
            "vest_box": list(map(float, fx.A_VEST))}


def test_converted_ground_truth_feeds_faz8_evaluation_and_counts_fragmentation():
    doc = dc.to_eval_video(fx.base_documents()["m002"], "test")
    assert ann.validate(doc)[0] == []                            # FAZ 8 şemasıyla geçerli
    run = {"frames": [{"frame": 0, "people": [_person(1, False)]}, {"frame": 50, "people": [_person(1, True)]},
                      {"frame": 100, "people": [_person(2, True)]}],            # ID değişimi (parçalanma)
           "events": [{"type": "start", "incident_id": "p1", "pid": 1, "t": 2.0, "missing_helmet": True}],
           "unclosed": [], "video_sec": 10.0}
    ev = evaluate_against_gt(run, doc)
    assert (ev["incidents"]["gt_incidents"], ev["incidents"]["detected"]) == (1, 1)
    assert ev["tracking"]["id_switches"] == 1 and ev["tracking"]["fragmentation_extra_ids"] == 1
    assert ev["person_frame"]["helmet"]["tp"] == 2


def test_converter_refuses_unreviewed_or_invalid():
    doc = fx.base_documents()["m002"]
    doc["frames"][1]["persons"][0]["review"] = {"status": "pending"}
    with pytest.raises(ValueError, match="BLOCKED"):
        dc.to_eval_video(doc, "test")


# ---------------------------------------------------------------- JSON Schema sözleşmesi kodla tutarlı
def test_json_schemas_match_contract_constants():
    s = json.loads(open(f"{SCHEMAS}{os.sep}ppe-annotation-1.schema.json", encoding="utf-8").read())
    person = s["properties"]["frames"]["items"]["properties"]["persons"]["items"]
    assert person["properties"]["helmet"]["properties"]["status"]["enum"] == list(dc.STATUSES)
    assert person["properties"]["visibility"]["enum"] == list(dc.VISIBILITY)
    assert s["properties"]["media"]["properties"]["license"]["properties"]["status"]["enum"] == list(dc.LICENSE)
    m = json.loads(open(f"{SCHEMAS}{os.sep}ppe-dataset-manifest-1.schema.json", encoding="utf-8").read())
    assert m["properties"]["split_policy"]["properties"]["group_by"]["enum"] == list(dm.LEVELS)
    doc = fx.base_documents()["m004"]
    assert set(s["required"]) <= set(doc) and set(person["required"]) <= set(doc["frames"][0]["persons"][0])
    assert set(s["properties"]["media"]["required"]) <= set(doc["media"])


def test_json_schema_against_fixture_manifest(tmp_path):
    m = json.loads(open(f"{SCHEMAS}{os.sep}ppe-dataset-manifest-1.schema.json", encoding="utf-8").read())
    man = fx.write_dataset(tmp_path, fx.base_documents())
    assert set(m["required"]) <= set(man)
    assert all(set(m["properties"]["items"]["items"]["required"]) <= set(i) for i in man["items"])


# ---------------------------------------------------------------- CLI
def test_cli_fixture_validate_convert_agreement(tmp_path, capsys):
    ds = tmp_path / "ds"
    assert cli.main(["data-fixtures", "--out", str(ds)]) == 0
    assert cli.main(["data-fixtures", "--out", str(ds)]) == 1                   # dolu dizinin üzerine yazmaz
    out = tmp_path / "rep"
    assert cli.main(["data-validate", str(ds / "manifest.json"), "--out", str(out)]) == 0
    rep = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert rep["status"] == "PASS" and rep["real_site_dataset"] == "BLOCKED"
    assert "SYNTHETIC" in (out / "report.md").read_text(encoding="utf-8")
    bad = tmp_path / "bad"
    assert cli.main(["data-fixtures", "--out", str(bad), "--scenario", "train_test_leakage"]) == 0
    assert cli.main(["data-validate", str(bad / "manifest.json"), "--out", str(tmp_path / "r2")]) == 1
    blk = tmp_path / "blk"
    cli.main(["data-fixtures", "--out", str(blk), "--scenario", "missing_license_approval"])
    assert cli.main(["data-validate", str(blk / "manifest.json"), "--out", str(tmp_path / "r3")]) == 2
    assert cli.main(["data-convert", str(ds / "annotations" / "m002.json"), "--split", "test",
                     "--out", str(tmp_path / "e.json")]) == 0
    assert ann.validate(json.loads((tmp_path / "e.json").read_text(encoding="utf-8")))[0] == []
    a, b = fx.agreement_pair()
    (tmp_path / "a.json").write_text(json.dumps(a), encoding="utf-8")
    (tmp_path / "b.json").write_text(json.dumps(b), encoding="utf-8")
    assert cli.main(["data-agreement", str(tmp_path / "a.json"), str(tmp_path / "b.json"),
                     "--out", str(tmp_path / "iaa.json")]) == 0
    assert cli.main(["data-fixtures", "--out", str(tmp_path / "x"), "--scenario", "nope"]) == 1


def test_cli_manifest_builder_assigns_splits_and_freezes(tmp_path):
    root = tmp_path / "ds"
    fx.write_dataset(root, fx.base_documents())
    items = [{"media_id": m, "media_path": f"media/{m}.bin", "annotation_path": f"annotations/{m}.json",
              "dhash": [fx.DHASH[m]]} for m in ("m001", "m002", "m003", "m004")]
    (tmp_path / "items.json").write_text(json.dumps(items), encoding="utf-8")
    out = root / "manifest2.json"
    assert cli.main(["data-manifest", "--root", str(root), "--items", str(tmp_path / "items.json"),
                     "--dataset-id", "ppe-fixture", "--dataset-version", "1.0.0", "--created-at",
                     "2026-11-02T00:00:00Z", "--classification", "SYNTHETIC", "--group-by", "camera_id",
                     "--seed", "s1", "--ratios", "0.5,0.25,0.25", "--assign-splits", "--freeze", "veri_muhendisi",
                     "--out", str(out)]) == 0
    man = json.loads(out.read_text(encoding="utf-8"))
    assert sorted(i["split"] for i in man["items"]) == ["test", "train", "train", "val"]
    assert dm.validate_dataset(root, man)["status"] == "PASS"
