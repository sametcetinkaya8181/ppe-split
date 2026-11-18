"""SteelBench — tekrar çalıştırılabilir dedektör referans değerlendirmesi (FAZ 10-A.4 baseline).

Gerçek detector.py'yi (best.pt + ByteTrack + PPEMonitor; kod DEĞİŞMEZ) 3 yerel stok video üzerinde, AYRI bir izole
yığına (`ppe-bench`: kendi PostgreSQL volume'u, 127.0.0.1:8200) karşı çalıştırır ve şunları üretir:
  manifest  girdiler (video yolu + SHA-256), model SHA-256, sürümler, cihaz, CONF/IMGSZ/VID_STRIDE, tracker ve
            PPEMonitor ayarları, dashboard imajı, Git commit'i
  bütünlük  dedektörün ürettiği olay/rapor (defter) ↔ PostgreSQL ↔ snapshot dosyası ↔ dashboard API
  inceleme  kişi kutuları, durum/ANALIZ nedenleri, baret/yelek eşleştirmeleri, iz kimliği değişimleri, incident'lar
  sınıflama alarmlar bilinen hatalara (steelbench_expected.json: E-01..E-04) eşlenir; yanlış alarm asla "doğru
            tespit" sayılmaz; ground-truth yetersizse metrikler NOT_MEASURABLE / INSUFFICIENT_GROUND_TRUTH
  izolasyon ana yığın (ppe-split), ppe-rel yığını, üretim outbox'ı ve data/snapshots önce/sonra parmak izi
  karşılaştırma  önceki çalıştırma veya commit'li referans özeti (steelbench_baseline.json)

  python tools/reliability/steelbench.py run [--device auto|mps|0|cpu] [--images] [--compare RESULT.json]
  python tools/reliability/steelbench.py compare ESKİ.json YENİ.json
  python tools/reliability/steelbench.py report RESULT.json            (Markdown'u yeniden üret)

Çıkış kodu: 0 = bütünlük + izolasyon PASS ve referansa göre fark yok; 1 = girdi uyuşmazlığı, bütünlük veya izolasyon
FAIL; 2 = insan incelemesi gerekiyor (yeni alarm, bilinen hata yeniden üretilmedi/değişti).
Çıktı: tools/reliability/output/<UTC>_steelbench/ (Git dışı). Ayrıntı: docs/STEELBENCH.md."""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
# Ayrı izole yığın: ppe-rel ve ana yığın (ppe-split) ile hiçbir kaynak paylaşılmaz (yalnız salt okunur imaj etiketi).
os.environ["REL_PROJECT"] = "ppe-bench"
os.environ["REL_ENV_FILE"] = str(Path(os.getenv("REL_OUTPUT", HERE / "output")) / "bench.env")
os.environ["REL_DEFAULT_PORT"] = "8200"
sys.path.insert(0, str(HERE))
import common as c  # noqa: E402

EXPECTED = HERE / "steelbench_expected.json"
BASELINE = HERE / "steelbench_baseline.json"
SCHEMA = "steelbench-result/1"
FORBIDDEN_PROJECTS = {"ppe-rel", "ppe-split"}
DECISION_KEYS = ("CONF", "IMGSZ", "VID_STRIDE", "TRACKER", "MODEL_PATH", "WINDOW_SEC", "MIN_SPAN_SEC", "MIN_OBS",
                 "ENTER_RATE", "EXIT_RATE", "PERSON_CONF_MIN", "MIN_PERSON_H_PX", "EDGE_MARGIN_PX", "REALERT_SEC",
                 "STALE_SEC", "CLASS_HELMET", "CLASS_VEST", "CLASS_PERSON")
EVALUATED = ("UYGUN", "IHLAL")
PROBE = r'''
import hashlib, json, os, platform, sys
sys.path.insert(0, ".")
import detector, ppe_core as core
import cv2, numpy, torch, ultralytics, yaml
from ultralytics import YOLO
trk = detector.TRACKER
p = trk if os.path.isabs(trk) else os.path.join(os.path.dirname(ultralytics.__file__), "cfg", "trackers", trk)
print(json.dumps({
  "python": platform.python_version(), "platform": platform.platform(), "torch": torch.__version__,
  "ultralytics": ultralytics.__version__, "opencv": cv2.__version__, "numpy": numpy.__version__,
  "cuda_available": torch.cuda.is_available(), "mps_available": bool(getattr(torch.backends, "mps", None) and torch.backends.mps.is_available()),
  "detector": {k: getattr(detector, k) for k in ("CONF", "IMGSZ", "VID_STRIDE", "TRACKER", "DEVICE", "MODEL_PATH")},
  "ppe_core": {k: getattr(core, k) for k in ("WINDOW_SEC", "MIN_SPAN_SEC", "MIN_OBS", "ENTER_RATE", "EXIT_RATE",
               "PERSON_CONF_MIN", "MIN_PERSON_H_PX", "EDGE_MARGIN_PX", "REALERT_SEC", "STALE_SEC", "CLASS_HELMET",
               "CLASS_VEST", "CLASS_PERSON")},
  "tracker_config": yaml.safe_load(open(p)) if os.path.exists(p) else None,
  "model_names": {int(k): v for k, v in YOLO(detector.MODEL_PATH).names.items()},
}, default=str))
'''


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def iou(a, b) -> float:
    ix, iy = max(0, min(a[2], b[2]) - max(a[0], b[0])), max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / u if u > 0 else 0.0


# ---------------------------------------------------------------- izolasyon parmak izi
def _psql_container(name: str, sql: str):
    r = subprocess.run(["docker", "exec", name, "sh", "-c",
                        f'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -AtX -F "|" -c "{sql}"'],
                       capture_output=True, text=True, timeout=30)
    return r.stdout.strip() if r.returncode == 0 else None


def _containers(prefix: str) -> dict:
    r = subprocess.run(["docker", "ps", "-a", "--filter", f"name=^{prefix}-", "--format", "{{.Names}}"],
                       capture_output=True, text=True)
    out = {}
    for n in sorted(r.stdout.split()):
        i = subprocess.run(["docker", "inspect", n, "--format", "{{.Id}}|{{.State.Status}}|{{.State.StartedAt}}"],
                           capture_output=True, text=True).stdout.strip()
        out[n] = i
    return out


