"""FAZ 10-A.4 tekrarlanabilir video taban çizgisi (tek komut). Mevcut bileşenleri yeniden kullanır:
detector/evaluation (pipeline.run_video = üretim karar zincirinin ölçümlü çevrimdışı kopyası, cli.environment =
model/sürüm/tracker manifesti, evaluate_against_gt), tools/reliability/common (izole yığın, psql, gerçek dedektör
süreci). Analiz: detector/evaluation/baseline.py (birim testli).

  python tools/reliability/baseline_eval.py run                        # V1-V3, VID_STRIDE=2, çevrimdışı iz + uçtan uca
  python tools/reliability/baseline_eval.py run --no-e2e               # yalnız çevrimdışı iz (DB/Docker gerekmez)
  python tools/reliability/baseline_eval.py run --review review.json --previous <önceki result.json>
  python tools/reliability/baseline_eval.py compare A/result.json B/result.json

Aşamalar:
  1. Manifest: video SHA-256 + kapsayıcı meta, model SHA-256 + sınıf haritası, Python/torch/CUDA/Ultralytics/OpenCV,
     cihaz, CONF/IMGSZ/tracker yapılandırması/VID_STRIDE, karar parametreleri, git HEAD.
  2. Çevrimdışı iz: kare başına kişi kutuları + güven + takip kimliği + eşleşen baret/yelek kutuları + PPEMonitor
     durumu + kapı (ANALIZ/değerlendirilemiyor) nedenleri + olaylar (trace_<video>.jsonl); incident başlangıç
     kareleri ve isteğe bağlı her N. kare çizimli inceleme görüntüsü (review/).
  3. Uçtan uca (varsayılan): gerçek detector.py (LOOP=0) -> SQLite outbox -> dashboard -> izole PostgreSQL. Ayrı
     compose projesi `ppe-eval` (ayrı volume'lar, ayrı rastgele parolalar, 127.0.0.1:8200). Dedektör outbox defteri
     ile DB satırları karşılaştırılır (eksik / fazla / mükerrer ayrı); incident, run, kişi raporu, snapshot kontrolü.
  4. İzolasyon doğrulaması: ana PostgreSQL sayımları, ana snapshot klasörü, üretim outbox'ı ve ppe-rel container
     durumları önce/sonra aynı olmalı.
  5. Rapor: result.json (makine) + REPORT.md (insan); önceki çalıştırma ve geçmiş (doğrulanmamış) referansla fark.

Ground-truth yoksa precision/recall/yanlış alarm oranı INSUFFICIENT_GROUND_TRUTH. Ham iz, görüntüler ve yığın
parolaları Git dışı (tools/reliability/output/). Videolar depoya eklenmez."""
import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DET = ROOT / "detector"
# izole değerlendirme yığını: ppe-rel deneylerinden ve ana yığından ayrı (common içe aktarılmadan önce)
os.environ.setdefault("REL_PROJECT", "ppe-eval")
os.environ.setdefault("REL_DEFAULT_PORT", "8200")   # common.env(): yeni yığının portu (SteelBench ile ortak ad)
os.environ.setdefault("REL_OUTPUT", str(HERE / "output" / "eval_stack"))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(DET))
import common as c                                                     # noqa: E402

VIDEOS = {"V1": DET / "videos" / "16533594_1920_1080_25fps.mp4",
          "V2": DET / "videos" / "5434223-hd_1920_1080_24fps.mp4",
          "V3": DET / "videos" / "8965526-uhd_2160_3840_25fps.mp4"}
