"""FAZ 10-A.5B deney modülü: varsayılan deney monitörü = üretim PPEMonitor (birebir), her aday politikanın amaçlanan
etkisi ve BELGELENEN yan etkisi (yanlış negatif / birleşme riski), offline ByteTrack davranışı. YOLO/GPU gerekmez."""
import random

import pytest

import ppe_core as core
from evaluation import experiments as ex

WH = (1920, 1080)
P = [800.0, 200.0, 1000.0, 700.0]


def _random_frames(seed, n=240):
    """Rastgele çok kişili dizi: kenar teması, güven dalgalanması, eşya titreşimi, kaybolma (STALE), yeniden doğma."""
    rnd = random.Random(seed)
    people = {pid: [rnd.uniform(0, 1700), rnd.uniform(0, 500)] for pid in range(1, 5)}
    frames = []
    for i in range(n):
        t = (i + 1) / 12
        persons, helmets, vests = [], [], []
        for pid, (x, y) in list(people.items()):
            if rnd.random() < 0.08:
                continue
            x += rnd.uniform(-15, 15)
            people[pid] = [x, y]
            h = rnd.choice([50, 300, 600])
            box = [x, y, x + h * 0.4, min(1080.0, y + h)]
            persons.append((pid, box, rnd.choice([0.3, 0.45, 0.8, 0.9])))
            if rnd.random() < (0.9 if pid % 2 else 0.1):
                helmets.append(ex._helmet_box(box))
            if rnd.random() < 0.7:
                vests.append(ex._vest_box(box))
        if i == n // 2:                                  # bir kişi kaybolur, yeni kimlik doğar
            people[10 + i] = people.pop(1, [500, 300])
        frames.append({"t": t, "persons": persons, "helmets": helmets, "vests": vests, "dets": []})
    return frames


@pytest.mark.parametrize("seed", range(12))
def test_default_experimental_monitor_equals_production(seed):
    frames = _random_frames(seed)
    a = ex.run_decision(frames, WH, production=True)
    b = ex.run_decision(frames, WH, {})
    assert ex.event_key(a["events"]) == ex.event_key(b["events"])
    assert [(u["pid"], u["reason"]) for u in a["unclosed"]] == [(u["pid"], u["reason"]) for u in b["unclosed"]]
    assert a["statuses"] == b["statuses"]


def test_production_constants_untouched_by_params():
    before = {k: getattr(core, k) for k in ex.ExperimentalMonitor.CONSTS}
    ex.run_decision(_random_frames(1), WH, {"params": {"MIN_SPAN_SEC": 9.0, "PERSON_CONF_MIN": 0.9}})
    assert {k: getattr(core, k) for k in ex.ExperimentalMonitor.CONSTS} == before
    with pytest.raises(ValueError):
        ex.ExperimentalMonitor(not_a_setting=1)


def _sc(name):
    return next(s for s in ex.synthetic_scenarios() if s["name"].startswith(name))


def _out(name, cfg=None, production=False):
    return ex.scenario_outcome(_sc(name), cfg or {}, production=production)


# ---------------------------------------------------------------- E-01
def test_helmet_evidence_categories():
    hb = ex._helmet_box(P)
    assert ex.helmet_evidence(1, P, [], {1: hb}) == "present"
    assert ex.helmet_evidence(1, P, [{"c": 0, "s": 0.12, "b": hb}], {}) == "weak"
    assert ex.helmet_evidence(1, P, [{"c": 0, "s": 0.07, "b": hb}], {}) == "none"            # floor 0.10 altı
    assert ex.helmet_evidence(1, P, [{"c": 7, "s": 0.6, "b": hb}], {}) == "absent"
    assert ex.helmet_evidence(1, P, [{"c": 7, "s": 0.6, "b": hb}, {"c": 0, "s": 0.2, "b": hb}], {}) == "weak"
    # başka kişiye eşleşmiş baret bu kişi için zayıf kanıt sayılmaz
    assert ex.helmet_evidence(1, P, [{"c": 0, "s": 0.8, "b": hb}], {2: hb}) == "none"


def test_baseline_alarms_on_occluded_helmet_and_conflict_aware_does_not():
    assert _out("S04", production=True)["alarm"]
    o = _out("S04", {"helmet_policy": "conflict_aware"})
    assert not o["alarm"] and o["statuses"].get("BELIRSIZ", 0) > 0          # bastırma değil: BELIRSIZ görünür


def test_conflict_aware_still_alarms_when_model_is_silent():
    """'her şeyi uygun say' filtresi değil: hiç kanıt yoksa alarm sürer."""
    assert _out("S01", {"helmet_policy": "conflict_aware"})["alarm"]


def test_conflict_aware_documented_false_negative_on_weak_flicker():
    """BELGELENEN RİSK: baretsiz kişide baş bölgesinde zayıf baret benzeri aday titreşirse H1 alarmı kaçırır."""
    assert _out("S02", production=True)["alarm"]
    assert not _out("S02", {"helmet_policy": "conflict_aware"})["alarm"]