def _dir_fp(d: Path) -> dict:
    if not d.is_dir():
        return {"exists": False}
    files = [p for p in d.rglob("*") if p.is_file()]
    return {"exists": True, "files": len(files), "bytes": sum(p.stat().st_size for p in files),
            "newest_mtime": max((p.stat().st_mtime for p in files), default=None)}


COUNTS_SQL = ("SELECT (SELECT count(*) FROM violation_events)||','||(SELECT count(*) FROM detector_runs)||','||"
              "(SELECT count(*) FROM incidents)||','||(SELECT count(*) FROM person_run_reports)||','||"
              "(SELECT coalesce(max(id),0) FROM violation_events)")


def _no_bench_rows(proj: str, cams: list) -> bool:
    """Ana / ppe-rel DB'sinde bench kamera kimlikleriyle satır yok mu (salt okunur). Yığın çalışmıyorsa True."""
    db = c.labeled_running(proj)                        # FAZ 02-A: etiketle çözülür (ad kalıbıyla değil)
    if db is None:
        return True
    ids = ",".join(f"'{x}'" for x in cams)
    n = _psql_container(db, f"SELECT (SELECT count(*) FROM cameras WHERE id IN ({ids})) + "
                            f"(SELECT count(*) FROM violation_events WHERE camera_id IN ({ids}))")
    return n == "0"


def isolation_fingerprint() -> dict:
    """Salt okunur: ana yığın ve ppe-rel DB sayımları (SELECT), container kimlikleri, üretim outbox ve snapshot dizini."""
    fp = {}
    for proj in ("ppe-split", "ppe-rel"):
        cont = _containers(proj)
        db = c.labeled_running(proj)                    # FAZ 02-A: etiketle çözülür (ad kalıbıyla değil)
        fp[proj] = {"containers": cont, "db_counts": _psql_container(db, COUNTS_SQL) if db else "not_running"}
    fp["production_outbox"] = _dir_fp(c.DETECTOR / "outbox")
    fp["main_snapshots"] = _dir_fp(c.ROOT / "data" / "snapshots")
    return fp


# ---------------------------------------------------------------- çalıştırma
def run_detector(out: Path, args, exp: dict) -> dict:
    ledger, outbox = out / "ledger", out / "outbox"
    ledger.mkdir()
    env = c.detector_env(outbox, stride=args.stride, extra={
        "DEVICE": args.device_resolved, "LOOP": "0", "IMGSZ": str(args.imgsz), "MODEL_PATH": args.model,
        "STEELBENCH_LEDGER_DIR": str(ledger)})
    url = env["DASHBOARD_URL"]
    sources = [f"[{v['camera_id']}]{v['key']}={c.ROOT / v['path']}" for v in exp["videos"]]
    t0 = time.monotonic()
    with open(out / "detector.log", "w", encoding="utf-8") as log:
        p = subprocess.Popen([str(c.PY), str(HERE / "steelbench_tap.py"), *sources], cwd=c.DETECTOR, env=env,
                             stdout=log, stderr=subprocess.STDOUT, **c.NEW_GROUP)
        try:
            rc = p.wait(args.timeout)
        except subprocess.TimeoutExpired:
            rc = c.stop_detector(p)["returncode"]
    return {"returncode": rc, "wall_sec": round(time.monotonic() - t0, 1), "dashboard_url": url}


def read_ledger(ledger: Path):
    events, reports, frames = [], [], defaultdict(list)
    for f in sorted(ledger.glob("tap_*.jsonl")):
        recs = [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()]
        cam = next((r["camera_id"] for r in recs if r["kind"] in ("report", "event")), None)
        for r in recs:
            if r["kind"] == "event":
                events.append(r)
            elif r["kind"] == "report":
                reports.append(r)
            elif cam:
                frames[cam].append(r)
    return events, reports, frames


def q(sql):
    return json.loads(c.psql(f"SELECT coalesce(json_agg(t), '[]') FROM ({sql}) t") or "[]")


def _ts(x):
    return datetime.fromisoformat(x.replace("Z", "+00:00"))