KNOWN = HERE / "baseline" / "known_errors.json"
REFERENCE = HERE / "baseline" / "reference_mac_20261102.json"
MAIN_DB = "ppe-split-db-1"
PROTECTED_STACKS = ("ppe-split", "ppe-rel")
LIMITATIONS = [
    "Ground-truth (ppe-eval-video/1) yok: precision, recall, yanlış alarm oranı ve gerçek kapsam hesaplanmadı.",
    "Alarm sınıflandırması yalnız insan inceleme kaydıyla (review.json) yapılır; incelenmemiş alarm doğru tespit "
    "sayılmaz.",
    "Mac (Apple MPS) referansı kullanıcı bildirimidir; ham kanıt ve video SHA-256'ları bu makinede yok -> girdi "
    "kimliği doğrulanamaz, karşılaştırma yalnız tarihsel.",
    "Üç stok video (dosya adı Pexels kalıbı) lisansı doğrulanmadı; yalnız yerel teknik ölçüm, dağıtım/eğitim yok.",
    "Çevrimdışı iz ile gerçek zamanlı uçtan uca koşu aynı karar zincirini kullanır ancak GPU sayısal belirsizliği "
    "nedeniyle olay sayıları küçük farklar gösterebilir (ayrı raporlanır).",
]


def log(msg):
    print(f"[{c.utc()}] {msg}", flush=True)


# ---------------------------------------------------------------- izolasyon
def _main_db(sql: str) -> str:
    r = subprocess.run(["docker", "exec", MAIN_DB, "sh", "-c",
                        f'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAX -F "|" -c "{sql}"'],
                       capture_output=True, text=True, timeout=30)
    return r.stdout.strip() if r.returncode == 0 else f"ERR:{r.stderr.strip()[:80]}"


def isolation_snapshot() -> dict:
    """Salt okunur: ana DB sayımları, ana snapshot/outbox dosyaları, korunan yığınların container durumları."""
    snap = ROOT / "data" / "snapshots"
    ps = subprocess.run(["docker", "ps", "-a", "--format", "{{.Names}}|{{.State}}|{{.Image}}"], capture_output=True,
                        text=True, timeout=30).stdout.splitlines()
    return {"main_db_counts": _main_db("SELECT (SELECT count(*) FROM violation_events), (SELECT count(*) FROM "
                                       "incidents), (SELECT count(*) FROM detector_runs), (SELECT count(*) FROM "
                                       "person_run_reports), (SELECT max(id) FROM audit_log)"),
            "main_snapshots": sorted(f.name for f in snap.glob("*")) if snap.exists() else [],
            "production_outbox_exists": (DET / "outbox").exists(),
            "protected_containers": sorted(x for x in ps if x.split("|")[0].startswith(PROTECTED_STACKS)),
            "other_containers": sorted(x for x in ps if not x.split("|")[0].startswith(PROTECTED_STACKS + (c.PROJECT,)))}


def isolation_verdict(before: dict, after: dict) -> dict:
    checks = {
        "eval_project_distinct": c.PROJECT not in PROTECTED_STACKS,
        "eval_port_distinct": c.env()["REL_PORT"] not in ("8000", "8100"),
        "main_db_unchanged": before["main_db_counts"] == after["main_db_counts"]
                             and not before["main_db_counts"].startswith("ERR"),
        "main_snapshots_unchanged": before["main_snapshots"] == after["main_snapshots"],
        "production_outbox_untouched": before["production_outbox_exists"] == after["production_outbox_exists"],
        "protected_containers_unchanged": before["protected_containers"] == after["protected_containers"],
        "other_containers_unchanged": before["other_containers"] == after["other_containers"],
    }
    return {"ok": all(checks.values()), "checks": checks, "main_db_counts": before["main_db_counts"],
            "main_snapshot_files": len(before["main_snapshots"])}


# ---------------------------------------------------------------- çevrimdışı iz
def offline(model, settings: dict, out: Path, frame_every: int) -> tuple[dict, dict]:
    from evaluation import baseline as bl
    from evaluation import pipeline
    import telemetry
    runs, analyses = {}, {}
    for key, path in VIDEOS.items():
        log(f"çevrimdışı iz {key} ({path.name})")
        run = pipeline.run_video(model, str(path), settings, camera_id=f"base{key.lower()}")
        a = bl.analyze_video(run, key, telemetry.gate_reasons)
        with open(out / f"trace_{key}.jsonl", "w", encoding="utf-8") as f:
            for fr in run["frames"]:
                f.write(json.dumps(fr, default=float) + "\n")
        with open(out / f"events_{key}.jsonl", "w", encoding="utf-8") as f:
            for e in run["events"]:
                f.write(json.dumps({k: v for k, v in e.items() if k != "snapshot_b64"}, default=str) + "\n")
        review_images(path, run, a, out / "review", key, frame_every)
        runs[key], analyses[key] = run, a
    return runs, analyses


