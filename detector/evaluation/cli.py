"""python -m evaluation <komut>  (detector/ dizininden; .venv içinde)

  env                                  sürümler, checkpoint SHA256, sınıf eşlemesi, üretim ayarları
  audit   --root DS [--freeze M]       YOLO veri denetimi + sızıntı + (isteğe bağlı) donmuş manifest
  detect  --root DS --split S [--manifest M] [--allow-test-sweep]
  video   VIDEO... [--gt GT.json ...] [--max-frames N] [--check-equivalence N]
  temporal [--minutes N]               sentetik zamansal senaryolar (model kullanmaz)
  validate-gt GT.json...               ppe-eval-video/1 doğrulama
  example-gt                           sentetik şema örneği

Çıktılar: evaluation/output/<UTC>_<komut>/ (Git dışı). Üretim eşikleri DEĞİŞTİRİLMEZ; tümü detector.py/.env/ppe_core
değerlerinden okunur. Ultralytics telemetrisi bu süreçte YOLO_OFFLINE=1 ile kapatılır."""
import argparse
import csv
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

os.environ["YOLO_OFFLINE"] = "1"                   # değerlendirme sürecinden Ultralytics analitiği gönderilmez

DETECTOR_DIR = Path(__file__).resolve().parent.parent
OUT_ROOT = Path(__file__).resolve().parent / "output"
REQUIRED_CLASSES = {"helmet": 0, "vest": 2, "Person": 6}


def _out_dir(cmd: str) -> Path:
    d = OUT_ROOT / f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}_{cmd}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _dump(path: Path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=_jsonable), encoding="utf-8")


def _jsonable(o):
    if isinstance(o, (set, tuple)):
        return list(o)
    if isinstance(o, Path):
        return o.as_posix()
    if hasattr(o, "item"):
        return o.item()
    return str(o)


def _csv(path: Path, rows: list[dict]):
    if not rows:
        return
    keys = list(dict.fromkeys(k for r in rows for k in r))
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        for r in rows:
            w.writerow({k: json.dumps(v, default=_jsonable) if isinstance(v, (list, dict)) else v for k, v in r.items()})


def production_settings() -> dict:
    """detector.py modül sabitleri (detector/.env + varsayılanlar) — üretimle aynı kaynak."""
    import detector as d
    import ppe_core as core
    model_path = Path(d.MODEL_PATH)
    if not model_path.is_absolute():
        model_path = DETECTOR_DIR / model_path
    return {"model_path": str(model_path), "device": d.DEVICE, "imgsz": d.IMGSZ, "conf": d.CONF,
            "tracker": d.TRACKER, "vid_stride": d.VID_STRIDE, "iou_nms": "Ultralytics varsayılanı (0.7)",
            "class_helmet": core.CLASS_HELMET, "class_vest": core.CLASS_VEST, "class_person": core.CLASS_PERSON}


def _sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def environment(settings: dict) -> dict:
    import cv2
    import numpy
    import torch
    import ultralytics
    import yaml
    from ultralytics.utils.checks import check_yaml
    from .temporal import decision_parameters
    ck = torch.load(settings["model_path"], map_location="cpu", weights_only=False)
    m = ck.get("model") or ck.get("ema")
    ta = ck.get("train_args") or {}
    trk = Path(check_yaml(settings["tracker"]))
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=DETECTOR_DIR, capture_output=True, text=True,
                              timeout=10).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=DETECTOR_DIR, capture_output=True, text=True,
                               timeout=10).stdout.strip().splitlines()
    except Exception:                                 # noqa: BLE001
        head, dirty = None, []
    return {"generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "git_head": head,
            "git_dirty_files": len(dirty), "python": platform.python_version(), "platform": platform.platform(),
            "torch": torch.__version__, "cuda": torch.version.cuda, "cuda_available": torch.cuda.is_available(),
            "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
            "ultralytics": ultralytics.__version__, "opencv": cv2.__version__, "numpy": numpy.__version__,
            "checkpoint": {"path": Path(settings["model_path"]).name, "sha256": _sha256(settings["model_path"]),
                           "bytes": Path(settings["model_path"]).stat().st_size,
                           "names": {int(k): v for k, v in m.names.items()}, "nc": len(m.names),
                           "arch": m.yaml.get("yaml_file"), "scale": m.yaml.get("scale"),
                           "end2end": m.yaml.get("end2end"), "date": ck.get("date"), "version": ck.get("version"),
                           "license": ck.get("license"),
                           "train_args": {k: ta.get(k) for k in ("model", "data", "epochs", "imgsz", "batch", "seed",
                                                                 "deterministic", "optimizer", "split", "fraction",
                                                                 "pretrained")},
                           "train_metrics": ck.get("train_metrics")},
            "production_settings": settings, "decision_parameters": decision_parameters(),
            "tracker_config": {"file": trk.name, **(yaml.safe_load(trk.read_text(encoding="utf-8")) or {})},
            "telemetry": {"YOLO_OFFLINE_in_eval": os.environ.get("YOLO_OFFLINE")}}


