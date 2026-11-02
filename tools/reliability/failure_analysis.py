"""FAZ 10-A.5A ML hata analizi (tanı aracı; üretim modeli, eşikler ve karar mantığı DEĞİŞMEZ).

  python tools/reliability/failure_analysis.py collect                      # V1-V3 tanı izi + analiz + inceleme paketi
  python tools/reliability/failure_analysis.py collect --baseline <10-A.4 koşu klasörü>   # eşdeğerlik kontrolü
  python tools/reliability/failure_analysis.py analyze <koşu klasörü>        # diag_*.jsonl'den analizi yeniden üret
  python tools/reliability/failure_analysis.py review <koşu klasörü>         # yalnız inceleme paketini yeniden üret

Toplama: üretim karar zinciri pipeline.run_video AYNEN çalışır (YOLO + ByteTrack + match_items + PPEMonitor). Yalnız
gözlem kancaları eklenir:
  1. on_predict_postprocess_end geri çağrısı tracker'dan ÖNCE çalışır -> ham YOLO tespitleri (NMS sonrası, conf>=CONF).
  2. pipeline.extract sarılır -> tracker çıktısı TÜM sınıflarıyla (kimlik + sınıf + güven + kutu).
  3. Ayrı bir model örneği (aynı best.pt) aynı kareyi conf=--probe-conf ile tahmin eder -> eşik altı adaylar.
     Bu "sonda" yalnız tanıdır; üretim akışını etkilemez (ayrı predictor, ayrı tracker yok).
Çıktı tools/reliability/output/<UTC>_failure_analysis/ (Git dışı): diag_V*.jsonl, analysis.json, ANALYSIS.md,
review_pack/ (kör orijinal kareler + ayrı model çizimli kareler + inceleme formu). GT yok -> doğruluk metriği yok."""
import argparse
import csv
import hashlib
import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DET = ROOT / "detector"
sys.path.insert(0, str(DET))
sys.path.insert(0, str(HERE))

VIDEOS = {"V1": DET / "videos" / "16533594_1920_1080_25fps.mp4",
          "V2": DET / "videos" / "5434223-hd_1920_1080_24fps.mp4",
          "V3": DET / "videos" / "8965526-uhd_2160_3840_25fps.mp4"}
PROBE_CLASSES = (0, 2, 6, 7)                      # helmet, vest, Person, no_helmet
NAMES = {0: "helmet", 2: "vest", 6: "Person", 7: "no_helmet"}
TRACK_BUFFER_CALLS = 30                           # bytetrack.yaml track_buffer (izleyici çağrısı)
REVIEW_SEED = 10_5                                # kör sıralama tekrarlanabilir olsun


def log(msg):
    from common import utc
    print(f"[{utc()}] {msg}", flush=True)


def _dets(boxes, with_id=False) -> list:
    if boxes is None or not len(boxes):
        return []
    cls = boxes.cls.cpu().numpy().astype(int)
    conf = boxes.conf.cpu().numpy()
    xyxy = boxes.xyxy.cpu().numpy()
    ids = boxes.id.cpu().numpy().astype(int) if with_id and boxes.id is not None else None
    out = []
    for i in range(len(cls)):
        d = {"c": int(cls[i]), "s": round(float(conf[i]), 4), "b": [round(float(x), 1) for x in xyxy[i]]}
        if with_id:
            d["id"] = None if ids is None else int(ids[i])
        out.append(d)
    return out


