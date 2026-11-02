"""FAZ 10-A.5B kontrollü deneyler (saf fonksiyonlar, birim testli). Üretim ppe_core.PPEMonitor / IncidentTracker /
tracker yapılandırması DEĞİŞMEZ; burada yalnız ayrı, isimli deney varyantları vardır.

Akış (tools/reliability/experiments_5b.py):
  1. Tespit önbelleği (GPU, bir kez): kare başına TÜM sınıflar conf >= 0.05, tam hassasiyet  -> {"t", "dets"}
  2. track_offline(): Ultralytics BYTETracker önbellekten yeniden oynatılır. Üretim girdisiyle (conf >= 0.25, tüm
     sınıflar) FAZ 10-A.5A tanı izini birebir vermelidir (eşdeğerlik); varyantlar: yalnız Person, düşük skor aşaması,
     tampon/eşik.
  3. decision_frames() + run_decision(): ExperimentalMonitor (varsayılan yapılandırma = PPEMonitor, birebir) +
     protocol.IncidentTracker yaşam döngüsü.
Hiçbir fonksiyon doğruluk (precision/recall) üretmez: ground-truth yok. Varyant çıktıları yalnız "ne olurdu"dur."""
from collections import Counter, defaultdict, deque
from statistics import median

import ppe_core as core
import protocol

from .metrics import box_iou

C_HELMET, C_GLOVES, C_VEST, C_PERSON, C_NO_HELMET = 0, 1, 2, 6, 7
PROD_CONF = 0.25
TRACKER_DEFAULTS = {"tracker_type": "bytetrack", "track_high_thresh": 0.25, "track_low_thresh": 0.1,
                    "new_track_thresh": 0.25, "track_buffer": 30, "match_thresh": 0.8, "fuse_score": True}


# ---------------------------------------------------------------- takip (offline ByteTrack)
def track_offline(frames: list, *, conf_in: float = PROD_CONF, classes=None, wh=(1920, 1080), **tracker_cfg) -> dict:
    """frames: [{"t", "dets": [{"c","s","b"}]}] (NMS sırası korunmuş). Tracker'a yalnız s >= conf_in (ve classes)
    tespitleri verilir — üretimde model.track(conf=0.25) ile aynı girdi. Döner: {"tracked": kare başına
    [{"id","c","s","b"}], "new_tracks": yeni kişi izi başlangıçlarında kayıp izlerin Kalman tahminiyle en iyi IoU}."""
    import numpy as np
    from types import SimpleNamespace
    from ultralytics.engine.results import Boxes
    from ultralytics.trackers.byte_tracker import BYTETracker
    trk = BYTETracker(SimpleNamespace(**{**TRACKER_DEFAULTS, **tracker_cfg}))
    out, starts, seen = [], [], set()
    for i, fr in enumerate(frames):
        rows = [d["b"] + [d["s"], d["c"]] for d in fr["dets"]
                if d["s"] >= conf_in and (classes is None or d["c"] in classes)]
        data = np.asarray(rows, dtype=np.float32).reshape(-1, 6)
        res = trk.update(Boxes(data, (wh[1], wh[0])), None)
        if len(res):                                    # Ultralytics Results.update kutuyu görüntüye kırpar
            res[:, [0, 2]] = res[:, [0, 2]].clip(0, wh[0])
            res[:, [1, 3]] = res[:, [1, 3]].clip(0, wh[1])
            cur = [{"id": int(r[4]), "c": int(r[6]), "s": float(r[5]), "b": [float(x) for x in r[:4]]} for r in res]
        elif any(not s.is_activated for s in trk.tracked_stracks):
            cur = []                                    # Ultralytics: onaysız yeni iz varken sonuç boşaltılır
        else:                                           # Ultralytics: aksi halde ham tespitler KİMLİKSİZ kalır
            cur = [{"id": None, "c": int(r[5]), "s": float(r[4]), "b": [float(x) for x in r[:4]]} for r in data]
        # update sonrası kayıp izlerin kutusu bu kare için Kalman tahminidir (multi_predict uygulandı, eşleşmedi)
        lost_pred = [(int(s.track_id), s.xyxy) for s in trk.lost_stracks if int(s.cls) == C_PERSON]
        for d in cur:
            if d["c"] == C_PERSON and d["id"] is not None and d["id"] not in seen and i > 0:
                best = max(((box_iou(d["b"], p.tolist()), pid) for pid, p in lost_pred), default=(0.0, None))
                starts.append({"frame_i": i, "t": round(fr["t"], 2), "pid": d["id"], "conf": round(d["s"], 3),
                               "best_lost_pid": best[1], "best_lost_iou": round(best[0], 3),
                               "iou_x_conf": round(best[0] * d["s"], 3)})
        seen.update(d["id"] for d in cur if d["c"] == C_PERSON and d["id"] is not None)
        out.append(cur)
    return {"tracked": out, "new_tracks": starts}


