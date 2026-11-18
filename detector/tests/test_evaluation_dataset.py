"""FAZ 8 veri denetimi: YOLO etiket doğrulama, sahipsiz/eksik etiket, sınıf eşleme uyumsuzluğu, sızıntı (bayt-özdeş,
algısal yakın kopya, kaynak grubu), donmuş manifest tutarlılığı ve video GT şeması (UNKNOWN kuralları dahil).
Sentetik fixture'lar; gerçek kişi görüntüsü yok."""
import copy
import json

import cv2
import numpy as np
import pytest

from evaluation import annotations as ann
from evaluation import dataset as ds

NAMES = {0: "helmet", 1: "gloves", 2: "vest", 3: "boots", 4: "goggles", 5: "none", 6: "Person", 7: "no_helmet",
         8: "no_goggle", 9: "no_gloves", 10: "no_boots"}


def _img(path, seed, size=64):
    rng = np.random.default_rng(seed)
    cv2.imwrite(str(path), rng.integers(0, 255, (size, size, 3), dtype=np.uint8))


@pytest.fixture
def yolo_root(tmp_path):
    for sp in ("train", "val", "test"):
        (tmp_path / "images" / sp).mkdir(parents=True)
        (tmp_path / "labels" / sp).mkdir(parents=True)
    for i in range(4):
        _img(tmp_path / "images" / "train" / f"a{i}.jpg", i)
        (tmp_path / "labels" / "train" / f"a{i}.txt").write_text("6 0.5 0.5 0.4 0.8\n0 0.5 0.2 0.1 0.1\n")
    (tmp_path / "labels" / "train" / "orphan.txt").write_text("0 0.5 0.5 0.1 0.1\n")
    _img(tmp_path / "images" / "test" / "t0.jpg", 100)
    (tmp_path / "labels" / "test" / "t0.txt").write_text("6 0.5 0.5 0.4 0.8\n12 0.5 0.5 0.1 0.1\n0 1.5 0.5 0.1 0.1\n")
    _img(tmp_path / "images" / "test" / "t1.jpg", 101)                       # etiket dosyası yok
    _img(tmp_path / "images" / "val" / "v0.jpg", 200)
    (tmp_path / "labels" / "val" / "v0.txt").write_text("")
    return tmp_path


def test_parse_label_rejects_invalid_rows():
    rows, errs = ds.parse_yolo_label_text("0 0.5 0.5 0.1 0.1\n0 0.5\n11 0.5 0.5 0.1 0.1\n0 0.5 0.5 0 0.1\n"
                                          "0 1.2 0.5 0.1 0.1\nx 0.5 0.5 0.1 0.1\n", 11)
    assert rows == [(0, 0.5, 0.5, 0.1, 0.1)] and len(errs) == 5


def test_audit_counts_orphans_missing_and_invalid(yolo_root):
    tr = ds.audit_split(yolo_root, "train", NAMES)
    assert tr["images"] == 4 and tr["orphan_label_files"] == ["orphan.txt"]
    assert tr["instances"]["Person"] == 4 and tr["instances"]["helmet"] == 4
    te = ds.audit_split(yolo_root, "test", NAMES)
    assert te["missing_label_files"] == 1 and te["invalid_rows"] == 2
    assert ds.audit_split(yolo_root, "val", NAMES)["empty_label_files"] == 1


def test_class_mapping_mismatch_detected():
    req = {"helmet": 0, "vest": 2, "Person": 6}
    assert ds.check_class_mapping(NAMES, NAMES, req) == []
    swapped = {**NAMES, 2: "boots", 3: "vest"}
    probs = ds.check_class_mapping(swapped, NAMES, req)
    assert any("vest=2" in p for p in probs) and any("farklı" in p for p in probs)


def test_leakage_exact_and_near_duplicates(yolo_root):
    src = yolo_root / "images" / "train" / "a0.jpg"
    (yolo_root / "images" / "test" / "dup.jpg").write_bytes(src.read_bytes())              # bayt-özdeş
    im = cv2.imread(str(src))
    cv2.imwrite(str(yolo_root / "images" / "test" / "near.jpg"), cv2.resize(im, (60, 60)))  # yeniden boyut
    r = ds.leakage_report(yolo_root, "test", "train", max_dist=6)
    assert r["exact_duplicates"] == 1 and ("dup.jpg", "a0.jpg") in [tuple(x) for x in r["exact_examples"]]
    near = {q for q, _, _ in r["near_examples"]}
    assert "near.jpg" in near and "t0.jpg" not in near
    assert "YOK" in r["source_metadata"]