def integrity(events, reports) -> dict:
    db_ev = q("SELECT event_id, incident_id, run_id, camera_id, pid, event_type, missing_helmet, missing_vest, "
              "helmet_rate, vest_rate, video_time_sec, occurred_at, box, snapshot_path FROM violation_events")
    db_inc = q("SELECT id, run_id, camera_id, pid FROM incidents")
    db_runs = q("SELECT id, camera_id, status, finished_at, report_metadata FROM detector_runs")
    db_rep = q("SELECT run_id, pid, first_sec, last_sec, observations, helmet_rate, vest_rate, ever_violation "
               "FROM person_run_reports")
    L, D = {e["event_id"]: e for e in events}, {e["event_id"]: e for e in db_ev}
    problems, snaps = [], []
    for eid, le in sorted(L.items()):
        de = D.get(eid)
        if de is None:
            continue
        checks = {"incident_id": le["incident_id"] == de["incident_id"], "run_id": le["run_id"] == de["run_id"],
                  "camera_id": le["camera_id"] == de["camera_id"], "pid": le["pid"] == de["pid"],
                  "type": le["type"] == de["event_type"], "missing_helmet": le["missing_helmet"] == de["missing_helmet"],
                  "missing_vest": le["missing_vest"] == de["missing_vest"],
                  "rates": (le["helmet_rate"], le["vest_rate"]) == (de["helmet_rate"], de["vest_rate"]),
                  "box": [round(v) for v in le["box"]] == [round(v) for v in de["box"]],
                  "video_time": abs(float(le["t"]) - float(de["video_time_sec"])) <= 0.05 + 1e-9,
                  "occurred_at": abs((_ts(le["occurred_at"]) - _ts(de["occurred_at"])).total_seconds()) < 0.001,
                  "box_in_frame": 0 <= de["box"][0] < de["box"][2] <= le["frame_w"] and
                                  0 <= de["box"][1] < de["box"][3] <= le["frame_h"],
                  "snapshot_only_on_start": (de["snapshot_path"] is not None) == (le["type"] == "start"
                                                                                  and "snapshot_sha256" in le)}
        problems += [{"event_id": eid, "check": k} for k, ok in checks.items() if not ok]
        if de["snapshot_path"]:
            snaps.append((eid, de["snapshot_path"].split("/", 1)[1], le.get("snapshot_sha256")))
    sess = c.Session()
    snap_ok = 0
    for eid, name, sha_l in snaps:
        r = subprocess.run(["docker", "exec", c.container("dashboard"), "sha256sum", f"/data/snapshots/{name}"],
                           capture_output=True, text=True)
        api = sess.get(f"/snapshots/{name}")
        sha_api = hashlib.sha256(api.content).hexdigest() if api.status_code == 200 else None
        if sha_l == (r.stdout.split() or [None])[0] == sha_api:
            snap_ok += 1
        else:
            problems.append({"event_id": eid, "check": "snapshot_sha256"})
    inc = {i["id"]: i for i in db_inc}
    by_inc = defaultdict(list)
    for e in db_ev:
        by_inc[e["incident_id"]].append(e)
    inc_ok = 0
    for iid, evs in by_inc.items():
        row = inc.get(iid)
        types = [e["event_type"] for e in sorted(evs, key=lambda e: (float(e["video_time_sec"]), e["event_type"] != "start"))]
        ok = (row is not None and all((e["run_id"], e["camera_id"], e["pid"]) == (row["run_id"], row["camera_id"], row["pid"])
                                      for e in evs)
              and types.count("start") <= 1 and types.count("end") <= 1 and (types[0] == "start" or
                                                                              all(L.get(e["event_id"], {}).get("orphan") for e in evs))
              and ("end" not in types or types[-1] == "end"))
        inc_ok += ok
        if not ok:
            problems.append({"incident_id": iid, "check": "incident_lifecycle", "types": types})
    runs = {r["id"]: r for r in db_runs}
    rows_by = defaultdict(dict)
    for r in db_rep:
        rows_by[r["run_id"]][r["pid"]] = r
    run_ok = 0
    for rp in reports:
        dr, got, rows = runs.get(rp["run_id"]), rows_by.get(rp["run_id"], {}), rp.get("rows") or []

        def same(x):
            g = got.get(x["pid"])
            return (g is not None and abs(float(g["first_sec"]) - x["first"]) < 0.051 and
                    abs(float(g["last_sec"]) - x["last"]) < 0.051 and g["observations"] == x["n"] and
                    g["helmet_rate"] == x["helmet"] and g["vest_rate"] == x["vest"] and g["ever_violation"] == x["violation"])
        ok = (dr is not None and dr["status"] == rp.get("run_status") and dr["finished_at"] is not None and
              ((dr.get("report_metadata") or {}).get("end_reason") == rp.get("end_reason")) and
              len(rows) == len(got) and all(same(x) for x in rows))
        run_ok += ok
        if not ok:
            problems.append({"run_id": rp["run_id"], "check": "run_report"})
    api = sess.get("/api/history/incidents?limit=200")
    api_n = len(api.json().get("items", [])) if api.ok else None
    starts = sum(1 for e in db_ev if e["event_type"] == "start")
    if api_n != starts:
        problems.append({"check": "api_history_count", "api": api_n, "db_starts": starts})
    lost, extra = sorted(set(L) - set(D)), sorted(set(D) - set(L))
    delivery = {"ledger_events": len(events), "db_events": len(db_ev), "lost": lost, "unexpected_in_db": extra,
                "duplicate_ledger_ids": len(events) - len(L), "duplicate_db_ids": len(db_ev) - len(D)}
    ok = not (problems or lost or extra or delivery["duplicate_ledger_ids"] or delivery["duplicate_db_ids"])
    return {"status": "PASS" if ok else "FAIL", "delivery": delivery, "snapshot_names": [n for _, n, _ in snaps],
            "snapshots": {"checked": len(snaps), "match": snap_ok},
            "incidents": {"checked": len(by_inc), "ok": inc_ok, "db_rows": len(db_inc)},
            "runs": {"checked": len(reports), "ok": run_ok, "db_rows": len(db_runs), "db_report_rows": len(db_rep)},
            "api_history_incidents": api_n, "problems": problems}


def outbox_final(outbox: Path) -> dict:
    sys.path.insert(0, str(c.DETECTOR))
    import outbox as ob
    res = {}
    for f in sorted(outbox.glob("*.sqlite3")):
        o = ob.Outbox(f, f.stem, lock_timeout=2)
        try:
            m = o.metrics()
            res[f.stem] = {k: m[k] for k in ("pending_count", "dead_letter_count", "quarantine_count")}
        finally:
            o.close()
    return res


# ---------------------------------------------------------------- inceleme (kare kayıtlarından)
def analyze_video(frames: list, events: list, report: dict | None) -> dict:
    tracks = defaultdict(lambda: {"frames": 0, "evaluated": 0, "first": None, "last": None, "statuses": Counter(),
                                  "analiz_reasons": Counter(), "helmet_matched": 0, "vest_matched": 0, "max_conf": 0.0})
    person_frames = evaluated = 0
    reasons, unmatched = Counter(), Counter()
    births = []
    for fr in frames:
        matched_h = {tuple(p["helmet_box"]) for p in fr["persons"] if p["helmet_box"]}
        matched_v = {tuple(p["vest_box"]) for p in fr["persons"] if p["vest_box"]}
        unmatched["helmet"] += sum(1 for b in fr["helmets"] if tuple(b) not in matched_h)
        unmatched["vest"] += sum(1 for b in fr["vests"] if tuple(b) not in matched_v)
        for p in fr["persons"]:
            tr = tracks[p["pid"]]
            if tr["first"] is None:
                tr["first"] = fr["t"]
                births.append({"t": fr["t"], "pid": p["pid"]})
            tr["last"] = fr["t"]
            tr["frames"] += 1
            tr["statuses"][p["status"]] += 1
            tr["max_conf"] = max(tr["max_conf"], p["conf"])
            tr["helmet_matched"] += p["helmet_box"] is not None
            tr["vest_matched"] += p["vest_box"] is not None
            person_frames += 1
            if p["status"] in EVALUATED:
                evaluated += 1
                tr["evaluated"] += 1
            else:
                why = p["gate"] or ["insufficient_history"]
                for r in why:
                    reasons[r] += 1
                    tr["analiz_reasons"][r] += 1
    track_rows = []
    for pid, tr in sorted(tracks.items()):
        track_rows.append({"pid": pid, "first": tr["first"], "last": tr["last"],
                           "visible_sec": round(tr["last"] - tr["first"], 2), "frames": tr["frames"],
                           "evaluated_frames": tr["evaluated"],
                           "evaluated_fraction": round(tr["evaluated"] / tr["frames"], 3) if tr["frames"] else None,
                           "statuses": dict(tr["statuses"]), "analiz_reasons": dict(tr["analiz_reasons"]),
                           "helmet_matched_frames": tr["helmet_matched"], "vest_matched_frames": tr["vest_matched"],
                           "max_conf": round(tr["max_conf"], 3)})
    inc = {}
    for e in sorted(events, key=lambda e: e["t"]):
        cur = inc.setdefault(e["incident_id"], {"pid": e["pid"], "missing": e["missing"], "start": None, "end": None,
                                                "ongoing": 0, "closed_by": None})
        if e["type"] == "start":
            cur["start"] = e["t"]
        elif e["type"] == "ongoing":
            cur["ongoing"] += 1
        else:
            cur["end"], cur["closed_by"] = e["t"], "end"
    for u in (report or {}).get("unclosed_incidents") or []:
        if u["incident_id"] in inc and inc[u["incident_id"]]["closed_by"] is None:
            inc[u["incident_id"]].update(end=u.get("t"), closed_by=u.get("reason"))
    return {"frames_processed": len(frames), "person_frames": person_frames, "evaluated_person_frames": evaluated,
            "system_coverage": round(evaluated / person_frames, 4) if person_frames else None,
            "analiz_reasons": dict(sorted(reasons.items())), "track_ids": len(tracks),
            "track_ids_ge10_frames": sum(1 for t in tracks.values() if t["frames"] >= 10),
            "track_births": births, "unmatched_items": dict(unmatched), "tracks": track_rows,
            "incidents": sorted(inc.values(), key=lambda x: x["start"] if x["start"] is not None else -1),
            "run": None if report is None else {"status": report.get("run_status"), "end_reason": report.get("end_reason"),
                                                "video_sec": report.get("video_time_sec")}}