def tracked_equivalence(tracked: list, diag: list, tol: float = 0.15) -> dict:
    """track_offline çıktısı ↔ FAZ 10-A.5A tanı izi 'tracked' (kimlik, sınıf, kutu ±tol px, güven ±1e-3)."""
    same = diff = 0
    first = None
    for i, (a, b) in enumerate(zip(tracked, diag)):
        # tanı izi güveni 4 haneye, kutuyu 0.1 px'e yuvarlar: yeniden yuvarlama yerine sıralayıp toleransla karşılaştır
        def key(d):
            return (d["id"] or 0, d["c"], -d["s"])
        sa, sb = sorted(a, key=key), sorted(b["tracked"], key=key)
        ok = len(sa) == len(sb) and all(
            x["id"] == y["id"] and x["c"] == y["c"] and abs(x["s"] - y["s"]) <= 1e-3
            and max(abs(p - q) for p, q in zip(x["b"], y["b"])) <= tol for x, y in zip(sa, sb))
        same += ok
        diff += not ok
        if not ok and first is None:
            first = i
    return {"status": "IDENTICAL" if diff == 0 and len(tracked) == len(diag) else "DIFFERENT", "frames": len(diag),
            "frames_identical": same, "frames_different": diff, "first_different_i": first}


def tracking_summary(tracked: list, times: list, wh, buffer_sec: float) -> dict:
    """GT'siz kimlik kararlılığı göstergeleri (sezgisel; kimlik değişimi SAYISI değildir)."""
    from . import failure as fa
    diag = [{"t": t, "tracked": tr, "raw": None,
             "people": [{"pid": d["id"], "box": d["b"], "conf": d["s"], "decided": False, "gate": []}
                        for d in tr if d["c"] == C_PERSON and d["id"] is not None]} for t, tr in zip(times, tracked)]
    tracks = fa.person_tracks(diag, wh)
    hist = fa.class_history(diag)
    frag = fa.fragment_candidates(tracks, max_gap_sec=buffer_sec + 0.5, t0=times[0] if times else 0.0)
    lens = [t["frames"] for t in tracks.values()]
    return {"person_ids": len(tracks), "person_ids_ge5_frames": sum(1 for n in lens if n >= 5),
            "person_ids_with_other_classes": sorted(p for p in tracks if len(hist.get(p, {})) > 1),
            "tracker_ids_all_classes": len(hist), "max_concurrent_persons": fa.max_concurrent(diag),
            "fragment_candidates": len(frag), "fragment_pairs": [(f["prev_pid"], f["next_pid"]) for f in frag],
            "concurrent_duplicates": fa.concurrent_duplicates(diag),
            "person_frames": sum(lens), "median_track_frames": median(lens) if lens else 0}


# ---------------------------------------------------------------- karar girdisi
def decision_frames(frames: list, tracked: list, *, items: str = "tracked", min_conf: float = PROD_CONF,
                    crop: list | None = None) -> list:
    """Karar katmanı girdisi. items='tracked' (üretim: tracker çıktısındaki baret/yelek) | 'raw' (tracker öncesi
    ham tespit >= min_conf; yalnız-Person takibinde zorunlu). Kişiler tracker çıktısından ve s >= min_conf (düşük skor
    aşamasıyla sürdürülen izler karara min_conf altında girmez). crop: kare başına {kişi kutusu indeksi: ikinci aşama
    güveni} (isteğe bağlı)."""
    out = []
    for i, (fr, tr) in enumerate(zip(frames, tracked)):
        persons = [(d["id"], d["b"], d["s"]) for d in tr
                   if d["c"] == C_PERSON and d["id"] is not None and d["s"] >= min_conf]
        src = tr if items == "tracked" else fr["dets"]
        helmets = [d["b"] for d in src if d["c"] == C_HELMET and d["s"] >= min_conf]
        vests = [d["b"] for d in src if d["c"] == C_VEST and d["s"] >= min_conf]
        out.append({"t": fr["t"], "persons": persons, "helmets": helmets, "vests": vests, "dets": fr["dets"],
                    "crop": _crop_for(persons, fr, crop[i] if crop else None)})
    return out