def test_verified_absence_misses_violation_without_no_helmet_class():
    """BELGELENEN RİSK: H2 yalnız no_helmet kanıtıyla alarm verir; model sessizse gerçek ihlal kaçırılır."""
    assert not _out("S01", {"helmet_policy": "verified_absence"})["alarm"]
    assert _out("S03", {"helmet_policy": "verified_absence"})["alarm"]


def test_graded_keeps_every_baseline_alarm_and_labels_evidence():
    for name in ("S01", "S02", "S03", "S04"):
        sc = _sc(name)
        a = ex.run_decision(sc["frames"], sc["wh"], production=True)
        b = ex.run_decision(sc["frames"], sc["wh"], {"helmet_policy": "graded"})
        assert ex.event_key(a["events"]) == ex.event_key(b["events"])
    grades = {n: ex.incidents(ex.run_decision(_sc(n)["frames"], ex.SYN_WH, {"helmet_policy": "graded"}))[0]["evidence"]
              for n in ("S01", "S03", "S04")}
    assert grades == {"S01": "missing_detection", "S03": "verified_absence", "S04": "conflicting"}


def test_head_occlusion_gate_excludes_frames_but_keeps_alarm():
    sc = _sc("S15")
    r = ex.run_decision(sc["frames"], sc["wh"], {"helmet_occlusion_gate": True})
    assert r["frame_counts"]["helmet_gated"] < r["frame_counts"]["vest_gated"]
    assert ex.incidents(r)


def test_helmet_min_obs_delays_but_does_not_drop():
    a, b = _out("S06", production=True), _out("S06", {"helmet_min_obs": 16})
    assert a["alarm"] and b["alarm"] and b["first_start"] > a["first_start"]


# ---------------------------------------------------------------- E-02
def test_track_gates_suppress_short_low_conf_machine():
    assert _out("S07", production=True)["alarm"]
    for g in ({"kind": "rolling_median", "tau": 0.5}, {"kind": "gated_ratio", "rho": 0.6},
              {"kind": "aspect", "max_aspect": 0.55}):
        assert not _out("S07", {"track_gate": g})["alarm"], g


@pytest.mark.parametrize("scenario,gate", [("S05", {"kind": "rolling_median", "tau": 0.5}),
                                           ("S06", {"kind": "gated_ratio", "rho": 0.6}),
                                           ("S14", {"kind": "aspect", "max_aspect": 0.55})])
def test_track_gates_documented_false_negatives(scenario, gate):
    """BELGELENEN RİSK: uzak (düşük güven), kısmen örtülü (güven dalgalı), çömelmiş (geniş kutu) baretsiz çalışan."""
    assert _out(scenario, production=True)["alarm"]
    assert not _out(scenario, {"track_gate": gate})["alarm"]


def test_crop_verify_blocks_start_until_verified():
    sc = _sc("S01")
    for fr in sc["frames"]:
        fr["crop"] = {1: 0.3 if fr["t"] < 4.0 else 0.8}
    r = ex.run_decision(sc["frames"], sc["wh"], {"crop_verify": {"tau": 0.5}})
    inc = ex.incidents(r)
    assert len(inc) == 1 and inc[0]["start"] >= 4.0 and r["verify_rejects"] > 0


# ---------------------------------------------------------------- E-03
def test_item_specific_edge_evaluates_bottom_edge_helmet_only():
    assert not _out("S08", production=True)["alarm"]
    o = _out("S08", {"edge_policy": "item_specific"})
    assert o["alarm"]
    sc = _sc("S08")
    r = ex.run_decision(sc["frames"], sc["wh"], {"edge_policy": "item_specific"})
    assert r["frame_counts"]["vest_gated"] == 0                             # yelek kesik kutuda değerlendirilmez
    assert all(e["missing"] in ("Baretsiz", "") for e in r["events"])
    assert not _out("S09", {"edge_policy": "item_specific"})["alarm"]


def test_item_specific_edge_rejects_top_edge():
    top = [800.0, 0.0, 1000.0, 600.0]
    frames = [{"t": (i + 1) / 12, "persons": [(1, top, 0.9)], "helmets": [], "vests": [], "dets": []}
              for i in range(96)]
    assert not ex.incidents(ex.run_decision(frames, WH, {"edge_policy": "item_specific"}))


# ---------------------------------------------------------------- E-04
def test_id_fragmentation_duplicate_incident_and_handoff():
    """Üretim: aynı kişi yeni kimlik alınca İKİNCİ incident (FAZ 10-A.5A mekanizması). A4 kimlik devriyle tek."""
    assert _out("S11", production=True)["incidents"] == 2
    o = _out("S11", {"handoff": {"max_gap_sec": 2.5, "max_center_dist_h": 1.0}})
    assert o["incidents"] == 1 and o["ok"]