# ---------------------------------------------------------------- sınıflama ve metrikler
def classify(events, exp, cam_to_key) -> list:
    known = [k for k in exp["known_errors"] if k["kind"] == "false_alarm"]
    verified = exp.get("verified_true_incidents") or []
    out = []
    for e in sorted((e for e in events if e["type"] == "start"), key=lambda e: (e["camera_id"], e["t"])):
        video = cam_to_key[e["camera_id"]]
        vio = "missing_helmet" if e["missing_helmet"] else "missing_vest"

        def hit(ref):
            return (ref["video"] == video and ref["violation"] in ("missing_helmet" if e["missing_helmet"] else "",
                                                                    "missing_vest" if e["missing_vest"] else "")
                    and abs(e["t"] - ref["t"]) <= ref.get("t_tolerance", 1.5) and iou(e["box"], ref["box"]) >= ref.get("min_iou", 0.3))
        k = next((r for r in known if hit(r)), None)
        v = next((r for r in verified if hit(r)), None)
        cls = "KNOWN_FALSE_ALARM" if k else "VERIFIED_TRUE_VIOLATION" if v else "UNREVIEWED_NEW_ALARM"
        out.append({"video": video, "camera_id": e["camera_id"], "t": e["t"], "pid": e["pid"], "violation": vio,
                    "missing": e["missing"], "helmet_rate": e["helmet_rate"], "vest_rate": e["vest_rate"],
                    "box": e["box"], "classification": cls, "known_error": k["id"] if k else None,
                    "incident_id": e["incident_id"]})
    return out


def known_error_status(exp, alarms, videos) -> dict:
    out = {}
    for k in exp["known_errors"]:
        if k["kind"] == "false_alarm":
            hits = [a for a in alarms if a["known_error"] == k["id"]]
            st = "REPRODUCED" if hits else "NOT_REPRODUCED"
            evidence = [{"t": a["t"], "pid": a["pid"]} for a in hits]
        elif k["kind"] == "coverage_gap":
            cr = k["criterion"]
            bad = [t for t in videos[k["video"]]["tracks"] if t["visible_sec"] >= cr["min_visible_sec"]
                   and (t["evaluated_fraction"] or 0) <= cr["max_evaluated_fraction"]]
            st = "REPRODUCED" if bad else "NOT_REPRODUCED"
            evidence = [{"pid": t["pid"], "visible_sec": t["visible_sec"], "evaluated_fraction": t["evaluated_fraction"],
                         "analiz_reasons": t["analiz_reasons"]} for t in bad]
        else:
            n = videos[k["video"]]["track_ids"]
            st = "REPRODUCED" if n > k["criterion"]["max_track_ids"] else "NOT_REPRODUCED"
            evidence = {"track_ids": n, "track_ids_ge10_frames": videos[k["video"]]["track_ids_ge10_frames"],
                        "reviewed_persons": k["criterion"]["reviewed_persons"]}
        out[k["id"]] = {"kind": k["kind"], "video": k["video"], "status": st, "evidence": evidence,
                        "note": None if st == "REPRODUCED" else
                        "FIX_CANDIDATE: hata bu çalıştırmada görülmedi; bağımsız insan doğrulaması ve regresyon testi "
                        "olmadan 'giderildi' sayılmaz"}
    return out


def metrics(exp, alarms, videos) -> dict:
    mg = exp["min_ground_truth"]
    by = Counter(a["classification"] for a in alarms)
    confirmed_reviews = sum(1 for k in exp["known_errors"] if k["review"]["status"] == "CONFIRMED_BY_INDEPENDENT_HUMAN")
    obs_sec = sum(v["run"]["video_sec"] or 0 for v in videos.values() if v["run"])
    full_gt = all(v["human_review"]["full_video_ground_truth"] for v in exp["videos"])
    return {
        "precision": {"status": "NOT_MEASURABLE" if by["UNREVIEWED_NEW_ALARM"] else "INSUFFICIENT_GROUND_TRUTH",
                      "value": None,
                      "observed": {"alarms": len(alarms), "verified_true": by["VERIFIED_TRUE_VIOLATION"],
                                   "known_false": by["KNOWN_FALSE_ALARM"], "unreviewed": by["UNREVIEWED_NEW_ALARM"],
                                   "independently_confirmed_reviews": confirmed_reviews},
                      "required": f">= {mg['reviewed_alarms_for_precision']} bağımsız insan tarafından incelenmiş alarm"},
        "recall": {"status": "NOT_MEASURABLE", "value": None,
                   "reason": "doğrulanmış gerçek ihlal yok (verified_true_incidents boş) ve videoların tam ground-truth'u "
                             "yok; örnek kare incelemesi bütün videoya genellenemez",
                   "required": f">= {mg['true_incidents_for_recall']} etiketli gerçek ihlal (docs/data)"},
        "false_alarm_rate_per_camera_hour": {
            "status": "INSUFFICIENT_GROUND_TRUTH", "value": None,
            "observed": {"known_false_alarms": by["KNOWN_FALSE_ALARM"], "observed_video_sec": round(obs_sec, 1),
                         "full_video_ground_truth": full_gt},
            "required": f">= {mg['camera_hours_for_fa_rate']} kamera-saat tam etiketli video + bağımsız inceleme"},
        "system_coverage": {"status": "MEASURED_SYSTEM_ONLY",
                            "definition": "değerlendirilen (UYGUN/IHLAL) kişi-kare / sistemin tespit ettiği kişi-kare; "
                                          "tespit edilmeyen kişileri kapsamaz",
                            "per_video": {k: v["system_coverage"] for k, v in videos.items()}},
        "ground_truth_coverage": {"status": "NOT_MEASURABLE", "reason": "kişi düzeyinde ground-truth yok"},
    }