def _crop_for(persons, fr, crop_i) -> dict:
    """tracker kişi kutusu -> en iyi örtüşen ham Person tespitinin ikinci aşama (kırpıntı) güveni."""
    if not crop_i:
        return {}
    out = {}
    for pid, box, _ in persons:
        best = max(((box_iou(box, fr["dets"][j]["b"]), v) for j, v in crop_i.items()), default=(0.0, None))
        out[int(pid)] = best[1] if best[0] >= 0.5 else None
    return out


def frames_from_diag(diag: list) -> list:
    """FAZ 10-A.5A tanı izinden karar girdisi (kişi + üretimde eşleşen eşya kutuları): üretim eşdeğerliği için."""
    out = []
    for fr in diag:
        dets = list(fr.get("raw") or ()) + list(fr.get("probe") or ())
        out.append({"t": fr["t"], "persons": [(p["pid"], p["box"], p["conf"]) for p in fr["people"]],
                    "helmet_map": {p["pid"]: p["helmet_box"] for p in fr["people"] if p.get("helmet_box")},
                    "vest_map": {p["pid"]: p["vest_box"] for p in fr["people"] if p.get("vest_box")},
                    "dets": dets, "crop": {}})
    return out


# ---------------------------------------------------------------- E-01: baret kanıt durumu
def helmet_evidence(pid, box, dets, helmet_map, *, floor: float = 0.10, nh_conf: float = PROD_CONF) -> str:
    """Kişi-kare baret kanıtı: present (eşleşti) | weak (baş bölgesinde eşleşmemiş/eşik altı baret adayı >= floor;
    no_helmet ile çelişki de buraya) | absent (no_helmet >= nh_conf, baret adayı yok) | none (hiç kanıt yok)."""
    if pid in helmet_map:
        return "present"
    head = core.head_region(box)
    others = [b for p, b in helmet_map.items() if p != pid]
    weak = any(d["c"] == C_HELMET and d["s"] >= floor and core.inside(core.center(d["b"]), head)
               and not any(box_iou(d["b"], o) >= 0.5 for o in others) for d in dets)
    nh = any(d["c"] == C_NO_HELMET and d["s"] >= nh_conf and core.inside(core.center(d["b"]), head) for d in dets)
    return "weak" if weak else "absent" if nh else "none"


def head_occluded(pid, box, persons, dets) -> bool:
    """Model çıktısından baş örtülmesi vekili: baş bölgesinde eldiven (el/alet) tespiti veya baş merkezini örten,
    kameraya daha yakın (alt kenarı daha aşağıda) başka bir kişi kutusu."""
    head = core.head_region(box)
    hc = ((head[0] + head[2]) / 2, (box[1] + head[3]) / 2)
    if any(d["c"] == C_GLOVES and d["s"] >= PROD_CONF and core.inside(core.center(d["b"]), head) for d in dets):
        return True
    h = box[3] - box[1]
    return any(p != pid and core.inside(hc, b) and b[3] > box[3] + 0.05 * h for p, b, _ in persons)


# ---------------------------------------------------------------- deney monitörü
DEFAULTS = {
    "helmet_policy": "binary",      # binary (üretim) | conflict_aware (E-01 H1) | verified_absence (H2) | graded (H5)
    "evidence_floor": 0.10,         # weak baret adayı alt eşiği (H1/H5)
    "helmet_occlusion_gate": False,  # E-01 H1b: başı örtülü kare baret kararına girmez
    "edge_policy": "any",           # any (üretim) | item_specific (E-03: baret yalnız alt kenar muaf)
    "track_gate": None,             # E-02: {"kind": rolling_median|gated_ratio|aspect, ...}
    "crop_verify": None,            # E-02: {"tau": t} — start öncesi ikinci aşama kişi doğrulaması
    "handoff": None,                # E-04 A4: {"max_gap_sec", "max_center_dist_h"} — kimlik devri
    "verified_rate": 0.5,           # H2: no_helmet kanıtı oranı
    "helmet_min_obs": None,         # E-01 H6: baret kararı için gereken değerlendirilebilir gözlem (None = MIN_OBS)
    "params": {},                   # ppe_core sabitleri (MIN_SPAN_SEC vb.) — yalnız bu monitör için
}


def gate_reasons(box, conf, wh, p) -> tuple:
    W, H = wh
    x1, y1, x2, y2 = box
    m = p["EDGE_MARGIN_PX"]
    r = []
    if conf < p["PERSON_CONF_MIN"]:
        r.append("low_conf")
    if y2 - y1 < p["MIN_PERSON_H_PX"]:
        r.append("too_small")
    r += [s for s, hit in (("left", x1 <= m), ("top", y1 <= m), ("right", x2 >= W - m), ("bottom", y2 >= H - m)) if hit]
    return tuple(r)