# ---------------------------------------------------------------- toplama
def collect_video(model, probe, path: Path, settings: dict, probe_conf: float) -> tuple[dict, list]:
    from evaluation import pipeline
    import telemetry
    raw_q, diag = [], []

    def pre_tracker(predictor):
        for r in predictor.results:
            raw_q.append(_dets(r.boxes))

    cbs = model.callbacks["on_predict_postprocess_end"]
    cbs.insert(0, pre_tracker)                    # tracker geri çağrısından önce
    orig = pipeline.extract

    def wrapped(r):
        pr = probe.predict(r.orig_img, conf=probe_conf, imgsz=settings["imgsz"], device=settings["device"],
                           classes=list(PROBE_CLASSES), verbose=False)[0]
        diag.append({"raw": raw_q.pop(0) if raw_q else None, "tracked": _dets(r.boxes, with_id=True),
                     "probe": [d for d in _dets(pr.boxes) if d["s"] < settings["conf"]]})
        return orig(r)

    pipeline.extract = wrapped
    try:
        run = pipeline.run_video(model, str(path), settings, camera_id="diag")
    finally:
        pipeline.extract = orig
        cbs.remove(pre_tracker)
    if len(diag) != len(run["frames"]) or raw_q:
        raise RuntimeError(f"kanca hizası bozuk: diag={len(diag)} kare={len(run['frames'])} artık={len(raw_q)}")
    wh = (run["width"], run["height"])
    for d, fr in zip(diag, run["frames"]):
        for p in fr["people"]:
            p["gate"] = list(telemetry.gate_reasons(p["box"], p["conf"], wh))
        d.update({k: fr[k] for k in ("idx", "frame", "t", "people", "events")})
    meta = {k: run[k] for k in ("fps", "width", "height", "frame_count", "stride", "processed_frames", "video_sec")}
    meta["events"] = [{k: e[k] for k in ("type", "pid", "t", "missing", "helmet_rate", "vest_rate", "box")}
                      for e in run["events"]]
    meta["unclosed"] = [{k: u.get(k) for k in ("pid", "reason", "t")} for u in run["unclosed"]]
    return meta, diag


def equivalence(diag: list, meta: dict, base_dir: Path, key: str) -> dict:
    """Tanı koşusu FAZ 10-A.4 izini yeniden üretiyor mu (kişi kimliği/kutu/güven + olaylar)?"""
    tr = base_dir / f"trace_{key}.jsonl"
    ev = base_dir / f"events_{key}.jsonl"
    if not tr.exists():
        return {"status": "NO_BASELINE"}
    base = [json.loads(x) for x in tr.read_text(encoding="utf-8").splitlines() if x]
    same = diff = 0
    first_diff = None
    for a, b in zip(base, diag):
        ka = sorted((p["pid"], tuple(round(x) for x in p["box"]), round(p["conf"], 2)) for p in a["people"])
        kb = sorted((p["pid"], tuple(round(x) for x in p["box"]), round(p["conf"], 2)) for p in b["people"])
        if ka == kb:
            same += 1
        else:
            diff += 1
            first_diff = first_diff if first_diff is not None else a["idx"]
    from evaluation.failure import event_key
    be = [json.loads(x) for x in ev.read_text(encoding="utf-8").splitlines() if x] if ev.exists() else []
    return {"status": "IDENTICAL" if diff == 0 and len(base) == len(diag) and event_key(be) == event_key(meta["events"])
            else "DIFFERENT", "frames_baseline": len(base), "frames_diag": len(diag), "frames_identical": same,
            "frames_different": diff, "first_different_idx": first_diff,
            "events_identical": event_key(be) == event_key(meta["events"])}


def cmd_collect(a) -> int:
    from common import stamp
    from ultralytics import YOLO
    from evaluation import cli as ev
    for k, p in VIDEOS.items():
        if not p.exists():
            log(f"BLOCKED: video yok: {k} {p.name}")
            return 3
    settings = {**ev.production_settings(), "vid_stride": a.stride}
    env = ev.environment(settings)
    out = HERE / "output" / f"{stamp()}_failure_analysis"
    out.mkdir(parents=True)
    log(f"çıktı {out.name}; ayarlar {settings}")
    model, probe = YOLO(settings["model_path"]), YOLO(settings["model_path"])
    manifest = {"schema": "ppe-failure-diag/1", "settings": settings, "probe_conf": a.probe_conf,
                "probe_classes": PROBE_CLASSES, "model_sha256": env["checkpoint"]["sha256"],
                "model_names": env["checkpoint"]["names"], "tracker_config": env["tracker_config"],
                "decision_parameters": env["decision_parameters"], "git_head": env["git_head"],
                "git_dirty_files": env["git_dirty_files"],
                "versions": {k: env[k] for k in ("python", "torch", "cuda", "gpu", "ultralytics", "opencv")},
                "videos": {}, "equivalence": {}}
    for key, path in VIDEOS.items():
        log(f"tanı izi {key}")
        meta, diag = collect_video(model, probe, path, settings, a.probe_conf)
        with open(out / f"diag_{key}.jsonl", "w", encoding="utf-8") as f:
            for d in diag:
                f.write(json.dumps(d, default=float) + "\n")
        manifest["videos"][key] = {"file": path.name, "sha256": _sha(path), **meta}
        if a.baseline:
            manifest["equivalence"][key] = equivalence(diag, meta, Path(a.baseline), key)
            log(f"  eşdeğerlik {key}: {manifest['equivalence'][key]['status']}")
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    analyze(out)
    if not a.no_review:
        review_pack(out)
    log("bitti")
    return 0


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------- analiz
def load(out: Path) -> tuple[dict, dict]:
    m = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    diags = {k: [json.loads(x) for x in (out / f"diag_{k}.jsonl").read_text(encoding="utf-8").splitlines() if x]
             for k in m["videos"]}
    return m, diags