# ---------------------------------------------------------------- karşılaştırma
def compare(prev: dict, cur: dict) -> dict:
    p, n = prev["summary"], cur["summary"]
    caveats = [f"{k}: {p['environment'].get(k)} -> {n['environment'].get(k)}" for k in
               ("model_sha256", "device", "torch", "ultralytics", "config_sha256") if p["environment"].get(k) != n["environment"].get(k)]

    def same(a, b):
        return (a["video"] == b["video"] and a["violation"] == b["violation"] and abs(a["t"] - b["t"]) <= 1.5
                and iou(a["box"], b["box"]) >= 0.3)
    new = [a for a in n["alarms"] if not any(same(a, b) for b in p["alarms"])]
    gone = [b for b in p["alarms"] if not any(same(a, b) for a in n["alarms"])]
    ke = {k: {"previous": p["known_errors"].get(k), "current": n["known_errors"].get(k)}
          for k in sorted(set(p["known_errors"]) | set(n["known_errors"]))
          if p["known_errors"].get(k) != n["known_errors"].get(k)}
    vids = {}
    for k in sorted(set(p["videos"]) | set(n["videos"])):
        a, b = p["videos"].get(k, {}), n["videos"].get(k, {})
        vids[k] = {f: {"previous": a.get(f), "current": b.get(f)} for f in
                   ("track_ids", "track_ids_ge10_frames", "system_coverage", "incidents", "event_types")
                   if a.get(f) != b.get(f)}
    changed = bool(new or gone or ke or p["integrity"] != n["integrity"])
    return {"previous_run": prev.get("run_id"), "current_run": cur.get("run_id"), "environment_differences": caveats,
            "integrity": {"previous": p["integrity"], "current": n["integrity"]}, "new_alarms": new,
            "disappeared_alarms": gone, "known_error_changes": ke, "video_changes": {k: v for k, v in vids.items() if v},
            "verdict": "CHANGED_REVIEW_REQUIRED" if changed else "NO_DECISION_CHANGE"}


def summarize(res: dict) -> dict:
    m = res["manifest"]
    env = {"model_sha256": m["model"]["sha256"], "device": m["runtime"]["device"], "torch": m["runtime"]["torch"],
           "ultralytics": m["runtime"]["ultralytics"], "config_sha256": m["config_sha256"]}
    return {"environment": env, "integrity": res["integrity"]["status"],
            "alarms": [{k: a[k] for k in ("video", "t", "violation", "box", "classification", "known_error")}
                       for a in res["alarms"]],
            "known_errors": {k: v["status"] for k, v in res["known_errors"].items()},
            "videos": {k: {"track_ids": v["track_ids"], "track_ids_ge10_frames": v["track_ids_ge10_frames"],
                           "system_coverage": v["system_coverage"],
                           "event_types": v["event_types"],
                           "incidents": [{"start": i["start"], "end": i["end"], "closed_by": i["closed_by"],
                                          "missing": i["missing"]} for i in v["incidents"]]}
                       for k, v in res["videos"].items()},
            "metrics_status": {k: v["status"] for k, v in res["metrics"].items()}}