class ExperimentalMonitor:
    """PPEMonitor ile aynı durum makinesi; eşya başına ayrı gözlem kuyruğu ve isteğe bağlı politikalar. Varsayılan
    yapılandırmada PPEMonitor ile birebir aynı olayları üretir (test_evaluation_experiments)."""
    CONSTS = ("WINDOW_SEC", "MIN_SPAN_SEC", "MIN_OBS", "ENTER_RATE", "EXIT_RATE", "PERSON_CONF_MIN", "MIN_PERSON_H_PX",
              "EDGE_MARGIN_PX", "REALERT_SEC", "STALE_SEC")

    def __init__(self, **cfg):
        unknown = set(cfg) - set(DEFAULTS)
        if unknown:
            raise ValueError(f"bilinmeyen deney ayarı: {sorted(unknown)}")
        self.cfg = {**DEFAULTS, **cfg}
        self.p = {k: getattr(core, k) for k in self.CONSTS} | self.cfg["params"]
        self.obs = {"helmet": defaultdict(deque), "vest": defaultdict(deque)}
        self.hist = defaultdict(lambda: deque(maxlen=25))      # kişi izi güven/en-boy geçmişi (E-02)
        self.state, self.first_seen, self.last_seen, self.last_alert, self.last_box = {}, {}, {}, {}, {}
        self.aliases, self.verify_rejects = [], 0
        self.frame_counts = Counter()

    # -- E-02 iz düzeyi kişi kapısı
    def _track_ok(self, pid) -> bool:
        g = self.cfg["track_gate"]
        if not g:
            return True
        h = list(self.hist[pid])[-g.get("k", 25):]
        if len(h) < g.get("min_obs", 8):
            return False
        if g["kind"] == "rolling_median":
            return median(c for c, _ in h) >= g["tau"]
        if g["kind"] == "gated_ratio":
            return sum(c >= self.p["PERSON_CONF_MIN"] for c, _ in h) / len(h) >= g["rho"]
        if g["kind"] == "aspect":
            return median(a for _, a in h) <= g["max_aspect"]
        raise ValueError(g["kind"])

    def _handoff(self, pid, box, t, present) -> None:
        """E-04 A4: yeni kimlik, yakın zamanda kaybolmuş (bu karede görünmeyen) yakın bir izin devamıysa durumu
        devralır. Döner değer yok; self.aliases'e (eski, yeni, t) yazılır."""
        h = self.cfg["handoff"]
        best = None
        for q, ts in self.last_seen.items():
            if q in present or t - ts > h["max_gap_sec"] or q == pid:
                continue
            ob = self.last_box[q]
            d = ((core.center(ob)[0] - core.center(box)[0]) ** 2 + (core.center(ob)[1] - core.center(box)[1]) ** 2) \
                ** 0.5 / max(1.0, max(ob[3] - ob[1], box[3] - box[1]))
            if d <= h["max_center_dist_h"] and (best is None or d < best[0]):
                best = (d, q)
        if best is None:
            return
        q = best[1]
        for dd in (self.obs["helmet"], self.obs["vest"], self.state, self.first_seen, self.last_alert, self.hist):
            if q in dd:
                dd[pid] = dd.pop(q)
        self.last_seen.pop(q, None)
        self.last_box.pop(q, None)
        self.aliases.append((q, pid, t))

    def _decide_helmet(self, q, was):
        n = len(q)
        p = self.p
        present = sum(v == "present" for v in q) / n if n else 0.0
        pol = self.cfg["helmet_policy"]
        if pol in ("binary", "graded"):
            viol = present < (p["EXIT_RATE"] if was else p["ENTER_RATE"])
            return viol, present, False
        if pol == "conflict_aware":
            missing = sum(v in ("none", "absent") for v in q) / n if n else 0.0
            viol = missing > 1 - p["EXIT_RATE"] if was else missing >= 1 - p["ENTER_RATE"]
            return viol, present, (not viol and present < p["ENTER_RATE"])
        if pol == "verified_absence":
            absent = sum(v == "absent" for v in q) / n if n else 0.0
            viol = absent >= self.cfg["verified_rate"] * (0.5 if was else 1.0)
            return viol, present, (not viol and present < p["ENTER_RATE"])
        raise ValueError(pol)

    def update(self, t, frame_wh, persons, helmet_ev: dict, vest_map: dict, *, occluded=None, verify=None):
        """helmet_ev: {pid: present|weak|absent|none}; occluded: {pid: bool}; verify: callable(pid) -> bool|None."""
        p = self.p
        statuses, events = {}, []
        present_now = {int(x[0]) for x in persons}
        for pid, box, conf in persons:
            pid = int(pid)
            if self.cfg["handoff"] and pid not in self.last_seen:
                self._handoff(pid, box, t, present_now)
            self.first_seen.setdefault(pid, t)
            self.last_seen[pid] = t
            self.last_box[pid] = list(box)
            st = self.state.setdefault(pid, {"helmet_viol": False, "vest_viol": False, "decided": False,
                                             "helmet_unc": False})
            x1, y1, x2, y2 = box
            self.hist[pid].append((conf, (x2 - x1) / max(1.0, y2 - y1)))
            reasons = gate_reasons(box, conf, frame_wh, p)
            base_ok = not reasons
            if self.cfg["edge_policy"] == "item_specific":
                helmet_ok = not (set(reasons) - {"bottom"})
            else:
                helmet_ok = base_ok
            track_ok = self._track_ok(pid)
            helmet_ok = helmet_ok and track_ok and not (self.cfg["helmet_occlusion_gate"] and (occluded or {}).get(pid))
            vest_ok = base_ok and track_ok
            self.frame_counts["person_frames"] += 1
            self.frame_counts["helmet_gated"] += helmet_ok
            self.frame_counts["vest_gated"] += vest_ok
            if reasons == ("bottom",):
                self.frame_counts["bottom_edge_only"] += 1
            if base_ok and not track_ok:
                self.frame_counts["dropped_by_track_gate"] += 1
            hq, vq = self.obs["helmet"][pid], self.obs["vest"][pid]
            if helmet_ok:
                hq.append((t, helmet_ev.get(pid, "none")))
            if vest_ok:
                vq.append((t, pid in vest_map))
            for q in (hq, vq):
                while q and t - q[0][0] > p["WINDOW_SEC"]:
                    q.popleft()
            span_ok = (t - self.first_seen[pid]) >= p["MIN_SPAN_SEC"]
            h_upd = helmet_ok and len(hq) >= (self.cfg["helmet_min_obs"] or p["MIN_OBS"]) and span_ok
            v_upd = vest_ok and len(vq) >= p["MIN_OBS"] and span_ok
            h_rate = sum(o[1] == "present" for o in hq) / len(hq) if hq else 0.0
            v_rate = sum(o[1] for o in vq) / len(vq) if vq else 0.0
            if h_upd or v_upd:
                was_viol = st["helmet_viol"] or st["vest_viol"]
                prev = dict(st)
                if h_upd:
                    st["helmet_viol"], h_rate, st["helmet_unc"] = self._decide_helmet([o[1] for o in hq],
                                                                                       st["helmet_viol"])
                if v_upd:
                    st["vest_viol"] = (v_rate < p["ENTER_RATE"]) if not st["vest_viol"] else (v_rate < p["EXIT_RATE"])
                now_viol = st["helmet_viol"] or st["vest_viol"]
                if now_viol and not was_viol and self.cfg["crop_verify"] and verify is not None:
                    ok = verify(pid)
                    if not ok:                          # doğrulanamadı: bu karede start yok, sonraki karede yeniden
                        self.verify_rejects += 1
                        st.update(helmet_viol=prev["helmet_viol"], vest_viol=prev["vest_viol"])
                        now_viol = False
                st["decided"] = True
                missing = " + ".join(x for x, f in (("Baretsiz", st["helmet_viol"]),
                                                    ("Yeleksiz", st["vest_viol"])) if f)

                def ev(kind):
                    e = {"type": kind, "pid": pid, "t": round(t, 1), "missing": missing,
                         "helmet_rate": round(h_rate * 100), "vest_rate": round(v_rate * 100),
                         "box": [int(x1), int(y1), int(x2), int(y2)]}
                    if self.cfg["helmet_policy"] == "graded" and st["helmet_viol"]:
                        e["helmet_evidence"] = evidence_grade([o[1] for o in hq])
                    return e

                if now_viol:
                    if not was_viol:
                        self.last_alert[pid] = t
                        events.append(ev("start"))
                    elif p["REALERT_SEC"] and t - self.last_alert.get(pid, 0) >= p["REALERT_SEC"]:
                        self.last_alert[pid] = t
                        events.append(ev("ongoing"))
                elif was_viol:
                    e = ev("end")
                    e["missing"] = ""
                    events.append(e)
            if not st["decided"]:
                status = "ANALIZ"
            elif st["helmet_viol"] or st["vest_viol"]:
                status = "IHLAL"
            elif st["helmet_unc"]:
                status = "BELIRSIZ"
            else:
                status = "UYGUN"
            self.frame_counts[f"status_{status}"] += 1
            statuses[pid] = (status, h_rate, v_rate)
        for pid in [q for q, ts in self.last_seen.items() if t - ts > p["STALE_SEC"]]:
            for d in (self.obs["helmet"], self.obs["vest"], self.state, self.first_seen, self.last_seen,
                      self.last_alert, self.last_box, self.hist):
                d.pop(pid, None)
        return statuses, events


