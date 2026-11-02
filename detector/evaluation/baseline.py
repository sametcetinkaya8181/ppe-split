"""FAZ 10-A.4 tekrarlanabilir video taban çizgisi — saf analiz katmanı (GPU/DB/ağ gerektirmez; birim testli).

Girdi: pipeline.run_video çıktısı (üretim karar zincirinin çevrimdışı kopyası: YOLO + ByteTrack + match_items +
PPEMonitor + IncidentTracker) ve isteğe bağlı uçtan uca koşunun (gerçek detector.py -> outbox -> dashboard ->
izole PostgreSQL) kayıtları. Çıktı: makine tarafından okunur sonuç + Markdown.

İlkeler:
  - Ground-truth (ppe-eval-video/1) yoksa precision / recall / yanlış alarm oranı HESAPLANMAZ:
    INSUFFICIENT_GROUND_TRUTH. Gözlenen sayılar (alarm sayısı, kapıdan geçen kişi-kare oranı) ayrı ve "doğruluk
    değildir" notuyla verilir; gerçek kapsam (sahnedeki kişilere göre) NOT_MEASURABLE.
  - Hiçbir alarm varsayılan olarak doğru tespit sayılmaz: insan inceleme kaydı yoksa UNREVIEWED.
  - Bilinen hatalar (known_errors.json) yalnız "sinyal var/yok" olarak işaretlenir; giderildi sayılması için
    sinyalin yokluğu + bağımsız insan doğrulaması + regresyon testi birlikte gerekir.
  - Örnek karelerden bütün videoya genelleme yapılmaz; sayılar işlenen karelerin tamamından hesaplanır."""
import hashlib
from collections import Counter
from pathlib import Path

from .metrics import incidents_from_events

SCHEMA = "ppe-baseline/1"
GT_STATUS = "INSUFFICIENT_GROUND_TRUTH"
NOT_MEASURABLE = "NOT_MEASURABLE"
REVIEW_LABELS = ("false_alarm", "true_violation", "unclear")
SHORT_TRACK_SEC = 1.0


# ---------------------------------------------------------------- manifest
def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def video_entry(path, key: str) -> dict:
    """Video kimliği: içerik özeti + kapsayıcı meta (yol yalnız dosya adı olarak; kullanıcı klasörü yazılmaz)."""
    import cv2
    p = Path(path)
    cap = cv2.VideoCapture(str(p))
    try:
        meta = {"fps": cap.get(cv2.CAP_PROP_FPS), "frame_count": int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),
                "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))}
    finally:
        cap.release()
    return {"key": key, "file": p.name, "sha256": sha256_file(p), "bytes": p.stat().st_size, **meta}


# ---------------------------------------------------------------- kare izi -> özetler
def annotate_gates(frames: list, frame_wh, gate_fn) -> None:
    """Her kişiye PPEMonitor kapısının ret nedenlerini ekler (gate_fn = telemetry.gate_reasons; aynı sabitler)."""
    for f in frames:
        for p in f["people"]:
            p["gate"] = list(gate_fn(p["box"], p["conf"], frame_wh))