# ---------------------------------------------------------------- Markdown
def render_md(res: dict) -> str:
    m, it = res["manifest"], res["integrity"]
    L = [f"# SteelBench raporu — {res['run_id']}", "",
         "> Saha doğruluğu ölçümü DEĞİLDİR. 3 yerel stok video (LICENSE_UNVERIFIED), insan incelemesi ön/tek "
         "inceleyici. Yanlış alarmlar başarılı tespit sayılmaz.", "",
         "## Özet", "", "| Kalem | Sonuç |", "|---|---|",
         f"| Girdiler (video SHA-256) | {res['inputs_status']} |",
         f"| İzolasyon | **{res['isolation']['status']}** |",
         f"| Kayıt bütünlüğü (defter ↔ DB ↔ snapshot ↔ API) | **{it['status']}** |",
         f"| Alarmlar | {len(res['alarms'])} ({', '.join(f'{k} {v}' for k, v in Counter(a['classification'] for a in res['alarms']).items()) or 'yok'}) |",
         f"| Bilinen hatalar | {', '.join(f'{k} {v['status']}' for k, v in res['known_errors'].items())} |",
         f"| Karar | **{res['verdict']}** (çıkış kodu {res['exit_code']}) |", "",
         "## Ortam (manifest)", "",
         f"- Model `{m['model']['path']}` SHA-256 `{m['model']['sha256']}`; sınıflar: "
         + ", ".join(f"{k}={v}" for k, v in m["model"]["names"].items() if k in ("0", "2", "6", 0, 2, 6)),
         f"- Cihaz **{m['runtime']['device']}**; Python {m['runtime']['python']}, torch {m['runtime']['torch']}, "
         f"Ultralytics {m['runtime']['ultralytics']}, OpenCV {m['runtime']['opencv']}",
         f"- Dedektör: CONF {m['config']['detector']['CONF']}, IMGSZ {m['config']['detector']['IMGSZ']}, "
         f"VID_STRIDE {m['config']['detector']['VID_STRIDE']}, TRACKER {m['config']['detector']['TRACKER']}",
         f"- Tracker: `{json.dumps(m['config']['tracker'], ensure_ascii=False)}`",
         f"- PPEMonitor: `{json.dumps(m['config']['ppe_core'], ensure_ascii=False)}`",
         f"- detector/.env karar anahtarları: {m['config']['env_file_decision_keys'] or 'yok'}",
         f"- Dashboard imajı `{m['dashboard_image'][:19]}…`, Git `{m['git']['commit']}`"
         + (" (çalışma ağacı değişmiş)" if m["git"]["dirty"] else ""), "",
         "| Video | Kamera | SHA-256 | Çözünürlük | Kare |", "|---|---|---|---|---|"]
    L += [f"| {v['key']} | {v['camera_id']} | `{v['sha256'][:16]}…` | {v['width']}×{v['height']} | {v['frames']} |"
          for v in m["videos"]]
    d = it["delivery"]
    L += ["", "## Kayıt bütünlüğü", "",
          f"- Olay: defter {d['ledger_events']}, DB {d['db_events']}; eksik {len(d['lost'])}, fazla "
          f"{len(d['unexpected_in_db'])}, mükerrer (defter/DB) {d['duplicate_ledger_ids']}/{d['duplicate_db_ids']}",
          f"- Snapshot SHA-256 (defter = disk = API): {it['snapshots']['match']}/{it['snapshots']['checked']}",
          f"- Incident yaşam döngüsü: {it['incidents']['ok']}/{it['incidents']['checked']}; run + kişi raporu: "
          f"{it['runs']['ok']}/{it['runs']['checked']} ({it['runs']['db_report_rows']} kişi satırı); API geçmiş: "
          f"{it['api_history_incidents']} incident",
          f"- Outbox sonu: {json.dumps(res['outbox_final'], ensure_ascii=False)}",
          f"- Sorunlar: {json.dumps(it['problems'], ensure_ascii=False) if it['problems'] else 'yok'}", "",
          "## Alarmlar", "", "| Video | t (sn) | Kişi ID | Eksik | Baret/Yelek % | Sınıflama | Bilinen hata |",
          "|---|---|---|---|---|---|---|"]
    L += [f"| {a['video']} | {a['t']} | {a['pid']} | {a['missing']} | {a['helmet_rate']}/{a['vest_rate']} | "
          f"**{a['classification']}** | {a['known_error'] or '—'} |" for a in res["alarms"]] or ["| — | | | | | yok | |"]
    L += ["", "## Bilinen hatalar", "", "| Kimlik | Tür | Video | Durum | Kanıt |", "|---|---|---|---|---|"]
    L += [f"| {k} | {v['kind']} | {v['video']} | **{v['status']}** | `{json.dumps(v['evidence'], ensure_ascii=False)}` |"
          for k, v in res["known_errors"].items()]
    L += ["", "## Metrikler", "", "| Metrik | Durum | Not |", "|---|---|---|"]
    for k, v in res["metrics"].items():
        note = v.get("observed") or v.get("per_video") or v.get("reason")
        L.append(f"| {k} | **{v['status']}** | `{json.dumps(note, ensure_ascii=False)}` |")
    L += ["", "## Video ayrıntısı (inceleme)", ""]
    for k, v in res["videos"].items():
        L += [f"### {k}", "",
              f"- İşlenen kare {v['frames_processed']}, kişi-kare {v['person_frames']}, değerlendirilen "
              f"{v['evaluated_person_frames']} (sistem kapsamı {v['system_coverage']}); ANALIZ nedenleri "
              f"{v['analiz_reasons']}; eşleşmeyen baret/yelek kutusu {v['unmatched_items']}",
              f"- İz kimliği: {v['track_ids']} (≥10 kare: {v['track_ids_ge10_frames']}); doğumlar "
              + ", ".join(f"t={b['t']}→{b['pid']}" for b in v["track_births"][:20]),
              f"- Olaylar: {v['event_types']}; run {v['run']}", "",
              "| İz | İlk–son (sn) | Kare | Değerlendirilen | Durumlar | ANALIZ nedenleri | Baret/yelek eşleşen kare |",
              "|---|---|---|---|---|---|---|"]
        L += [f"| {t['pid']} | {t['first']}–{t['last']} | {t['frames']} | {t['evaluated_frames']} ({t['evaluated_fraction']}) | "
              f"{t['statuses']} | {t['analiz_reasons']} | {t['helmet_matched_frames']}/{t['vest_matched_frames']} |"
              for t in v["tracks"]]
        L += ["", "| Incident | Başlangıç | Bitiş | Kapanış | Eksik |", "|---|---|---|---|---|"]
        L += [f"| ID {i['pid']} | {i['start']} | {i['end']} | {i['closed_by']} | {i['missing']} |" for i in v["incidents"]] \
            or ["| — | | | | |"]
        L.append("")
    iso = res["isolation"]
    L += ["## İzolasyon", "", f"- Yığın `{iso['project']}` (port {iso['port']}); dedektör hedefi `{iso['detector_target']}`",
          f"- Kontroller: `{json.dumps(iso['checks'], ensure_ascii=False)}`", ""]
    cmp_ = res.get("comparison")
    L += ["## Önceki çalıştırmayla karşılaştırma", ""]
    if cmp_:
        L += [f"- Karşılaştırılan: `{cmp_['previous_run']}` → `{cmp_['current_run']}`; **{cmp_['verdict']}**",
              f"- Ortam farkları: {cmp_['environment_differences'] or 'yok'}",
              f"- Yeni alarm: {len(cmp_['new_alarms'])}; kaybolan alarm: {len(cmp_['disappeared_alarms'])}; "
              f"bilinen hata değişimi: {cmp_['known_error_changes'] or 'yok'}",
              f"- Video farkları: `{json.dumps(cmp_['video_changes'], ensure_ascii=False)}`"]
    else:
        L.append("Karşılaştırma yok.")
    L += ["", "Kısıtlar: 3 kısa stok video (toplam ~52 sn), videolarda doğrulanmış gerçek ihlal yok, insan incelemesi "
          "tek inceleyicili ve örnek kare tabanlı; sonuçlar cihaz/sürüme göre küçük farklar gösterebilir (iz ID'leri, "
          "zaman ±0.5 sn)."]
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------- ana akış
def resolve_device(dev: str, probe: dict) -> str:
    if dev != "auto":
        return dev
    return "0" if probe["cuda_available"] else "mps" if probe["mps_available"] else "cpu"