def evidence_grade(values) -> str:
    """H5: alarm penceresinin kanıt sınıfı (bastırmaz; yalnız etiketler). İhlal penceresindeki her baret kanıtı
    (eşleşen veya zayıf) çelişki sayılır."""
    n = max(1, len(values))
    c = Counter(values)
    if c["absent"] / n >= 0.5:
        return "verified_absence"
    if (c["weak"] + c["present"]) / n >= 0.25:
        return "conflicting"
    return "missing_detection"


# ---------------------------------------------------------------- karar koşusu + incident yaşam döngüsü
def run_decision(dframes: list, wh, cfg: dict | None = None, *, production: bool = False) -> dict:
    """dframes: decision_frames()/frames_from_diag() çıktısı. production=True: ppe_core.PPEMonitor (taban çizgisi).
    detector._process / pipeline.DecisionStep ile aynı çağrı sırası: match_items -> update -> enrich_event -> sweep."""
    cfg = cfg or {}
    mon = core.PPEMonitor() if production else ExperimentalMonitor(**cfg)
    floor = cfg.get("evidence_floor", DEFAULTS["evidence_floor"])
    trk = protocol.IncidentTracker()
    events, statuses, rows = [], Counter(), []
    n_alias = 0
    t = 0.0
    for fr in dframes:
        t = fr["t"]
        persons = fr["persons"]
        hm = fr["helmet_map"] if "helmet_map" in fr else core.match_items(persons, fr["helmets"], core.head_region)
        vm = fr["vest_map"] if "vest_map" in fr else core.match_items(persons, fr["vests"], core.torso_region)
        if production:
            st, raw = mon.update(t, wh, persons, hm, vm)
        else:
            ev = {int(pid): helmet_evidence(int(pid), box, fr["dets"], hm, floor=floor) for pid, box, _ in persons}
            occ = ({int(pid): head_occluded(int(pid), box, persons, fr["dets"]) for pid, box, _ in persons}
                   if cfg.get("helmet_occlusion_gate") else None)
            tau = (cfg.get("crop_verify") or {}).get("tau")
            crop = fr.get("crop") or {}
            st, raw = mon.update(t, wh, persons, ev, vm, occluded=occ,
                                 verify=lambda pid: (crop.get(pid) or 0.0) >= tau)
            for old, new, _ in mon.aliases[n_alias:]:   # devralınan kimliğin açık incident'ı yeni kimlikle sürer
                if old in trk.active:
                    trk.active[new] = trk.active.pop(old)
            n_alias = len(mon.aliases)
        for pid, (s, *_) in st.items():
            statuses[s] += 1
            rows.append((t, int(pid), s))
        for e in raw:
            e = protocol.enrich_event(e, run_id="exp", camera_id="exp", tracker=trk)
            events.append(e)
        trk.sweep(mon.state.keys(), t)
    trk.close_all("run_end", t)
    return {"events": events, "unclosed": trk.unclosed, "statuses": dict(statuses), "status_rows": rows,
            "frame_counts": dict(getattr(mon, "frame_counts", {})), "aliases": list(getattr(mon, "aliases", [])),
            "verify_rejects": getattr(mon, "verify_rejects", 0)}