def _load_model(settings):
    from ultralytics import YOLO
    return YOLO(settings["model_path"])


# ---------------------------------------------------------------- komutlar
def cmd_env(a):
    s = production_settings()
    env = environment(s)
    problems = []
    for name, idx in REQUIRED_CLASSES.items():
        if env["checkpoint"]["names"].get(idx) != name:
            problems.append(f"{name}={idx} beklenir, checkpoint {idx}={env['checkpoint']['names'].get(idx)!r}")
    env["class_mapping_problems"] = problems
    out = _out_dir("env")
    _dump(out / "environment.json", env)
    print(json.dumps({k: env[k] for k in ("git_head", "ultralytics", "torch", "gpu")}, ensure_ascii=False))
    print("checkpoint sha256:", env["checkpoint"]["sha256"])
    print("sınıf eşleme:", "UYUMLU" if not problems else problems)
    print("çıktı:", out)
    return 1 if problems else 0


def cmd_audit(a):
    from .dataset import audit_split, check_class_mapping, leakage_report, build_manifest, verify_manifest
    import yaml
    root = Path(a.root)
    names = {int(k): v for k, v in (yaml.safe_load((root / "data.yaml").read_text(encoding="utf-8"))["names"]).items()}
    s = production_settings()
    import torch
    m = torch.load(s["model_path"], map_location="cpu", weights_only=False)["model"]
    res = {"root_name": root.name, "class_mapping_problems": check_class_mapping(m.names, names, REQUIRED_CLASSES),
           "splits": {sp: audit_split(root, sp, names) for sp in a.splits},
           "leakage": {sp: leakage_report(root, sp, "train", a.max_hamming) for sp in a.splits if sp != "train"}}
    lk = res["leakage"]
    if "val" in a.splits and "test" in a.splits:
        res["leakage"]["test_vs_val"] = leakage_report(root, "test", "val", a.max_hamming)
    if a.verify:
        res["manifest_verify"] = verify_manifest(root, json.loads(Path(a.verify).read_text(encoding="utf-8")))
    out = _out_dir("audit")
    if a.freeze:
        man = build_manifest(root, a.splits, dataset_name=root.name,
                             extra={"frozen_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                    "checkpoint_sha256": _sha256(s["model_path"])})
        Path(a.freeze).write_text(json.dumps(man, indent=1), encoding="utf-8")
        res["manifest_frozen"] = {"path": Path(a.freeze).name, "content_sha256": man["content_sha256"],
                                  "file_count": man["file_count"]}
    _dump(out / "audit.json", res)
    for sp, r in res["splits"].items():
        print(f"{sp}: {r['images']} görüntü, sahipsiz etiket {len(r['orphan_label_files'])}, eksik etiket "
              f"{r['missing_label_files']}, geçersiz satır {r['invalid_rows']}")
    for sp, r in lk.items():
        print(f"sızıntı {r['eval_split']}↔{r['train_split']}: birebir {r['exact_duplicates']}, yakın "
              f"{r['near_duplicates_dhash']} (hamming<={r['near_max_hamming']})")
    print("çıktı:", out)
    return 0


def cmd_detect(a):
    from . import detect_eval as de
    from .dataset import verify_manifest
    root = Path(a.root)
    if a.manifest:
        problems = verify_manifest(root, json.loads(Path(a.manifest).read_text(encoding="utf-8")))
        if problems:
            print("HATA: donmuş manifest ile veri uyuşmuyor; değerlendirme yapılmadı:", problems[:10])
            return 2
    s = production_settings()
    model = _load_model(s)
    names = {int(k): v for k, v in model.names.items()}
    out = _out_dir(f"detect_{a.split}")
    data_yaml = de.write_data_yaml(root, names, out / "data.yaml")
    res = {"split": a.split, "settings": s, "checkpoint_sha256": _sha256(s["model_path"]),
           "manifest_verified": bool(a.manifest)}
    res["ultralytics_val"] = de.ultralytics_val(model, data_yaml, a.split, s, out)
    items = de.predict_split(model, de.load_split(root, a.split, len(names)), s)
    classes = sorted(names)
    res["images"] = len(items)
    res["operating_point"] = {"conf": s["conf"], "iou_match": 0.5,
                              "per_class": de.operating_point(items, names, classes, s["conf"])}
    res["confidence_distribution"] = {names[c]: v for c, v in
                                      de.confidence_distribution(items, classes, s["conf"]).items()}
    res["recall_by_size"] = de.recall_by_size(items, names, [0, 2, 6, 7], s["conf"])
    res["errors"] = de.error_examples(items, names, [0, 2, 6, 7], s["conf"])
    res["person_proxy"] = de.person_proxy(items, s["conf"])
    if a.split != "test" or a.allow_test_sweep:
        res["threshold_sweep"] = de.threshold_sweep(items, names, [0, 2, 6, 7])
    else:
        res["threshold_sweep"] = "REDDEDİLDİ: test split'inde eşik taraması yapılmaz (validation kullanın)"
    _dump(out / "detect.json", res)
    _csv(out / "per_class.csv", [{"class": k, **de.summarize_prf(v), **res["ultralytics_val"]["per_class"].get(k, {})}
                                  for k, v in res["operating_point"]["per_class"].items()])
    uv = res["ultralytics_val"]
    print(f"{a.split}: mAP50={uv['map50']:.3f} mAP50-95={uv['map50_95']:.3f} P={uv['mp']:.3f} R={uv['mr']:.3f}")
    for k in ("helmet", "vest", "Person", "no_helmet"):
        v = res["operating_point"]["per_class"].get(k)
        if v:
            print(f"  {k:10s} conf>={s['conf']}: P={_f(v['precision'])} R={_f(v['recall'])} F1={_f(v['f1'])} "
                  f"(GT {v['gt_instances']})")
    print("çıktı:", out)
    return 0


def _f(x):
    return "n/a" if x is None else f"{x:.3f}"


def cmd_video(a):
    from .pipeline import run_video, evaluate_against_gt, aggregate, frame_signatures, production_stream_signature
    from .annotations import validate
    import detector as d
    s = production_settings()
    if a.stride:
        s["vid_stride"] = a.stride
    model = _load_model(s)
    gts = {}
    for g in a.gt or []:
        doc = json.loads(Path(g).read_text(encoding="utf-8"))
        err, _ = validate(doc)
        if err:
            print(f"HATA: {Path(g).name} geçersiz, kullanılmadı: {err[:5]}")
            return 2
        gts[doc["video"].get("source_name") or doc["video"]["video_id"]] = doc
    out = _out_dir("video")
    summary, evals = [], []
    for v in a.videos:
        name = Path(v).name
        if a.check_equivalence:
            prod = production_stream_signature(model, v, s, a.check_equivalence)
            mine = frame_signatures(model, v, s, a.check_equivalence)
            same = sum(1 for x, y in zip(prod, mine) if x == y)
            eq = {"frames_compared": min(len(prod), len(mine)), "identical_frames": same}
            print(f"[{name}] eşdeğerlik: {same}/{eq['frames_compared']} kare aynı (id, sınıf, kutu)")
        else:
            eq = None
        run = run_video(model, v, s, max_frames=a.max_frames, snapshot_fn=d.attach_snapshot)
        run["equivalence"] = eq
        frames = run.pop("frames")
        tracks = {}
        for f in frames:
            for p in f["people"]:
                tr = tracks.setdefault(p["pid"], {"first": f["t"], "last": f["t"], "n": 0, "statuses": {}})
                tr["last"], tr["n"] = f["t"], tr["n"] + 1
                tr["statuses"][p["status"]] = tr["statuses"].get(p["status"], 0) + 1
        run["unlabeled_stats"] = {
            "distinct_track_ids": len(tracks),
            "short_tracks_lt_1s": sum(1 for t in tracks.values() if t["last"] - t["first"] < 1.0),
            "frames_with_person": sum(1 for f in frames if f["people"]),
            "person_frames": sum(len(f["people"]) for f in frames),
            "status_person_frames": _count_status(frames),
            "frames_with_no_helmet_class": sum(1 for f in frames if f["raw_no_helmet"]),
            "start_events": sum(1 for e in run["events"] if e["type"] == "start"),
            "alarms_unverified": [{"pid": e["pid"], "t": e["t"], "missing": e["missing"]} for e in run["events"]
                                  if e["type"] == "start"],
            "tracks": tracks}
        for e in run["events"]:
            e.pop("snapshot_b64", None)               # görüntü kırpıntısı çıktıya yazılmaz
        res = {"settings": s, "run": run}
        key = name if name in gts else Path(v).stem
        doc = gts.get(name) or gts.get(Path(v).stem)
        if doc is not None:
            run["frames"] = frames
            ev = evaluate_against_gt(run, doc)
            run.pop("frames")
            res["evaluation"] = {k: v2 for k, v2 in ev.items() if k != "person_frame_rows"}
            evals.append(ev)
        else:
            res["evaluation"] = "BLOCKED: bu video için ground truth yok — doğruluk metriği hesaplanmadı"
        _dump(out / f"video_{Path(key).stem}.json", res)
        tm = run["timing_ms"]
        summary.append({"video": name, "res": f"{run['width']}x{run['height']}", "fps": round(run["fps"], 2),
                        "stride": run["stride"], "frames": run["processed_frames"], "video_sec": round(run["video_sec"], 2),
                        "wall_sec": round(run["wall_sec"], 2), "eff_fps": round(run["effective_proc_fps"], 2),
                        "required_fps": round(run["required_proc_fps"], 2),
                        "realtime_factor": round(run["realtime_factor"], 2),
                        **{f"{k}_p50": _r(tm[k]["p50"]) for k in ("decode_ms", "inference_ms", "bytetrack_ms",
                                                                  "monitor_ms", "alert_ms", "frame_total_ms")},
                        "frame_total_p95": _r(tm["frame_total_ms"]["p95"]),
                        "gpu_max_alloc_mb": _r((run["gpu"] or {}).get("max_allocated_mb")),
                        "rss_mb_max": _r(max((x["rss_mb"] for x in run["resources"]), default=None)),
                        "start_events": run["unlabeled_stats"]["start_events"],
                        "track_ids": run["unlabeled_stats"]["distinct_track_ids"],
                        "has_gt": doc is not None})
        print(json.dumps(summary[-1], ensure_ascii=False))
    _csv(out / "video_summary.csv", summary)
    if evals:
        _dump(out / "aggregate.json", aggregate(evals))
    print("çıktı:", out)
    return 0


def _r(x, n=2):
    return None if x is None else round(x, n)


def _count_status(frames):
    c = {}
    for f in frames:
        for p in f["people"]:
            c[p["status"]] = c.get(p["status"], 0) + 1
    return c


def cmd_temporal(a):
    from . import temporal as tp
    import detector as d
    rate = 25.0 / d.VID_STRIDE
    out = _out_dir("temporal")
    res = {"decision_parameters": tp.decision_parameters(), "processed_rate_hz": rate,
           "note": "SENTETİK — karar mantığı karakterizasyonu; model doğruluğu değildir",
           "scenarios": tp.scenarios(rate), "head_hidden": tp.head_hidden_sweep(rate),
           "flicker": tp.flicker_sweep(rate, minutes=a.minutes), "false_helmet": tp.false_positive_helmet_sweep(rate)}
    _dump(out / "temporal.json", res)
    for sc in res["scenarios"]:
        m = sc["metrics"]
        print(f"{sc['scenario']:22s} GT={m['gt_incidents']} pred={m['pred_incidents']} tespit={m['detected']} "
              f"yanlış={m['false_incidents']} dup={m['duplicates']} gecikme={_f(m['start_latency_sec']['p50'])} "
              f"bitiş_hatası={_f(m['end_error_sec']['p50'])} kapanmamış={m['unclosed_pred_incidents']}")
    for h in res["head_hidden"]:
        print(f"baş görünmez {h['hidden_sec']} sn -> yanlış incident {h['false_incidents']}")
    for f in res["flicker"]:
        print(f"p={f['p_helmet_detect']:.2f} burst={f['mean_miss_burst_frames']:>2} -> "
              f"{f['false_per_person_hour']:.2f} yanlış/kişi-saat, yanlış ihlal süresi "
              f"%{100 * f['false_violation_time_fraction']:.1f}")
    for f in res["false_helmet"]:
        print(f"q={f['q_false_helmet']:.2f} -> kaçırılan {f['missed']}/{f['trials']}, ort. başlangıç "
              f"{_f(f['mean_start_sec'])} sn")
    print("çıktı:", out)
    return 0


def cmd_validate_gt(a):
    from .annotations import validate
    bad = 0
    for p in a.files:
        err, warn = validate(json.loads(Path(p).read_text(encoding="utf-8")))
        print(f"{Path(p).name}: {'GEÇERSİZ' if err else 'GEÇERLİ'} ({len(err)} hata, {len(warn)} uyarı)")
        for e in err[:20]:
            print("  HATA:", e)
        for w in warn[:20]:
            print("  uyarı:", w)
        bad += bool(err)
    return 1 if bad else 0


def cmd_example_gt(a):
    from .annotations import example_document
    print(json.dumps(example_document(), ensure_ascii=False, indent=2))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m evaluation", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("env").set_defaults(fn=cmd_env)
    p = sub.add_parser("audit")
    p.add_argument("--root", required=True)
    p.add_argument("--splits", nargs="+", default=["train", "val", "test"])
    p.add_argument("--max-hamming", type=int, default=4)
    p.add_argument("--freeze")
    p.add_argument("--verify")
    p.set_defaults(fn=cmd_audit)
    p = sub.add_parser("detect")
    p.add_argument("--root", required=True)
    p.add_argument("--split", required=True, choices=["val", "test"])
    p.add_argument("--manifest")
    p.add_argument("--allow-test-sweep", action="store_true")
    p.set_defaults(fn=cmd_detect)
    p = sub.add_parser("video")
    p.add_argument("videos", nargs="+")
    p.add_argument("--gt", nargs="*")
    p.add_argument("--max-frames", type=int)
    p.add_argument("--stride", type=int, help="yalnız çevrimdışı karşılaştırma; üretim VID_STRIDE değişmez")
    p.add_argument("--check-equivalence", type=int, default=0)
    p.set_defaults(fn=cmd_video)
    p = sub.add_parser("temporal")
    p.add_argument("--minutes", type=float, default=30.0)
    p.set_defaults(fn=cmd_temporal)
    p = sub.add_parser("validate-gt")
    p.add_argument("files", nargs="+")
    p.set_defaults(fn=cmd_validate_gt)
    sub.add_parser("example-gt").set_defaults(fn=cmd_example_gt)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