def git_info() -> dict:
    r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=c.ROOT, capture_output=True, text=True)
    d = subprocess.run(["git", "status", "--porcelain", "--", "detector", "dashboard", "tools/reliability"],
                       cwd=c.ROOT, capture_output=True, text=True)
    return {"commit": r.stdout.strip() or None, "dirty": bool(d.stdout.strip())}


def cmd_run(args) -> int:
    if c.PROJECT in FORBIDDEN_PROJECTS or c.env()["REL_PORT"] in ("8000", "8100"):
        print("HATA: SteelBench yalnız kendi izole yığınında (ppe-bench, 8200) çalışır")
        return 1
    exp = json.loads(EXPECTED.read_text(encoding="utf-8"))
    out = c.outdir("steelbench")
    res = {"schema": SCHEMA, "run_id": out.name, "started_utc": c.utc(), "out": str(out)}
    vids, bad = [], []
    for v in exp["videos"]:
        p = c.ROOT / v["path"]
        sha = sha256_file(p) if p.is_file() else None
        vids.append({**{k: v[k] for k in ("key", "camera_id", "path", "frames", "fps", "width", "height")}, "sha256": sha})
        if sha != v["sha256"]:
            bad.append(v["key"])
    res["inputs_status"] = "PASS" if not bad else f"FAIL ({bad})"
    if bad:
        c.write_json(out / "result.json", res)
        print("HATA: girdi videoları beklenen SHA-256 ile uyuşmuyor:", bad)
        return 1
    # sürüm/ayar probu dedektörle AYNI ortamda (detector/.env etkisi dahil)
    probe_env = c.detector_env(out / "outbox", stride=args.stride, extra={
        "DEVICE": args.device if args.device != "auto" else "cpu", "IMGSZ": str(args.imgsz), "MODEL_PATH": args.model})
    pr = subprocess.run([str(c.PY), "-c", PROBE], cwd=c.DETECTOR, env=probe_env, capture_output=True, text=True,
                        timeout=300)
    if pr.returncode != 0:
        print("HATA: sürüm probu başarısız:", pr.stderr.strip()[-800:])
        return 1
    probe = json.loads(pr.stdout.strip().splitlines()[-1])
    args.device_resolved = resolve_device(args.device, probe)
    env_keys = sorted(k for k in _env_file_keys(c.DETECTOR / ".env") if k in DECISION_KEYS)   # yalnız adlar
    config = {"detector": {**probe["detector"], "DEVICE": args.device_resolved}, "ppe_core": probe["ppe_core"],
              "tracker": probe["tracker_config"], "env_file_decision_keys": env_keys}
    model_path = c.DETECTOR / args.model
    res["manifest"] = {"schema": "steelbench-manifest/1", "benchmark": exp["benchmark"], "videos": vids,
                       "model": {"path": args.model, "sha256": sha256_file(model_path), "names": probe["model_names"]},
                       "runtime": {k: probe[k] for k in ("python", "platform", "torch", "ultralytics", "opencv", "numpy")}
                       | {"device": args.device_resolved},
                       "config": config, "config_sha256": hashlib.sha256(json.dumps(   # cihaz hariç karar ayarları
                           {"detector": {k: v for k, v in config["detector"].items() if k != "DEVICE"},
                            "ppe_core": config["ppe_core"], "tracker": config["tracker"]},
                           sort_keys=True, default=str).encode()).hexdigest(),
                       "git": git_info(), "license_note": exp["license_note"]}
    before = isolation_fingerprint()
    if args.build or subprocess.run(["docker", "image", "inspect", "ppe-dashboard:faz9-rel"],
                                    capture_output=True).returncode != 0:
        c.compose("build", "dashboard", timeout=1800)
    c.up(build=False)
    c.reset_data()
    res["manifest"]["dashboard_image"] = subprocess.run(
        ["docker", "inspect", c.container("dashboard"), "--format", "{{.Image}}"], capture_output=True, text=True).stdout.strip()
    det = run_detector(out, args, exp)
    res["detector_run"] = det
    time.sleep(3)
    events, reports, frames = read_ledger(out / "ledger")
    res["integrity"] = integrity(events, reports)
    res["outbox_final"] = outbox_final(out / "outbox")
    cam_to_key = {v["camera_id"]: v["key"] for v in exp["videos"]}
    rep_by = {r["camera_id"]: r for r in reports}
    res["videos"] = {}
    for v in exp["videos"]:
        a = analyze_video(frames.get(v["camera_id"], []), [e for e in events if e["camera_id"] == v["camera_id"]],
                          rep_by.get(v["camera_id"]))
        a["event_types"] = dict(Counter(e["type"] for e in events if e["camera_id"] == v["camera_id"]))
        res["videos"][v["key"]] = a
    res["alarms"] = classify(events, exp, cam_to_key)
    res["known_errors"] = known_error_status(exp, res["alarms"], res["videos"])
    res["metrics"] = metrics(exp, res["alarms"], res["videos"])
    if not args.keep_stack:
        c.compose("stop", timeout=300)
    after = isolation_fingerprint()
    cams = sorted(cam_to_key)
    snaps = res["integrity"].pop("snapshot_names")
    checks = {"main_db_has_no_bench_records": _no_bench_rows("ppe-split", cams),
              "ppe_rel_db_has_no_bench_records": _no_bench_rows("ppe-rel", cams),
              "main_snapshots_have_no_bench_files": not any((c.ROOT / "data" / "snapshots" / n).exists() for n in snaps),
              "production_outbox_has_no_bench_files": not any((c.DETECTOR / "outbox" / f"{x}.sqlite3").exists()
                                                              for x in cams),
              "bench_outbox_used": all((out / "outbox" / f"{x}.sqlite3").exists() for x in cams),
              "ppe_rel_containers_unchanged": before["ppe-rel"]["containers"] == after["ppe-rel"]["containers"],
              "main_containers_unchanged": before["ppe-split"]["containers"] == after["ppe-split"]["containers"],
              "separate_project_and_port": c.PROJECT not in FORBIDDEN_PROJECTS and c.env()["REL_PORT"] not in ("8000", "8100"),
              "detector_target_is_bench": det["dashboard_url"] == c.base_url()}
    info = {k: before[k] == after[k] for k in ("ppe-split", "ppe-rel", "production_outbox", "main_snapshots")}
    res["isolation"] = {"status": "PASS" if all(checks.values()) else "FAIL", "project": c.PROJECT,
                        "port": c.env()["REL_PORT"], "detector_target": det["dashboard_url"], "checks": checks,
                        "unchanged_before_after_info": info,
                        "note": "Ana yığın eşzamanlı kullanılıyorsa önce/sonra sayımları değişebilir; geçme koşulu "
                                "doğrudan kanıttır (bench kimlikleri ve dosyaları ana kaynaklarda yok).",
                        "before": before, "after": after}
    if args.images:
        res["review_images"] = review_images(out, res, frames, exp)
    prev = json.loads(Path(args.compare).read_text(encoding="utf-8")) if args.compare else (
        json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.exists() else None)
    res["summary"] = summarize(res)
    res["comparison"] = compare(prev, res) if prev else None
    hard_fail = res["integrity"]["status"] != "PASS" or res["isolation"]["status"] != "PASS" or det["returncode"] != 0
    review = (any(a["classification"] == "UNREVIEWED_NEW_ALARM" for a in res["alarms"])
              or any(v["status"] != "REPRODUCED" for v in res["known_errors"].values())
              or (res["comparison"] or {}).get("verdict") == "CHANGED_REVIEW_REQUIRED")
    res["exit_code"] = 1 if hard_fail else 2 if review else 0
    res["verdict"] = {0: "BASELINE_REPRODUCED", 1: "FAIL", 2: "REVIEW_REQUIRED"}[res["exit_code"]]
    res["ended_utc"] = c.utc()
    c.write_json(out / "result.json", res)
    (out / "report.md").write_text(render_md(res), encoding="utf-8")
    if args.write_baseline:
        c.write_json(BASELINE, {"schema": "steelbench-baseline/1", "run_id": res["run_id"], "summary": res["summary"]})
    print(f"SteelBench {res['verdict']} (çıkış {res['exit_code']}): bütünlük {res['integrity']['status']}, izolasyon "
          f"{res['isolation']['status']}, alarm {len(res['alarms'])}, bilinen hatalar "
          + ", ".join(f"{k}={v['status']}" for k, v in res["known_errors"].items()))
    print("rapor:", out / "report.md")
    return res["exit_code"]