def incidents(res: dict) -> list:
    """Olaylardan incident listesi: kimlik, kişi, başlangıç, bitiş (end/stale/run_end), süre, kanıt sınıfı."""
    inc = {}
    for e in res["events"]:
        r = inc.setdefault(e["incident_id"], {"pid": e["pid"], "start": None, "end": None, "close": None,
                                              "missing": e["missing"], "evidence": e.get("helmet_evidence"),
                                              "orphan": bool(e.get("orphan"))})
        if e["type"] == "start":
            r["start"] = e["t"]
        r["pids"] = sorted(set(r.get("pids", [])) | {e["pid"]})
        if e["type"] == "end":
            r["end"], r["close"] = e["t"], "end"
    for u in res["unclosed"]:
        if u["incident_id"] in inc:
            inc[u["incident_id"]].update(end=u["t"], close=u["reason"])
    out = []
    for r in inc.values():
        r["duration"] = round(r["end"] - r["start"], 1) if r["start"] is not None and r["end"] is not None else None
        out.append(r)
    return sorted(out, key=lambda r: (r["start"] is None, r["start"] or 0))


def event_key(events) -> list:
    return [(e["type"], int(e["pid"]), round(float(e["t"]), 1)) for e in events]


def interval_mask(t, spans, pad=1.0) -> bool:
    return any(s - pad <= t <= e + pad for s, e in spans)