def test_group_leakage_by_camera_and_unknown_metadata():
    entries = [{"split": "train", "camera_id": "c1", "site_id": "s1"},
               {"split": "test", "camera_id": "c1", "site_id": "s2"},
               {"split": "test", "camera_id": "c2"}]
    r = ds.check_group_leakage(entries, keys=("camera_id", "site_id"))
    assert r["camera_id"]["status"] == "LEAK" and r["camera_id"]["leaking_values"] == {"c1": ["test", "train"]}
    assert r["site_id"]["status"] == "UNKNOWN" and r["site_id"]["unknown_entries"] == 1   # kanıtlanmış OK değil


def test_manifest_frozen_and_tamper_detection(yolo_root):
    man = ds.build_manifest(yolo_root, ["test", "val"], dataset_name="fx")
    assert ds.verify_manifest(yolo_root, man) == []
    assert man["file_count"] == 3
    (yolo_root / "labels" / "test" / "t0.txt").write_text("6 0.5 0.5 0.4 0.8\n")               # etiket değişti
    _img(yolo_root / "images" / "test" / "new.jpg", 7)                                        # yeni dosya
    probs = ds.verify_manifest(yolo_root, man)
    assert any("içerik değişmiş: labels/test/t0.txt" in p for p in probs)
    assert any("manifestte olmayan dosya: images/test/new.jpg" in p for p in probs)
    forged = dict(man, files=man["files"][:-1])
    assert any("özeti tutmuyor" in p for p in ds.verify_manifest(yolo_root, forged))


def test_manifest_is_reproducible(yolo_root):
    a = ds.build_manifest(yolo_root, ["test"], dataset_name="fx")
    b = ds.build_manifest(yolo_root, ["test"], dataset_name="fx")
    assert a["content_sha256"] == b["content_sha256"]
    assert json.dumps(a["files"], sort_keys=True) == json.dumps(b["files"], sort_keys=True)


# ---------------------------------------------------------------- video GT şeması
def test_example_annotation_is_valid():
    err, _ = ann.validate(ann.example_document())
    assert err == []


@pytest.mark.parametrize("mutate,needle", [
    (lambda d: d["frames"][0]["persons"][0].update(box=[200, 100, 100, 50]), "geçersiz box"),
    (lambda d: d["frames"][0]["persons"][0].update(helmet="maybe"), "helmet"),
    (lambda d: d["frames"][0]["persons"][0].update(visibility="head_hidden", helmet="missing"), "unknown"),
    (lambda d: d["frames"][0]["persons"][0].update(visibility="torso_hidden", vest="missing"), "unknown"),
    (lambda d: d["frames"][1].update(frame=10), "artan"),
    (lambda d: d["frames"][0].update(t=9.0), "tutarsız"),
    (lambda d: d["incidents"][0].update(track_id="zz"), "track_id"),
    (lambda d: d["incidents"][0].update(start=5.0, end=4.0), "end > start"),
    (lambda d: d["incidents"][0].update(start=50.0), "aşıyor"),
    (lambda d: d["video"].update(split="holdout"), "split"),
    (lambda d: d.update(frames=[]), "etiketli kare"),
    (lambda d: d.update(schema="x"), "schema"),
    (lambda d: d["frames"][0]["persons"].append(copy.deepcopy(d["frames"][0]["persons"][0])), "yinelenen"),
])
def test_invalid_annotations_are_rejected(mutate, needle):
    doc = ann.example_document()
    mutate(doc)
    err, _ = ann.validate(doc)
    assert any(needle in e for e in err), err


def test_head_hidden_with_unknown_is_valid_and_incident_consistency_warns():
    doc = ann.example_document()
    doc["frames"][0]["persons"][0].update(visibility="head_hidden", helmet="unknown", helmet_box=None)
    doc["incidents"][0].update(start=1.0)                     # incident içinde helmet=present kare yok artık
    err, warn = ann.validate(doc)
    assert err == []
    doc = ann.example_document()
    doc["incidents"][0].update(start=1.0)                     # frame 49 (t=1.96) helmet=present -> uyarı
    err, warn = ann.validate(doc)
    assert err == [] and any("incident içi" in w for w in warn)