def _env_file_keys(p: Path) -> list:
    if not p.exists():
        return []
    return [ln.split("=", 1)[0].strip() for ln in p.read_text(encoding="utf-8").splitlines()
            if "=" in ln and not ln.strip().startswith("#")]


def review_images(out: Path, res: dict, frames: dict, exp: dict) -> list:
    """Alarm anlarında tam kare: tüm kişi kutuları (durum + iz + kapı nedeni), baret (sarı) / yelek (yeşil) kutuları.
    Kişi görüntüsü içerir: yalnız Git dışı çıktı dizinine yazılır."""
    import cv2
    rdir = out / "review"
    rdir.mkdir(exist_ok=True)
    col = {"IHLAL": (0, 0, 255), "UYGUN": (0, 200, 0), "ANALIZ": (160, 160, 160)}
    paths = []
    for a in res["alarms"]:
        v = next(x for x in exp["videos"] if x["key"] == a["video"])
        fr = min(frames.get(v["camera_id"], []), key=lambda f: abs(f["t"] - a["t"]), default=None)
        cap = cv2.VideoCapture(str(c.ROOT / v["path"]))
        cap.set(cv2.CAP_PROP_POS_MSEC, a["t"] * 1000)
        ok, img = cap.read()
        cap.release()
        if not ok or fr is None:
            continue
        th = max(2, img.shape[1] // 400)
        for b in fr["helmets"]:
            cv2.rectangle(img, (int(b[0]), int(b[1])), (int(b[2]), int(b[3])), (0, 255, 255), th)
        for b in fr["vests"]:
            cv2.rectangle(img, (int(b[0]), int(b[1])), (int(b[2]), int(b[3])), (0, 255, 0), th)
        for p in fr["persons"]:
            b = p["box"]
            cv2.rectangle(img, (int(b[0]), int(b[1])), (int(b[2]), int(b[3])), col.get(p["status"], (255, 0, 255)), th * 2)
            cv2.putText(img, f"ID{p['pid']} {p['status']} {','.join(p['gate'])}", (int(b[0]), max(20, int(b[1]) - 8)),
                        cv2.FONT_HERSHEY_SIMPLEX, th * 0.5, col.get(p["status"], (255, 0, 255)), th)
        s = 1280 / max(img.shape[:2])
        name = f"{a['video']}_t{a['t']}_{a['known_error'] or 'new'}.jpg"
        cv2.imwrite(str(rdir / name), cv2.resize(img, None, fx=s, fy=s))
        paths.append(f"review/{name}")
    return paths


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="SteelBench — tekrar çalıştırılabilir dedektör referans değerlendirmesi")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--device", default="auto", help="auto | mps | 0 (CUDA) | cpu")
    r.add_argument("--stride", type=int, default=2, help="VID_STRIDE (üretim: 2)")
    r.add_argument("--imgsz", type=int, default=640)
    r.add_argument("--model", default="models/best.pt", help="detector/ dizinine göre model yolu (aday model için)")
    r.add_argument("--timeout", type=float, default=900)
    r.add_argument("--images", action="store_true", help="alarm kareleri için inceleme görselleri (Git dışı)")
    r.add_argument("--compare", help="karşılaştırılacak önceki result.json (varsayılan: steelbench_baseline.json)")
    r.add_argument("--keep-stack", action="store_true", help="ppe-bench yığınını çalışır bırak (127.0.0.1:8200)")
    r.add_argument("--build", action="store_true", help="dashboard imajını yeniden derle")
    r.add_argument("--write-baseline", action="store_true", help="özeti steelbench_baseline.json'a yaz (bilinçli)")
    cp = sub.add_parser("compare")
    cp.add_argument("previous")
    cp.add_argument("current")
    rp = sub.add_parser("report")
    rp.add_argument("result")
    a = ap.parse_args(argv)
    if a.cmd == "run":
        return cmd_run(a)
    if a.cmd == "compare":
        prev, cur = (json.loads(Path(x).read_text(encoding="utf-8")) for x in (a.previous, a.current))
        res = compare(prev, cur)
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0 if res["verdict"] == "NO_DECISION_CHANGE" else 2
    res = json.loads(Path(a.result).read_text(encoding="utf-8"))
    Path(a.result).with_name("report.md").write_text(render_md(res), encoding="utf-8")
    print("rapor:", Path(a.result).with_name("report.md"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