# ---------------------------------------------------------------- sentetik senaryolar (yalnız yazılım mantığı)
SYN_WH = (1920, 1080)
SYN_FPS = 12.0                      # VID_STRIDE 2 @ 24 fps


def _person_box(x=800, y=200, w=200, h=500):
    return [float(x), float(y), float(x + w), float(y + h)]


def _helmet_box(p):
    w, h = p[2] - p[0], p[3] - p[1]
    return [p[0] + 0.25 * w, p[1] - 0.02 * h, p[0] + 0.75 * w, p[1] + 0.12 * h]


def _vest_box(p):
    w, h = p[2] - p[0], p[3] - p[1]
    return [p[0] + 0.1 * w, p[1] + 0.3 * h, p[0] + 0.9 * w, p[1] + 0.7 * h]


def _scenario(name, kind, desc, n_sec, people_fn, expect_incidents=None):
    """people_fn(i, t) -> [(pid, kutu, güven, {"helmet": s, "weak": s, "no_helmet": s, "gloves_head": bool,
    "vest": bool, "crop": s})] (eksik anahtar = yok; vest varsayılan True, crop varsayılan 0.8)."""
    frames = []
    for i in range(int(n_sec * SYN_FPS)):
        t = (i + 1) / SYN_FPS
        persons, helmets, vests, dets, crop = [], [], [], [], {}
        for pid, box, conf, ev in people_fn(i, t):
            persons.append((pid, box, conf))
            dets.append({"c": C_PERSON, "s": conf, "b": box})
            hb = _helmet_box(box)
            if ev.get("helmet"):
                helmets.append(hb)
                dets.append({"c": C_HELMET, "s": ev["helmet"], "b": hb})
            if ev.get("weak"):
                dets.append({"c": C_HELMET, "s": ev["weak"], "b": hb})
            if ev.get("no_helmet"):
                dets.append({"c": C_NO_HELMET, "s": ev["no_helmet"], "b": hb})
            if ev.get("gloves_head"):
                dets.append({"c": C_GLOVES, "s": 0.6, "b": [hb[0], hb[3] - 10, hb[0] + 40, hb[3] + 30]})
            if ev.get("vest", True):
                vests.append(_vest_box(box))
                dets.append({"c": C_VEST, "s": 0.8, "b": _vest_box(box)})
            crop[pid] = ev.get("crop", 0.8)
        frames.append({"t": t, "persons": persons, "helmets": helmets, "vests": vests, "dets": dets, "crop": crop})
    return {"name": name, "kind": kind, "desc": desc, "frames": frames, "wh": SYN_WH,
            "expect_incidents": expect_incidents}