def track_summary(frames: list) -> dict:
    """Kişi takibi: kimlik başına süre/durum/kapı nedeni; video düzeyinde kimlik sayısı ve eşzamanlılık.
    'Takip kimliği sayısı' benzersiz kişi sayısı DEĞİLDİR; kimlik/eşzamanlı oranı parçalanma GÖSTERGESİDİR
    (gerçek kişi sayısı ground-truth olmadan bilinmez)."""
    tracks = {}
    concurrent = []
    for f in frames:
        concurrent.append(len(f["people"]))
        for p in f["people"]:
            tr = tracks.setdefault(p["pid"], {"pid": p["pid"], "first_t": f["t"], "last_t": f["t"], "frames": 0,
                                              "status": Counter(), "gate": Counter(), "decided_frames": 0,
                                              "max_h_px": 0.0})
            tr["last_t"] = f["t"]
            tr["frames"] += 1
            tr["status"][p["status"]] += 1
            tr["decided_frames"] += bool(p["decided"])
            for g in p.get("gate") or ["evaluated"]:
                tr["gate"][g] += 1
            tr["max_h_px"] = max(tr["max_h_px"], p["box"][3] - p["box"][1])
    out = []
    for tr in sorted(tracks.values(), key=lambda x: (x["first_t"], x["pid"])):
        dur = round(tr["last_t"] - tr["first_t"], 2)
        out.append({**tr, "duration_sec": dur, "status": dict(tr["status"]), "gate": dict(tr["gate"]),
                    "max_h_px": round(tr["max_h_px"], 1), "ever_decided": tr["decided_frames"] > 0,
                    "dominant_gate": tr["gate"].most_common(1)[0][0] if tr["gate"] else None})
    nz = sorted(c for c in concurrent if c)
    max_conc = max(concurrent) if concurrent else 0
    return {"track_ids": len(out), "max_concurrent_persons": max_conc,
            "median_concurrent_persons": nz[len(nz) // 2] if nz else 0,
            "ids_per_max_concurrent": round(len(out) / max_conc, 2) if max_conc else None,
            "short_tracks_lt_1s": sum(1 for t in out if t["duration_sec"] < SHORT_TRACK_SEC),
            "never_decided_tracks": sum(1 for t in out if not t["ever_decided"]), "tracks": out}


def coverage(frames: list) -> dict:
    """Sistemin GÖZLEDİĞİ kişi-karelerin kapıdan geçme oranı. Sahnedeki gerçek kişilere göre kapsam değildir
    (kaçırılan kişiler bu sayıda yoktur) -> coverage_vs_truth = NOT_MEASURABLE."""
    total, evaluated, reasons, status = 0, 0, Counter(), Counter()
    for f in frames:
        for p in f["people"]:
            total += 1
            g = p.get("gate") or []
            evaluated += not g
            reasons.update(g)
            status[p["status"]] += 1
    return {"observed_person_frames": total, "gate_passed": evaluated,
            "gate_passed_ratio": round(evaluated / total, 4) if total else None,
            "unevaluated_by_reason": dict(reasons), "status_frames": dict(status),
            "coverage_vs_truth": NOT_MEASURABLE,
            "note": "yalnız modelin bulduğu kişiler; kaçırılan kişiler ve gerçek kapsam ground-truth gerektirir"}


def incidents(run: dict, video_key: str) -> list:
    """Incident yaşam döngüsü (start -> ongoing* -> end | stale | run_end) + başlangıç karesindeki kişi durumu."""
    inc = incidents_from_events(run["events"], video_id=video_key, unclosed=run.get("unclosed", ()))
    ongoing = Counter(e["incident_id"] for e in run["events"] if e["type"] == "ongoing")
    for i in inc:
        i["ongoing_events"] = ongoing.get(i["incident_id"], 0)
        f = nearest_frame(run["frames"], i["start"], pid=i["pid"])
        i["start_frame"] = None if f is None else f["frame"]
        p = next((x for x in (f or {}).get("people", []) if x["pid"] == i["pid"]), None)
        i["at_start"] = None if p is None else {k: p.get(k) for k in ("box", "conf", "h_rate", "v_rate", "helmet_box",
                                                                     "vest_box", "gate")}
        i["review"] = "UNREVIEWED"
    return inc


def nearest_frame(frames: list, t: float, pid=None, tol: float = 0.25):
    """Olay zamanına en yakın işlenen kare (PPEMonitor olay zamanını yuvarlar; kare zamanı idx*stride/fps).
    pid verilirse o kişinin göründüğü kareler tercih edilir. tol dışında kare yoksa None."""
    cands = [f for f in frames if abs(f["t"] - t) <= tol]
    if pid is not None:
        cands = [f for f in cands if any(p["pid"] == pid for p in f["people"])] or cands
    return min(cands, key=lambda f: abs(f["t"] - t), default=None)


def event_counts(events: list) -> dict:
    c = Counter(e["type"] for e in events)
    return {"start": c.get("start", 0), "ongoing": c.get("ongoing", 0), "end": c.get("end", 0),
            "orphan": sum(1 for e in events if e.get("orphan")), "total": len(events)}


def analyze_video(run: dict, key: str, gate_fn) -> dict:
    annotate_gates(run["frames"], (run["width"], run["height"]), gate_fn)
    return {"key": key, "processed_frames": run["processed_frames"], "video_sec": round(run["video_sec"], 2),
            "events": event_counts(run["events"]), "incidents": incidents(run, key),
            "tracking": track_summary(run["frames"]), "coverage": coverage(run["frames"]),
            "person_reports": run.get("report", []), "contract_errors": run.get("contract_errors", 0),
            "unclosed": [{k: u.get(k) for k in ("pid", "reason", "t")} for u in run.get("unclosed", [])],
            "timing": {k: (run.get("timing_ms") or {}).get(k) for k in ("inference_ms", "bytetrack_ms",
                                                                        "frame_total_ms")}}


# ---------------------------------------------------------------- insan incelemesi ve bilinen hatalar
def apply_review(videos: dict, review: dict | None, tol_sec: float = 1.0) -> list:
    """review.json: {"incidents": [{"video": "V2", "pid": 5, "start_t": 8.4, "label": "false_alarm",
    "reviewer": "...", "note": "..."}]}. Eşleşme: video + pid + başlangıç zamanı (±tol). Eşleşmeyen kayıtlar döner."""
    unmatched = []
    for r in (review or {}).get("incidents", []):
        if r.get("label") not in REVIEW_LABELS:
            raise ValueError(f"geçersiz inceleme etiketi: {r.get('label')!r}")
        v = videos.get(r["video"])
        hit = None if v is None else next((i for i in v["incidents"] if i["pid"] == r["pid"]
                                           and abs(i["start"] - r["start_t"]) <= tol_sec), None)
        if hit is None:
            unmatched.append(r)
        else:
            hit["review"] = r["label"]
            hit["reviewer"] = r.get("reviewer")
    return unmatched


def _signal(known: dict, v: dict) -> tuple[bool, list, dict]:
    rule, inc = known["signal"], v["incidents"]
    if rule["kind"] == "incident_missing":
        hits = [i for i in inc if rule["item"] in i["items"]]
        return bool(hits), [i["incident_id"] for i in hits], {"incidents_with_item": len(hits)}
    if rule["kind"] == "any_incident":
        return bool(inc), [i["incident_id"] for i in inc], {"incidents": len(inc)}
    if rule["kind"] == "edge_unevaluated_track":
        trs = [t for t in v["tracking"]["tracks"] if t["dominant_gate"] == "edge" and not t["ever_decided"]
               and t["duration_sec"] >= rule.get("min_sec", 2.0)]
        return bool(trs), [], {"edge_undecided_tracks": [t["pid"] for t in trs],
                               "edge_unevaluated_person_frames": v["coverage"]["unevaluated_by_reason"].get("edge", 0)}
    if rule["kind"] == "id_fragmentation":
        r = v["tracking"]["ids_per_max_concurrent"]
        return (r is not None and r >= rule["min_ratio"]), [], {"track_ids": v["tracking"]["track_ids"],
                                                                "max_concurrent": v["tracking"]["max_concurrent_persons"],
                                                                "ratio": r}
    raise ValueError(f"bilinmeyen sinyal kuralı: {rule['kind']}")


def evaluate_known_errors(known: list, videos: dict, manifest_videos: dict, review: dict | None) -> list:
    """Her bilinen hata için: sinyal (var/yok), ilgili incident'lar etiketlenir (asla doğru tespit sayılmaz),
    çözüm durumu yalnız sinyal yok + insan onayı + regresyon testi varsa RESOLVED_CONFIRMED."""
    confirmations = (review or {}).get("known_errors", {})
    out = []
    for k in known:
        v = videos.get(k["video"])
        if v is None:
            out.append({**_ke_head(k), "signal": "NOT_RUN", "resolution": "NOT_CONFIRMED"})
            continue
        present, inc_ids, detail = _signal(k, v)
        for i in v["incidents"]:
            if i["incident_id"] in inc_ids:
                i.setdefault("known_errors", []).append(k["id"])
        conf = confirmations.get(k["id"]) or {}
        if present:
            resolution = "OPEN"
        elif conf.get("human_confirmed_by") and conf.get("regression_test"):
            resolution = "RESOLVED_CONFIRMED"
        else:
            resolution = "SIGNAL_ABSENT_UNCONFIRMED"
        mv = manifest_videos.get(k["video"]) or {}
        out.append({**_ke_head(k), "signal": "PRESENT" if present else "ABSENT", "detail": detail,
                    "incident_ids": inc_ids, "resolution": resolution,
                    "reference_sha_verified": bool(k.get("reference_sha256")) and k.get("reference_sha256") == mv.get(
                        "sha256")})
    return out


def _ke_head(k):
    return {"id": k["id"], "video": k["video"], "kind": k["kind"], "title": k["title"],
            "reference_source": k.get("reference_source")}


def metric_block(gt_results: list | None) -> dict:
    """GT yoksa doğruluk metrikleri hesaplanmaz. GT varsa evaluate_against_gt/aggregate çıktısı aynen taşınır."""
    if not gt_results:
        return {"status": GT_STATUS, "precision": GT_STATUS, "recall": GT_STATUS, "false_alarm_rate": GT_STATUS,
                "coverage_vs_truth": NOT_MEASURABLE,
                "reason": "videolar için ppe-eval-video/1 ground-truth yok; incelenen örnek kareler bütün videoya "
                          "genellenmez"}
    return {"status": "MEASURED", "per_video": gt_results}


def review_summary(videos: dict) -> dict:
    c = Counter(i["review"] for v in videos.values() for i in v["incidents"])
    return {"alarms": sum(c.values()), **{k: c.get(k, 0) for k in ("UNREVIEWED",) + REVIEW_LABELS},
            "note": "incelenmemiş alarm doğru tespit sayılmaz"}


# ---------------------------------------------------------------- teslim (uçtan uca) karşılaştırması
def delivery_compare(ledger: list, db_rows: list) -> dict:
    """ledger: dedektörün outbox'a commit ettiği olaylar [{event_id, type, ...}]; db_rows: izole DB satırları
    [{event_id, event_type, ...}]. Eksik / fazla / mükerrer ayrı raporlanır."""
    led = Counter(str(e["event_id"]) for e in ledger)
    db = Counter(str(r["event_id"]) for r in db_rows)
    missing = sorted(set(led) - set(db))
    extra = sorted(set(db) - set(led))
    return {"detector_events": len(led), "db_rows": sum(db.values()), "db_distinct": len(db),
            "missing_in_db": missing, "extra_in_db": extra,
            "duplicate_in_db": sorted(k for k, n in db.items() if n > 1),
            "duplicate_in_detector": sorted(k for k, n in led.items() if n > 1),
            "by_type_detector": dict(Counter(e["type"] for e in ledger)),
            "by_type_db": dict(Counter(r["event_type"] for r in db_rows)),
            "ok": not missing and not extra and all(n == 1 for n in db.values())}


# ---------------------------------------------------------------- çalıştırmaları karşılaştırma
_ENV_KEYS = ("torch", "cuda", "gpu", "ultralytics", "opencv", "numpy", "platform", "device")


def run_digest(result: dict) -> dict:
    d = {"run_id": result["run_id"], "env": {k: result["manifest"]["environment"].get(k) for k in _ENV_KEYS},
         "model_sha256": result["manifest"]["model"]["sha256"], "settings": result["manifest"]["settings"],
         "videos": {}}
    for k, v in result["videos"].items():
        d["videos"][k] = {"sha256": result["manifest"]["videos"][k]["sha256"], "events": v["events"],
                          "alarms": len(v["incidents"]),
                          "alarm_items": sorted("+".join(i["items"]) for i in v["incidents"]),
                          "track_ids": v["tracking"]["track_ids"],
                          "max_concurrent": v["tracking"]["max_concurrent_persons"],
                          "gate_passed_ratio": v["coverage"]["gate_passed_ratio"],
                          "processed_frames": v["processed_frames"]}
    d["known_errors"] = {k["id"]: k["signal"] for k in result.get("known_errors", [])}
    if result.get("delivery"):
        d["delivery_ok"] = result["delivery"]["ok"]
    return d


def compare(prev: dict, cur: dict) -> dict:
    """İki run_digest (veya geçmiş referans) arasında kontrollü fark. Girdi kimliği (video/model özeti) farklıysa
    'comparable': False — farklar model/karar değişikliğine bağlanamaz."""
    same_inputs = prev.get("model_sha256") == cur.get("model_sha256") and all(
        prev.get("videos", {}).get(k, {}).get("sha256") == v.get("sha256") for k, v in cur.get("videos", {}).items())
    env_diff = {k: [prev.get("env", {}).get(k), cur.get("env", {}).get(k)] for k in _ENV_KEYS
                if prev.get("env", {}).get(k) != cur.get("env", {}).get(k)}
    set_diff = {k: [prev.get("settings", {}).get(k), v] for k, v in cur.get("settings", {}).items()
                if prev.get("settings", {}).get(k) != v}
    vids = {}
    for k in sorted(set(prev.get("videos", {})) | set(cur.get("videos", {}))):
        a, b = prev.get("videos", {}).get(k), cur.get("videos", {}).get(k)
        if a is None or b is None:
            vids[k] = {"only_in": "current" if a is None else "previous"}
            continue
        vids[k] = {f: [a.get(f), b.get(f)] for f in ("alarms", "alarm_items", "track_ids", "gate_passed_ratio",
                                                     "processed_frames") if a.get(f) != b.get(f)}
        ev = {t: [a.get("events", {}).get(t), b.get("events", {}).get(t)] for t in ("start", "ongoing", "end")
              if a.get("events", {}).get(t) != b.get("events", {}).get(t)}
        if ev:
            vids[k]["events"] = ev
    ke = {k: [prev.get("known_errors", {}).get(k), v] for k, v in cur.get("known_errors", {}).items()
          if prev.get("known_errors", {}).get(k) != v}
    return {"comparable_inputs": same_inputs, "environment_diff": env_diff, "settings_diff": set_diff,
            "videos": vids, "known_errors": ke, "identical_summary": not (vids_changed(vids) or ke)}


def vids_changed(vids: dict) -> bool:
    return any(v for v in vids.values())


# ---------------------------------------------------------------- Markdown
def render_markdown(r: dict) -> str:
    m = r["manifest"]
    env = m["environment"]
    L = [f"# Taban çizgisi — {r['run_id']}", "",
         f"Şema `{SCHEMA}`. Üretim: {r['created_utc']}. Git `{env.get('git_head', '?')[:12]}`"
         f"{' (kirli ağaç: ' + str(env.get('git_dirty_files')) + ' dosya)' if env.get('git_dirty_files') else ''}.",
         f"Cihaz: {env.get('gpu') or env.get('device')} | torch {env.get('torch')} (CUDA {env.get('cuda')}) | "
         f"Ultralytics {env.get('ultralytics')} | OpenCV {env.get('opencv')} | {env.get('platform')}",
         f"Model `{m['model']['file']}` SHA-256 `{m['model']['sha256']}`; ayarlar: "
         + ", ".join(f"{k}={v}" for k, v in m["settings"].items()), "",
         "## Doğruluk metrikleri", "",
         f"**{r['metrics']['status']}** — precision / recall / yanlış alarm oranı: {r['metrics'].get('precision')}; "
         f"gerçek kapsam: {r['metrics'].get('coverage_vs_truth')}. {r['metrics'].get('reason', '')}", "",
         "## Video özeti (çevrimdışı iz: üretim karar zinciri)", "",
         "| Video | SHA-256 (ilk 12) | İşlenen kare | Olay start/ongoing/end | Alarm (incident) | Takip kimliği / "
         "en çok eşzamanlı | Kapıdan geçen kişi-kare | Değerlendirilemeyen (neden) |",
         "|---|---|---|---|---|---|---|---|"]
    for k, v in r["videos"].items():
        e, t, c = v["events"], v["tracking"], v["coverage"]
        L.append(f"| {k} `{m['videos'][k]['file']}` | `{m['videos'][k]['sha256'][:12]}` | {v['processed_frames']} | "
                 f"{e['start']}/{e['ongoing']}/{e['end']} | {len(v['incidents'])} | {t['track_ids']} / "
                 f"{t['max_concurrent_persons']} | {c['gate_passed']}/{c['observed_person_frames']} "
                 f"({_pct(c['gate_passed_ratio'])}) | {_reasons(c['unevaluated_by_reason'])} |")
    L += ["", "## Alarmlar ve insan incelemesi", "",
          "| Video | Kişi (track) | Başlangıç sn | Bitiş | Eksik | Bilinen hata | İnceleme |", "|---|---|---|---|---|---|---|"]
    for k, v in r["videos"].items():
        for i in v["incidents"]:
            L.append(f"| {k} | {i['pid']} | {i['start']:.1f} | {_end(i)} | {'+'.join(i['items']) or '-'} | "
                     f"{', '.join(i.get('known_errors', [])) or '-'} | **{i['review']}** |")
    rs = r["review_summary"]
    L += ["", f"Alarm {rs['alarms']}: incelenmemiş {rs['UNREVIEWED']}, yanlış alarm {rs['false_alarm']}, doğru ihlal "
              f"{rs['true_violation']}, belirsiz {rs['unclear']}. {rs['note']}.", "",
          "## Bilinen hatalar", "", "| Kimlik | Video | Tür | Sinyal | Ayrıntı | Çözüm durumu |", "|---|---|---|---|---|---|"]
    for k in r["known_errors"]:
        L.append(f"| {k['id']} | {k['video']} | {k['title']} | **{k['signal']}** | {k.get('detail', '')} | "
                 f"{k['resolution']} |")
    if r.get("delivery"):
        d = r["delivery"]
        L += ["", "## Uçtan uca teslim (gerçek detector.py -> outbox -> dashboard -> izole PostgreSQL)", "",
              f"Sonuç: **{'PASS' if d['ok'] else 'FAIL'}** — dedektör {d['detector_events']} olay, DB {d['db_rows']} satır "
              f"({d['db_distinct']} tekil); eksik {len(d['missing_in_db'])}, fazla {len(d['extra_in_db'])}, DB'de mükerrer "
              f"{len(d['duplicate_in_db'])}. Türler (dedektör / DB): {d['by_type_detector']} / {d['by_type_db']}."]
        for key in ("incidents", "reports", "snapshots", "runs"):
            if key in r.get("e2e", {}):
                L.append(f"- {key}: {r['e2e'][key]}")
    if r.get("isolation"):
        L += ["", "## İzolasyon", "", f"**{'PASS' if r['isolation']['ok'] else 'FAIL'}** — "
              + "; ".join(f"{k}: {v}" for k, v in r["isolation"]["checks"].items())]
    for title, key in (("Önceki çalıştırmayla karşılaştırma", "comparison_previous"),
                       ("Geçmiş referansla karşılaştırma (doğrulanmamış)", "comparison_reference")):
        if r.get(key):
            c = r[key]
            L += ["", f"## {title}", "", f"Girdiler karşılaştırılabilir: **{c['comparable_inputs']}**; ortam farkı: "
                  f"{c['environment_diff'] or 'yok'}; ayar farkı: {c['settings_diff'] or 'yok'}.",
                  f"Video farkları: {c['videos']}; bilinen hata sinyali farkı: {c['known_errors'] or 'yok'}."]
    if r.get("limitations"):
        L += ["", "## Kısıtlar", ""] + [f"- {x}" for x in r["limitations"]]
    return "\n".join(L) + "\n"


def _pct(x):
    return "-" if x is None else f"%{100 * x:.1f}"


def _reasons(d):
    return ", ".join(f"{k} {v}" for k, v in sorted(d.items(), key=lambda kv: -kv[1])) or "-"


def _end(i):
    return f"{i['end']:.1f} ({i['closed_by']})" if i.get("end") is not None else (i.get("closed_by") or "açık")