def review_images(path: Path, run: dict, a: dict, d: Path, key: str, every: int) -> None:
    """İnceleme görüntüleri (Git dışı): incident başlangıç kareleri + isteğe bağlı her N. işlenen kare. Kişi kutusu
    (yeşil=UYGUN, kırmızı=IHLAL, sarı=ANALIZ), kimlik, kapı nedenleri, eşleşen baret (mavi) / yelek (turuncu)."""
    import cv2
    d.mkdir(exist_ok=True)
    starts = {i["start_frame"] for i in a["incidents"] if i["start_frame"] is not None}
    pick = [f for f in run["frames"] if f["frame"] in starts or (every and f["idx"] % every == 0)]
    cap = cv2.VideoCapture(str(path))
    for f in pick:
        cap.set(cv2.CAP_PROP_POS_FRAMES, f["frame"])
        ok, img = cap.read()
        if not ok:
            continue
        for p in f["people"]:
            col = {"UYGUN": (0, 200, 0), "IHLAL": (0, 0, 255)}.get(p["status"], (0, 220, 255))
            x1, y1, x2, y2 = map(int, p["box"])
            cv2.rectangle(img, (x1, y1), (x2, y2), col, 3)
            label = f"id{p['pid']} {p['status']} c{p['conf']:.2f} {','.join(p.get('gate') or [])}"
            cv2.putText(img, label, (x1, max(20, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.8, col, 2)
            for bk, bc in (("helmet_box", (255, 120, 0)), ("vest_box", (0, 140, 255))):
                if p.get(bk):
                    b = list(map(int, p[bk]))
                    cv2.rectangle(img, (b[0], b[1]), (b[2], b[3]), bc, 2)
        tag = "incident" if f["frame"] in starts else "frame"
        cv2.imwrite(str(d / f"{key}_{tag}_t{f['t']:06.2f}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    cap.release()


# ---------------------------------------------------------------- uçtan uca
def e2e(out: Path, timeout_extra: float) -> dict:
    from evaluation import baseline as bl
    d = out / "e2e"
    ob = d / "outbox"
    ob.mkdir(parents=True, exist_ok=True)
    log(f"izole yığın {c.PROJECT} hazırlanıyor ({c.base_url()})")
    c.up(build=False)
    c.reset_data()
    cams = {k: f"base{k.lower()}" for k in VIDEOS}
    sources = [f"[{cams[k]}]{k}={p}" for k, p in VIDEOS.items()]
    p = c.start_detector(sources, ob, d / "detector.log", stride=2, extra={"LOOP": "0"})
    budget = sum(bl.video_entry(v, k)["frame_count"] / 24.0 for k, v in VIDEOS.items()) + timeout_extra
    t0 = time.monotonic()
    while p.poll() is None and time.monotonic() - t0 < budget:
        time.sleep(2)
    stop = {"exited_by_itself": p.poll() is not None, "sec": round(time.monotonic() - t0, 1)}
    if p.poll() is None:
        stop.update(c.stop_detector(p, timeout=60))
    stop["returncode"] = p.returncode
    ledger, reports, states = [], [], {}
    for f in sorted(ob.glob("*.sqlite3")):
        con = sqlite3.connect(f"file:{f.as_posix()}?mode=ro", uri=True)
        try:
            for kind, et, mid, run_id, st in con.execute("SELECT kind, event_type, msg_id, run_id, state FROM outbox"):
                states[st] = states.get(st, 0) + 1
                (ledger if kind == "event" else reports).append({"event_id": mid, "type": et, "run_id": run_id,
                                                                 "camera": f.stem, "state": st})
        finally:
            con.close()
    ids = ",".join(f"'{x}'" for x in cams.values())
    rows = [dict(zip(("event_id", "event_type", "camera_id", "incident_id", "snapshot_path"), line.split("\t")))
            for line in c.psql(f"SELECT event_id, event_type, camera_id, incident_id, coalesce(snapshot_path,'') "
                               f"FROM violation_events WHERE camera_id IN ({ids})").splitlines() if line]
    delivery = bl.delivery_compare(ledger, rows)
    runs = c.psql(f"SELECT camera_id, status, coalesce(report_metadata->>'end_reason','-'), "
                  f"(SELECT count(*) FROM person_run_reports p WHERE p.run_id = r.id) FROM detector_runs r "
                  f"WHERE camera_id IN ({ids}) ORDER BY 1").splitlines()
    inc = c.psql(f"SELECT camera_id, count(*) FROM incidents WHERE camera_id IN ({ids}) GROUP BY 1").splitlines()
    snaps = [r["snapshot_path"] for r in rows if r["snapshot_path"]]
    present = subprocess.run(["docker", "exec", c.container("dashboard"), "ls", "/data/snapshots"], capture_output=True,
                             text=True).stdout.split()
    missing_snap = [s for s in snaps if s.rsplit("/", 1)[-1] not in present]
    log_text = (d / "detector.log").read_text(encoding="utf-8", errors="replace")
    image = subprocess.run(["docker", "inspect", c.container("dashboard"), "--format", "{{.Image}}"],
                           capture_output=True, text=True).stdout.strip()
    return {"delivery": delivery, "e2e": {
        "stack": {"project": c.PROJECT, "url": c.base_url(), "dashboard_image": image},
        "detector_stop": stop, "outbox_states": states,
        "runs": [dict(zip(("camera", "status", "end_reason", "person_rows"), x.split("\t"))) for x in runs],
        "reports": {"detector_reports": len(reports), "runs_with_report": sum(1 for x in runs if x.split("\t")[1] != "running")},
        "incidents": {x.split("\t")[0]: int(x.split("\t")[1]) for x in inc},
        "snapshots": {"rows_with_snapshot": len(snaps), "files_missing": len(missing_snap)},
        "events_by_camera": {k: sum(1 for e in ledger if e["camera"] == cid) for k, cid in cams.items()},
        "detector_log_errors": [ln for ln in log_text.splitlines() if "HATA" in ln or "Traceback" in ln][:10],
        "tracebacks_in_dashboard_log": subprocess.run(["docker", "logs", c.container("dashboard")],
                                                      capture_output=True, text=True, encoding="utf-8",
                                                      errors="replace").stdout.count("Traceback")}}


# ---------------------------------------------------------------- ana akış
def manifest(settings: dict, env: dict) -> dict:
    from evaluation import baseline as bl
    return {"schema": bl.SCHEMA, "environment": {**{k: env[k] for k in ("generated_utc", "git_head", "git_dirty_files",
                                                                       "python", "platform", "torch", "cuda",
                                                                       "cuda_available", "gpu", "ultralytics",
                                                                       "opencv", "numpy")},
                                                 "device": settings["device"]},
            "model": {"file": env["checkpoint"]["path"], "sha256": env["checkpoint"]["sha256"],
                      "bytes": env["checkpoint"]["bytes"], "names": env["checkpoint"]["names"]},
            "settings": {k: settings[k] for k in ("device", "imgsz", "conf", "tracker", "vid_stride")},
            "tracker_config": env["tracker_config"], "decision_parameters": env["decision_parameters"],
            "videos": {k: bl.video_entry(p, k) for k, p in VIDEOS.items()},
            "known_errors_sha256": bl.sha256_file(KNOWN)}


def cmd_run(a) -> int:
    from evaluation import baseline as bl
    from evaluation import cli as ev
    for k, p in VIDEOS.items():
        if not p.exists():
            log(f"BLOCKED: video yok: {k} {p.name}")
            return 3
    out = HERE / "output" / f"{c.stamp()}_baseline_{a.label}"          # yığın parolaları ayrı: output/eval_stack
    out.mkdir(parents=True)
    settings = {**ev.production_settings(), "vid_stride": a.stride}
    env = ev.environment(settings)
    if env["checkpoint"]["names"].get(6) != "Person" or env["checkpoint"]["names"].get(0) != "helmet":
        log("HATA: model sınıf haritası beklenenden farklı")
        return 2
    res = {"run_id": out.name, "created_utc": c.utc(), "manifest": manifest(settings, env),
           "limitations": LIMITATIONS}
    before = isolation_snapshot() if not a.no_e2e else None
    model = ev._load_model(settings)
    runs, videos = offline(model, settings, out, a.frame_every)
    del model
    res["videos"] = videos
    known = json.loads(KNOWN.read_text(encoding="utf-8"))["known_errors"]
    review = json.loads(Path(a.review).read_text(encoding="utf-8")) if a.review else None
    res["review_unmatched"] = bl.apply_review(videos, review)
    res["known_errors"] = bl.evaluate_known_errors(known, videos, res["manifest"]["videos"], review)
    res["review_summary"] = bl.review_summary(videos)
    res["metrics"] = bl.metric_block(None)
    if not a.no_e2e:
        try:
            res.update(e2e(out, a.timeout_extra))
            res["offline_vs_e2e_events"] = {k: {"offline": videos[k]["events"]["total"],
                                                "e2e_detector": res["e2e"]["events_by_camera"][k]} for k in VIDEOS}
        finally:
            res["isolation"] = isolation_verdict(before, isolation_snapshot())
    res["digest"] = bl.run_digest(res)
    if a.previous:
        res["comparison_previous"] = bl.compare(_digest(a.previous), res["digest"])
    res["comparison_reference"] = bl.compare(json.loads(REFERENCE.read_text(encoding="utf-8")), res["digest"])
    c.write_json(out / "result.json", res)
    (out / "REPORT.md").write_text(bl.render_markdown(res), encoding="utf-8")
    log(f"çıktı: {out}")
    print(json.dumps({"alarms": res["review_summary"], "known_errors": {k["id"]: k["signal"] for k in res["known_errors"]},
                      "delivery_ok": res.get("delivery", {}).get("ok"), "isolation_ok": res.get("isolation", {}).get("ok")},
                     ensure_ascii=False))
    ok = res.get("delivery", {}).get("ok", True) and res.get("isolation", {}).get("ok", True)
    return 0 if ok else 1


def _digest(path) -> dict:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    return d.get("digest", d)


def cmd_compare(a) -> int:
    from evaluation import baseline as bl
    print(json.dumps(bl.compare(_digest(a.previous), _digest(a.current)), ensure_ascii=False, indent=1))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--label", default="cuda")
    r.add_argument("--stride", type=int, default=2, help="referans test VID_STRIDE=2")
    r.add_argument("--no-e2e", action="store_true", help="yalnız çevrimdışı iz (izole yığın/dedektör süreci yok)")
    r.add_argument("--review", help="insan inceleme kaydı (review.json)")
    r.add_argument("--previous", help="karşılaştırılacak önceki result.json")
    r.add_argument("--frame-every", type=int, default=0, help="her N. işlenen kareden inceleme görüntüsü (0 = yalnız alarmlar)")
    r.add_argument("--timeout-extra", type=float, default=240)
    r.set_defaults(fn=cmd_run)
    cp = sub.add_parser("compare")
    cp.add_argument("previous")
    cp.add_argument("current")
    cp.set_defaults(fn=cmd_compare)
    a = ap.parse_args(argv)
    for k in ("review", "previous", "current"):                         # göreli yollar chdir'den önce çözülür
        if getattr(a, k, None):
            setattr(a, k, str(Path(getattr(a, k)).resolve()))
    os.chdir(DET)                                                      # detector modülleri .env'i buradan okur
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