def synthetic_scenarios() -> list:
    """kind: must_alarm (gerçek ihlal: kaçırılırsa yazılım düzeyinde yanlış negatif) | should_not_alarm (uyumlu /
    insan değil) | incident_count (beklenen incident sayısı). Değerler el ile seçilmiştir; saha doğruluğu DEĞİLDİR."""
    P, edge = _person_box(), _person_box(y=480, h=600)          # edge: alt kenara değen (y2 = 1080)
    machine_conf = [0.30, 0.45, 0.33, 0.41, 0.27, 0.52, 0.36, 0.62, 0.39, 0.44]
    return [
        _scenario("S01_bare_head_silent_model", "must_alarm",
                  "baretsiz; model hiç baret/no_helmet üretmiyor (V1-V3'teki no_helmet davranışı)", 8,
                  lambda i, t: [(1, P, 0.85, {})]),
        _scenario("S02_bare_head_weak_flicker", "must_alarm",
                  "baretsiz; karelerin 1/3'ünde baş bölgesinde zayıf baret benzeri aday (0.12; saç/şapka)", 8,
                  lambda i, t: [(1, P, 0.85, {"weak": 0.12 if i % 3 == 0 else None})]),
        _scenario("S03_bare_head_no_helmet_class", "must_alarm", "baretsiz; no_helmet sınıfı karelerin %70'inde", 8,
                  lambda i, t: [(1, P, 0.85, {"no_helmet": 0.6 if i % 10 < 7 else None})]),
        _scenario("S04_helmet_occluded", "should_not_alarm",
                  "baretli fakat örtülü: %10 eşleşme, %25 zayıf aday, kalan kanıt yok (E-01 benzeri)", 8,
                  lambda i, t: [(1, P, 0.85, {"helmet": 0.5 if i % 10 == 9 else None,
                                              "weak": 0.15 if i % 4 == 1 else None})]),
        _scenario("S05_distant_low_conf_worker", "must_alarm",
                  "uzak baretsiz çalışan: 120 px, kişi güveni 0.42-0.48", 8,
                  lambda i, t: [(1, _person_box(w=48, h=120), 0.42 + 0.06 * ((i * 7) % 10) / 10, {})]),
        _scenario("S06_partially_occluded_worker", "must_alarm",
                  "kısmen örtülü baretsiz çalışan: güven 0.35/0.65 dönüşümlü", 8,
                  lambda i, t: [(1, P, 0.35 if i % 2 else 0.65, {})]),
        _scenario("S07_machine_short_low_conf", "should_not_alarm",
                  "iş makinesi kolu Person: güven 0.27-0.62 dalgalı (medyan ~0.40), en/boy 0.6, 4.5 sn", 4.5,
                  lambda i, t: [(1, _person_box(w=240, h=400), machine_conf[i % 10],
                                 {"vest": i % 3 == 0, "crop": 0.3})]),
        _scenario("S08_bottom_edge_bare_head", "must_alarm", "alt kenara değen, başı görünen baretsiz çalışan", 8,
                  lambda i, t: [(1, edge, 0.85, {})]),
        _scenario("S09_bottom_edge_helmet", "should_not_alarm", "alt kenara değen baretli çalışan", 8,
                  lambda i, t: [(1, edge, 0.85, {"helmet": 0.7})]),
        _scenario("S10_helmet_removed_at_4s", "must_alarm", "ilk 4 sn baretli, sonra baretsiz (gecikme ölçümü)", 10,
                  lambda i, t: [(1, P, 0.85, {"helmet": 0.7 if t <= 4.0 else None})]),
        _scenario("S11_same_person_new_id", "incident_count",
                  "baretsiz tek kişi; 5.0 sn'de 0.25 sn kayıp, aynı yerde yeni kimlik (ID parçalanması)", 10,
                  lambda i, t: [] if 5.0 < t <= 5.25 else [(1 if t <= 5.0 else 2, P, 0.85, {})],
                  expect_incidents=1),
        _scenario("S12_two_people_far_apart", "incident_count",
                  "iki ayrı baretsiz kişi; A 0-5 sn solda, B 5.5 sn'den sonra sağda", 11,
                  lambda i, t: ([(1, _person_box(x=100), 0.85, {})] if t <= 5.0 else
                                [] if t <= 5.5 else [(2, _person_box(x=1600), 0.85, {})]),
                  expect_incidents=2),
        _scenario("S13_other_person_same_spot", "incident_count",
                  "iki ayrı baretsiz kişi; B, A'nın kaybolduğu yerde 0.5 sn sonra beliriyor", 11,
                  lambda i, t: [(1, P, 0.85, {})] if t <= 5.0 else [] if t <= 5.5 else [(2, P, 0.85, {})],
                  expect_incidents=2),
        _scenario("S14_crouching_worker", "must_alarm", "çömelmiş baretsiz çalışan (en/boy 0.9)", 8,
                  lambda i, t: [(1, _person_box(w=450, h=500), 0.8, {})]),
        _scenario("S15_gloves_near_bare_head", "must_alarm",
                  "baretsiz çalışan; karelerin %40'ında el/eldiven baş bölgesinde", 8,
                  lambda i, t: [(1, P, 0.85, {"gloves_head": i % 5 < 2})]),
    ]


def scenario_outcome(sc: dict, cfg: dict | None = None, production: bool = False) -> dict:
    res = run_decision(sc["frames"], sc["wh"], cfg, production=production)
    inc = incidents(res)
    starts = [i["start"] for i in inc if i["start"] is not None]
    alarm = bool(starts)
    ok = (alarm if sc["kind"] == "must_alarm" else not alarm if sc["kind"] == "should_not_alarm"
          else len(inc) == sc["expect_incidents"])
    return {"alarm": alarm, "first_start": min(starts) if starts else None, "incidents": len(inc), "ok": ok,
            "statuses": res["statuses"]}