def test_handoff_keeps_far_apart_people_separate_but_merges_same_spot():
    h = {"handoff": {"max_gap_sec": 2.5, "max_center_dist_h": 1.0}}
    assert _out("S12", h)["incidents"] == 2
    # BELGELENEN RİSK: farklı kişi aynı yerde kısa sürede belirirse birleşir (kimlik birleşmesi)
    assert _out("S13", production=True)["incidents"] == 2 and _out("S13", h)["incidents"] == 1


def test_synthetic_suite_baseline_profile():
    """Taban çizgisinin sentetik profili sabitlenir (karar mantığı değişirse bu test bilinçli güncellenir)."""
    bad = {s["name"] for s in ex.synthetic_scenarios() if not ex.scenario_outcome(s, production=True)["ok"]}
    assert bad == {"S04_helmet_occluded", "S07_machine_short_low_conf", "S08_bottom_edge_bare_head",
                   "S11_same_person_new_id"}


# ---------------------------------------------------------------- offline ByteTrack
def _seq(n, fn):
    return [{"t": (i + 1) / 12, "dets": fn(i)} for i in range(n)]


def _pbox(i):
    return [600.0 + 4 * i, 200.0, 800.0 + 4 * i, 700.0]


def _pids(tr):
    return sorted({d["id"] for f in tr["tracked"] for d in f if d["c"] == 6 and d["id"] is not None})


def test_tracker_class_agnostic_id_continues_as_vest_and_person_only_prevents():
    frames = _seq(60, lambda i: [{"c": 6, "s": 0.85, "b": _pbox(i)}] if i < 30 else
                  [{"c": 2, "s": 0.8, "b": [_pbox(i)[0] + 10, 260.0, _pbox(i)[2] - 10, 650.0]}] +
                  ([{"c": 6, "s": 0.85, "b": _pbox(i)}] if i >= 34 else []))
    prod = ex.track_offline(frames)
    first = _pids(prod)[0]
    assert any(d["id"] == first and d["c"] == 2 for f in prod["tracked"] for d in f)   # kişi kimliği yeleğe geçti
    po = ex.track_offline(frames, classes={6})
    assert not any(d["c"] != 6 for f in po["tracked"] for d in f)
    assert len(_pids(po)) == 1                                                          # kişi kimliği korunur


def test_low_score_stage_keeps_identity_through_low_conf_occlusion():
    frames = _seq(72, lambda i: [{"c": 6, "s": 0.15 if 30 <= i < 66 else 0.85, "b": _pbox(i)}])
    assert len(_pids(ex.track_offline(frames))) == 2                    # 3 sn > tampon: üretimde yeni kimlik
    assert len(_pids(ex.track_offline(frames, conf_in=0.10))) == 1      # ikinci aşama izi sürdürür


def test_low_score_tracks_do_not_enter_decision_below_prod_conf():
    frames = _seq(40, lambda i: [{"c": 6, "s": 0.15 if 10 <= i < 20 else 0.85, "b": _pbox(i)}])
    tr = ex.track_offline(frames, conf_in=0.10)
    dfr = ex.decision_frames(frames, tr["tracked"])
    assert all(c >= ex.PROD_CONF for fr in dfr for _, _, c in fr["persons"])
    assert sum(1 for fr in dfr if not fr["persons"]) == 10


def test_tracker_output_clipped_like_ultralytics_results():
    frames = _seq(3, lambda i: [{"c": 6, "s": 0.9, "b": [100.0, 600.0, 300.0, 1090.0]}])
    tr = ex.track_offline(frames, wh=(1920, 1080))["tracked"]
    assert all(d["b"][3] <= 1080.0 for f in tr for d in f)


def test_tracked_equivalence_tolerates_diag_rounding():
    """Tanı izi güveni 4 haneye yuvarlar (0.785515 -> 0.7855); yeniden yuvarlama yanlış 'DIFFERENT' vermemeli."""
    a = [[{"id": 3, "c": 6, "s": 0.785515, "b": [1.04, 2.0, 3.0, 4.0]}]]
    diag = [{"tracked": [{"id": 3, "c": 6, "s": 0.7855, "b": [1.0, 2.0, 3.0, 4.0]}]}]
    assert ex.tracked_equivalence(a, diag)["status"] == "IDENTICAL"
    diag[0]["tracked"][0]["id"] = 4
    assert ex.tracked_equivalence(a, diag)["status"] == "DIFFERENT"


def test_new_track_kalman_diagnostic():
    """Kayıptan sonra 150 px kaymış yeniden görünme: yeni kimlik; Kalman tahminiyle IoU x güven < 0.2 (eşleşemez)."""
    frames = _seq(40, lambda i: [] if 15 <= i < 18 else
                  [{"c": 6, "s": 0.85, "b": _pbox(i) if i < 15 else [_pbox(i)[0] + 150, 200.0, _pbox(i)[2] + 150, 700.0]}])
    n = ex.track_offline(frames)["new_tracks"]
    assert len(n) == 1 and n[0]["pid"] == 2 and n[0]["best_lost_pid"] == 1
    assert n[0]["frame_i"] == 19                                        # 18. karede onaysız; çıktıya 19'da girer
    assert 0 < n[0]["iou_x_conf"] < 0.2