def analyze(out: Path) -> dict:
    from evaluation import failure as fa
    m, diags = load(out)
    res = {"manifest": {k: m[k] for k in ("settings", "probe_conf", "model_sha256", "git_head", "versions",
                                           "equivalence")}, "videos": {}}
    for key, diag in diags.items():
        v = m["videos"][key]
        wh = (v["width"], v["height"])
        buffer_sec = TRACK_BUFFER_CALLS * v["stride"] / v["fps"]
        tracks = fa.person_tracks(diag, wh)
        hist = fa.class_history(diag)
        alarms = sorted({e["pid"] for e in v["events"] if e["type"] == "start"})
        base = fa.replay(diag, wh)
        variants = {"baseline_replay": base}
        for name, kw in (("person_conf_min_0.45", {"PERSON_CONF_MIN": 0.45}),
                         ("person_conf_min_0.50", {"PERSON_CONF_MIN": 0.50}),
                         ("person_conf_min_0.55", {"PERSON_CONF_MIN": 0.55}),
                         ("edge_exempt_bottom", {"edge_exempt": ("bottom",)}),
                         ("edge_exempt_all_but_top", {"edge_exempt": ("bottom", "left", "right")}),
                         ("min_person_h_120", {"MIN_PERSON_H_PX": 120}),
                         ("items_from_raw_pre_tracker", {"items": fa.item_boxes_from("raw")}),
                         ("items_conf_0.15_pre_tracker", {"items": fa.item_boxes_from("raw+probe", 0.15)}),
                         ("items_conf_0.10_pre_tracker", {"items": fa.item_boxes_from("raw+probe", 0.10)})):
            r = fa.replay(diag, wh, **kw)
            variants[name] = {"starts": r["starts"], "gated_person_frames": r["gated_person_frames"],
                              "decided_person_frames": r["decided_person_frames"]}
        res["videos"][key] = {
            "frame_wh": wh, "processed_frames": len(diag), "buffer_sec": round(buffer_sec, 2),
            "replay_matches_production": fa.event_key(base["events"]) == fa.event_key(v["events"]),
            "alarms": [{"pid": pid, "series_helmet": fa.item_series(diag, pid, "helmet"),
                        "series_vest": fa.item_series(diag, pid, "vest"),
                        "class_history": hist.get(pid), "track": tracks.get(pid)} for pid in alarms],
            "helmet_causes_gated": fa.item_cause_table(diag, "helmet"),
            "vest_causes_gated": fa.item_cause_table(diag, "vest"),
            "helmet_causes_alarm_pids": {pid: fa.item_cause_table(diag, "helmet", pid=pid) for pid in alarms},
            "tracks": tracks,
            "person_ids_with_other_classes": {pid: hist[pid] for pid in tracks if len(hist.get(pid, {})) > 1},
            "tracker_ids_total_all_classes": len(hist),
            "max_concurrent_persons": fa.max_concurrent(diag),
            "losses": fa.track_losses(diag, wh, buffer_sec),
            "fragment_candidates": fa.fragment_candidates(tracks, max_gap_sec=buffer_sec + 0.5, t0=diag[0]["t"]),
            "concurrent_duplicates": fa.concurrent_duplicates(diag),
            "coverage": fa.coverage_breakdown(diag, wh),
            "replay_variants": variants,
            "raw_vs_tracked": _raw_vs_tracked(diag),
        }
    (out / "analysis.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (out / "ANALYSIS.md").write_text(render(res), encoding="utf-8")
    log(f"analiz yazıldı: {out / 'analysis.json'}")
    return res


def _raw_vs_tracked(diag: list) -> dict:
    """Sınıf başına ham (tracker öncesi) ve tracker çıktısı tespit sayısı: tracker'ın düşürdüğü pay."""
    out = {}
    for c, n in NAMES.items():
        raw = sum(1 for fr in diag for d in fr["raw"] or () if d["c"] == c)
        trk = sum(1 for fr in diag for d in fr["tracked"] if d["c"] == c)
        out[n] = {"raw": raw, "tracked": trk, "dropped_ratio": round(1 - trk / raw, 3) if raw else None}
    return out


def render(res: dict) -> str:
    L = ["# FAZ 10-A.5A tanı özeti (otomatik)", "",
         "GT yok: aşağıdakiler gözlem ve sezgisel sınıflandırmadır; doğruluk metriği değildir.", "",
         f"Eşdeğerlik (FAZ 10-A.4 izi): {json.dumps(res['manifest']['equivalence'], ensure_ascii=False)}", ""]
    for k, v in res["videos"].items():
        L += [f"## {k}", "", f"- yeniden oynatma üretimle aynı: {v['replay_matches_production']}",
              f"- kişi izleri: {len(v['tracks'])}, en çok eşzamanlı: {v['max_concurrent_persons']}, tüm sınıflarda "
              f"tracker kimliği: {v['tracker_ids_total_all_classes']}",
              f"- ham→tracker: {json.dumps(v['raw_vs_tracked'])}",
              f"- baret nedenleri (kapıdan geçen): {json.dumps(v['helmet_causes_gated'])}",
              f"- kapsam: {json.dumps(v['coverage'])}",
              f"- kayıplar: {json.dumps(v['losses'])}",
              f"- parçalanma adayları: {json.dumps(v['fragment_candidates'])}",
              f"- eşzamanlı çift adayları: {json.dumps(v['concurrent_duplicates'])}",
              f"- başka sınıfla sürmüş kişi kimlikleri: {json.dumps(v['person_ids_with_other_classes'])}", "",
              "| varyant | start (pid, t, eksik) | kapıdan geçen | karar verilmiş |", "|---|---|---|---|"]
        for n, r in v["replay_variants"].items():
            L.append(f"| {n} | {r['starts']} | {r['gated_person_frames']} | {r['decided_person_frames']} |")
        L += ["", "| pid | kare | süre | güven med/min | yükseklik med (göreli) | en/boy | kapı | baret/yelek eşleşme | "
              "ham destek |", "|---|---|---|---|---|---|---|---|---|"]
        for t in v["tracks"].values():
            L.append(f"| {t['pid']} | {t['frames']} | {t['first_t']}–{t['last_t']} | {t['conf_median']}/{t['conf_min']}"
                     f" | {t['height_px_median']} ({t['height_rel_median']}) | {t['aspect_wh_median']} | {t['gate']} | "
                     f"{t['helmet_matched_ratio']}/{t['vest_matched_ratio']} | {t['raw_person_support_ratio']} |")
        L.append("")
    return "\n".join(L)


# ---------------------------------------------------------------- insan inceleme paketi
def _pick(m: dict, diags: dict) -> list:
    """İnceleme kareleri: alarm başlangıcı/bağlamı/bitişi + alarm olmayan kontrol aralıkları + kapsam (kenar) ve iz
    parçalanması kareleri. Her öğe: (video, işlenen kare idx, kaynak etiketi)."""
    items = []

    def near(diag, t):
        return min(diag, key=lambda f: abs(f["t"] - t))

    for key, diag in diags.items():
        v = m["videos"][key]
        end_t = diag[-1]["t"]
        spans = []
        inc = {}
        for e in v["events"]:
            inc.setdefault(e["pid"], []).append(e["t"])
        open_pids = {u["pid"] for u in v["unclosed"]}
        for pid in open_pids:
            inc.setdefault(pid, []).append(end_t)
        for pid, ts in inc.items():
            s, e = min(ts), max(ts)
            spans.append((s, e))
            for dt, tag in ((-2, "alarm_context_before"), (-1, "alarm_context_before"), (0, "alarm_start"),
                            (1, "alarm_context_after"), (2, "alarm_context_after")):
                items.append((key, near(diag, s + dt)["idx"], tag))
            if e - s > 4:
                items.append((key, near(diag, (s + e) / 2)["idx"], "alarm_middle"))
            if e > s:
                items.append((key, near(diag, e)["idx"], "alarm_open_at_video_end" if pid in open_pids else "alarm_end"))
        t = 1.0
        while t < end_t:                                # alarm dışı kontrol örnekleri (her 3 sn)
            if all(not (s - 1 <= t <= e + 1) for s, e in spans):
                items.append((key, near(diag, t)["idx"], "control_no_alarm"))
            t += 3.0
        firsts = {}
        for fr in diag:                                 # yeni kişi kimliği başlangıçları (E-04)
            for p in fr["people"]:
                firsts.setdefault(p["pid"], fr["idx"])
        for pid, idx in firsts.items():
            if idx > 1:
                items.append((key, idx, "new_track_start"))
    seen, out = set(), []
    for it in items:
        if (it[0], it[1]) not in seen:
            seen.add((it[0], it[1]))
            out.append(it)
        else:                                           # aynı kare birden çok nedenle seçildiyse etiketleri birleştir
            j = next(i for i, x in enumerate(out) if (x[0], x[1]) == (it[0], it[1]))
            if it[2] not in out[j][2]:
                out[j] = (out[j][0], out[j][1], out[j][2] + "+" + it[2])
    return out


def _overlay(img, fr: dict):
    import cv2
    for d in fr.get("raw") or ():                       # ham eşya/no_helmet tespitleri (ince)
        if d["c"] in (0, 2, 7):
            col = {0: (255, 120, 0), 2: (0, 140, 255), 7: (255, 0, 255)}[d["c"]]
            b = list(map(int, d["b"]))
            cv2.rectangle(img, (b[0], b[1]), (b[2], b[3]), col, 1)
            cv2.putText(img, f"{NAMES[d['c']]} {d['s']:.2f}", (b[0], b[3] + 16), cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 1)
    for p in fr["people"]:
        col = {"UYGUN": (0, 200, 0), "IHLAL": (0, 0, 255)}.get(p["status"], (0, 220, 255))
        x1, y1, x2, y2 = map(int, p["box"])
        cv2.rectangle(img, (x1, y1), (x2, y2), col, 3)
        cv2.putText(img, f"id{p['pid']} {p['status']} c{p['conf']:.2f} {','.join(p.get('gate') or [])}",
                    (x1, max(20, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.8, col, 2)
        for bk, bc in (("helmet_box", (255, 120, 0)), ("vest_box", (0, 140, 255))):
            if p.get(bk):
                b = list(map(int, p[bk]))
                cv2.rectangle(img, (b[0], b[1]), (b[2], b[3]), bc, 3)
    return img


REVIEW_README = """# İnsan inceleme paketi — FAZ 10-A.5A

Bu klasör yerel ve Git dışıdır. Görüntülerde kişiler var: yalnız yetkili inceleyenle paylaşın, kopyalamayın.

## Sıra (önemli)
1. **Önce yalnız `1_blind/` klasörünü açın.** Görüntülerde model çizimi yoktur; dosya adları rastgele sıralı
   kimliklerdir (R001...). Hangi karede alarm olduğu bilgisi bu aşamada verilmez.
2. `review_sheet.csv` içindeki her satırı orijinal görüntüye bakarak doldurun:
   - `kisi_sayisi`: görünen çalışan sayısı (insan olmayan nesneleri saymayın)
   - `baretsiz_calisan`: evet / hayir / belirsiz (baş görünmüyorsa veya örtülüyse **belirsiz**)
   - `yeleksiz_calisan`: evet / hayir / belirsiz
   - `insan_olmayan_nesne_insan_gibi`: modelin karıştırabileceği nesne gözlemi (serbest metin, isteğe bağlı)
   - `konum_notu`: baretsiz/yeleksiz/belirsiz kişinin yeri (sol/orta/sağ, ön/arka)
   - `inceleyen`, `tarih`
3. Formu tamamladıktan **sonra** `2_model_overlay/` klasöründeki aynı kimlikli çizimli görüntülere bakın.
   Çizimli görüntüye bakarak `review_sheet.csv`'deki ilk yanıtları değiştirmeyin; ayrı not gerekiyorsa
   `overlay_sonrasi_not` sütununu kullanın.
4. Alarm kararını yalnız siz verin: `review_template.json` içindeki her alarm için `label` alanına
   `false_alarm`, `true_violation` veya `unclear` yazın; `reviewer` ve `note` doldurun; dosyayı `review.json`
   olarak kaydedin. Doldurulmamış şablon (`label: null`) değerlendirme aracı tarafından **reddedilir**; alarmlar
   o zamana kadar UNREVIEWED kalır.
5. `3_key_open_after_labeling/key.json` kör kimliklerin hangi video/zamana ait olduğunu gösterir; yalnız etiketleme
   bittikten sonra açın.

## Kapsam
Paket alarm başlangıcı, öncesi/sonrası bağlam, alarm ortası/bitişi, **alarm olmayan kontrol aralıkları** (her 3 sn)
ve yeni takip kimliği başlangıç karelerini içerir. Kontrol karelerindeki yanıtlar, kaçırılan gerçek ihlal olup
olmadığını görmek içindir; bu küçük örnek ground-truth yerine geçmez.
"""


def review_pack(out: Path) -> Path:
    import cv2
    m, diags = load(out)
    d = out / "review_pack"
    for sub in ("1_blind", "2_model_overlay", "3_key_open_after_labeling"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    items = _pick(m, diags)
    order = list(range(len(items)))
    random.Random(REVIEW_SEED).shuffle(order)
    key, rows = [], []
    for n, i in enumerate(order, start=1):
        vk, idx, tag = items[i]
        fr = next(f for f in diags[vk] if f["idx"] == idx)
        cap = cv2.VideoCapture(str(VIDEOS[vk]))
        cap.set(cv2.CAP_PROP_POS_FRAMES, fr["frame"])
        ok, img = cap.read()
        cap.release()
        if not ok:
            continue
        rid = f"R{n:03d}"
        cv2.imwrite(str(d / "1_blind" / f"{rid}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 95])
        cv2.imwrite(str(d / "2_model_overlay" / f"{rid}_model.jpg"), _overlay(img.copy(), fr),
                    [cv2.IMWRITE_JPEG_QUALITY, 90])
        key.append({"id": rid, "video": vk, "t": round(fr["t"], 2), "frame": fr["frame"], "source": tag,
                    "model_people": [{k: p[k] for k in ("pid", "status", "conf", "gate")} for p in fr["people"]]})
        rows.append({"id": rid, "goruntu": f"1_blind/{rid}.jpg", "kisi_sayisi": "", "baretsiz_calisan": "",
                     "yeleksiz_calisan": "", "insan_olmayan_nesne_insan_gibi": "", "konum_notu": "",
                     "inceleyen": "", "tarih": "", "overlay_sonrasi_not": ""})
    (d / "3_key_open_after_labeling" / "key.json").write_text(json.dumps(key, ensure_ascii=False, indent=1),
                                                              encoding="utf-8")
    with open(d / "review_sheet.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    tmpl = {"schema_note": "baseline_eval.py --review biçimi; label: false_alarm | true_violation | unclear. "
                           "label null iken araç dosyayı REDDEDER (alarm UNREVIEWED kalır).",
            "incidents": [{"video": vk, "pid": e["pid"], "start_t": e["t"], "label": None, "reviewer": None,
                           "note": None} for vk, v in m["videos"].items() for e in v["events"] if e["type"] == "start"]}
    (d / "review_template.json").write_text(json.dumps(tmpl, ensure_ascii=False, indent=1), encoding="utf-8")
    (d / "README.md").write_text(REVIEW_README, encoding="utf-8")
    log(f"inceleme paketi: {len(rows)} kare -> {d}")
    return d


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--stride", type=int, default=2)
    c.add_argument("--probe-conf", type=float, default=0.05)
    c.add_argument("--baseline", help="FAZ 10-A.4 koşu klasörü (trace_V*.jsonl) — eşdeğerlik kontrolü")
    c.add_argument("--no-review", action="store_true")
    for name in ("analyze", "review"):
        s = sub.add_parser(name)
        s.add_argument("dir")
    a = ap.parse_args()
    if a.cmd == "collect":
        if a.baseline:
            a.baseline = str(Path(a.baseline).resolve())
        sys.exit(cmd_collect(a))
    if a.cmd == "analyze":
        analyze(Path(a.dir).resolve())
    else:
        review_pack(Path(a.dir).resolve())


if __name__ == "__main__":
    main()
