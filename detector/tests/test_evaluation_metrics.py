"""FAZ 8 metrik formülleri: P/R/F1/FNR, sıfır payda, Wilson, kutu eşleme, kişi-kare UNKNOWN kuralları,
incident eşleme (gecikme, duplicate, yanlış alarm, kapanmamış), saat başına yanlış alarm. Model/GPU gerekmez."""
import pytest

from evaluation import metrics as m


# ---------------------------------------------------------------- temel formüller
def test_prf_known_values():
    r = m.prf(8, 2, 4)
    assert r["precision"] == pytest.approx(0.8)
    assert r["recall"] == pytest.approx(8 / 12)
    assert r["f1"] == pytest.approx(2 * 0.8 * (8 / 12) / (0.8 + 8 / 12))
    assert r["fnr"] == pytest.approx(4 / 12)
    lo, hi = r["recall_ci95"]
    assert lo < 8 / 12 < hi


def test_zero_denominators_return_none_not_fabricated():
    r = m.prf(0, 0, 0, tn=0)
    assert r["precision"] is None and r["recall"] is None and r["f1"] is None and r["fnr"] is None
    assert r["specificity"] is None and r["fpr"] is None
    assert r["precision_ci95"] is None and r["recall_ci95"] is None
    r = m.prf(0, 5, 0)                                   # pozitif hiç yok: recall tanımsız, precision 0
    assert r["recall"] is None and r["precision"] == 0.0 and r["f1"] is None


def test_wilson_bounds():
    assert m.wilson(0, 0) is None
    lo, hi = m.wilson(0, 10)
    assert lo == 0.0 and 0.2 < hi < 0.35
    lo, hi = m.wilson(10, 10)
    assert hi == 1.0 and 0.65 < lo < 0.8


def test_percentile_and_empty():
    assert m.percentile([], 50) is None
    assert m.percentile([1, 2, 3, 4], 50) == pytest.approx(2.5)
    assert m.summary_stats([None, 2.0])["n"] == 1


# ---------------------------------------------------------------- kutu düzeyi
def test_box_iou_and_greedy_matching_by_confidence():
    assert m.box_iou([0, 0, 10, 10], [0, 0, 10, 10]) == 1.0
    assert m.box_iou([0, 0, 10, 10], [20, 20, 30, 30]) == 0.0
    gts = [[0, 0, 10, 10]]
    preds = [([1, 1, 10, 10], 0.4), ([0, 0, 10, 10], 0.9)]   # yüksek güvenli olan eşleşir, diğeri FP
    matches, fps, fns = m.match_boxes(gts, preds, 0.5)
    assert matches[0][0] == 1 and fps == [0] and fns == []


def test_detection_counts_conf_threshold_and_missing_labels():
    images = [{"gt": {0: [[0, 0, 10, 10]]}, "pred": {0: [([0, 0, 10, 10], 0.2)]}},
              {"gt": {}, "pred": {0: [([5, 5, 9, 9], 0.9)]}},     # etiketsiz görüntüde tahmin -> FP
              {"gt": {0: [[0, 0, 4, 4]]}}]                        # tahmin anahtarı hiç yok -> FN
    r = m.detection_counts(images, [0], iou_thr=0.5, conf_thr=0.25)[0]
    assert (r["tp"], r["fp"], r["fn"], r["gt_instances"]) == (0, 1, 2, 2)
    r = m.detection_counts(images, [0], iou_thr=0.5, conf_thr=0.1)[0]
    assert (r["tp"], r["fp"], r["fn"]) == (1, 1, 1)


def test_valid_box():
    assert m.valid_box([0, 0, 1, 1])
    assert not m.valid_box([5, 0, 1, 1]) and not m.valid_box([0, 0, 1]) and not m.valid_box([0, 0, float("nan"), 1])


# ---------------------------------------------------------------- kişi-kare
def test_person_frame_unknown_is_never_a_violation_ground_truth():
    rows = [{"gt": "unknown", "pred": "violation"}, {"gt": "unknown", "pred": "ok"},
            {"gt": "missing", "pred": "violation"}, {"gt": "missing", "pred": "ok"},
            {"gt": "missing", "pred": "undecided"}, {"gt": "missing", "pred": "not_detected"},
            {"gt": "present", "pred": "violation"}, {"gt": "present", "pred": "ok"},
            {"gt": "present", "pred": "undecided"}, {"gt": None, "pred": "violation"}, {"gt": None, "pred": "ok"}]
    r = m.person_frame_confusion(rows, "helmet")
    assert (r["tp"], r["fp"], r["fn"], r["tn"]) == (1, 2, 3, 1)
    assert r["excluded_unknown"] == 2 and r["violation_on_unknown"] == 1     # FP sayılmadı
    assert r["fn_undecided"] == 1 and r["fn_not_detected"] == 1
    assert r["abstain_present"] == 1 and r["fp_unmatched"] == 1


def test_person_frame_rejects_invalid_state():
    with pytest.raises(ValueError):
        m.person_frame_confusion([{"gt": "maybe", "pred": "ok"}], "helmet")


# ---------------------------------------------------------------- incident düzeyi
def _gt(iid, s, e, item="helmet", vid="v", tr="A"):
    return {"incident_id": iid, "video_id": vid, "track_id": tr, "item": item, "start": s, "end": e}


def _pr(iid, s, e, items=("helmet",), vid="v", pid=1, closed="end"):
    return {"incident_id": iid, "video_id": vid, "pid": pid, "items": list(items), "start": s, "end": e,
            "closed_by": closed}


def test_incident_match_latency_end_error_duplicate_and_false():
    gt = [_gt("g1", 10.0, 20.0), _gt("g2", 40.0, None)]
    pred = [_pr("p1", 13.2, 21.6), _pr("p2", 15.0, 18.0, pid=2),            # g1 için ikinci alarm -> duplicate
            _pr("p3", 30.0, 32.0),                                         # hiçbir GT ile örtüşmüyor -> yanlış
            _pr("p4", 41.0, None, items=("vest",), closed="run_end")]      # ekipman farklı -> yanlış
    r = m.match_incidents(gt, pred, video_end=60.0)
    assert r["gt_incidents"] == 2 and r["detected"] == 1 and r["missed"] == 1 and r["missed_ids"] == ["g2"]
    assert r["duplicates"] == 1 and r["false_incidents"] == 2
    assert r["start_latency_sec"]["p50"] == pytest.approx(3.2)
    assert r["end_error_sec"]["p50"] == pytest.approx(-2.0)   # g1'in son alarmı p2 (end 18) — GT end 20
    assert r["unclosed_pred_incidents"] == 1
    assert r["precision"] == pytest.approx(2 / 4) and r["recall"] == pytest.approx(0.5)


def test_incident_end_error_measured_only_for_real_end_events():
    r = m.match_incidents([_gt("g1", 10.0, 20.0)], [_pr("p1", 13.0, 21.5)], video_end=30.0)
    assert r["end_error_sec"]["p50"] == pytest.approx(1.5)
    r = m.match_incidents([_gt("g1", 10.0, 20.0)], [_pr("p1", 13.0, 29.9, closed="stale")], video_end=30.0)
    assert r["end_error_sec"]["n"] == 0 and r["unclosed_pred_incidents"] == 1


def test_incident_temporal_tolerance():
    gt = [_gt("g1", 10.0, 12.0)]
    assert m.match_incidents(gt, [_pr("p", 13.5, 15.0)], video_end=30, tol_end=2.0)["detected"] == 1
    assert m.match_incidents(gt, [_pr("p", 14.5, 15.0)], video_end=30, tol_end=2.0)["false_incidents"] == 1
    assert m.match_incidents(gt, [_pr("p", 8.5, 9.5)], video_end=30, tol_start=1.0)["detected"] == 1


def test_incident_person_identity_check_with_pid_to_track():
    gt = [_gt("g1", 0.0, None, tr="B")]
    pred = [_pr("p1", 2.0, None, pid=1, closed="run_end")]
    assert m.match_incidents(gt, pred, video_end=20, pid_to_track={("v", 1): "A"})["false_incidents"] == 1
    assert m.match_incidents(gt, pred, video_end=20, pid_to_track={("v", 1): "B"})["detected"] == 1


def test_incident_videos_do_not_mix():
    r = m.match_incidents([_gt("g1", 0.0, 10.0, vid="a")], [_pr("p1", 2.0, 8.0, vid="b")], video_end=20)
    assert r["detected"] == 0 and r["false_incidents"] == 1


def test_false_alarms_per_camera_hour():
    assert m.false_alarms_per_camera_hour(3, 1800) == pytest.approx(6.0)
    assert m.false_alarms_per_camera_hour(3, 0) is None


def test_incidents_from_events_items_unclosed_and_orphans():
    ev = [{"type": "start", "pid": 1, "t": 2.0, "incident_id": "i1", "missing_helmet": True, "missing_vest": False},
          {"type": "ongoing", "pid": 1, "t": 5.0, "incident_id": "i1", "missing_helmet": True, "missing_vest": True},
          {"type": "end", "pid": 1, "t": 9.0, "incident_id": "i1", "missing_helmet": False, "missing_vest": False},
          {"type": "start", "pid": 2, "t": 3.0, "incident_id": "i2", "missing_helmet": False, "missing_vest": True},
          {"type": "end", "pid": 3, "t": 4.0, "incident_id": "orph", "orphan": True}]
    inc = {p["incident_id"]: p for p in m.incidents_from_events(ev, video_id="v", unclosed=[
        {"incident_id": "i2", "pid": 2, "reason": "stale", "t": 14.0}])}
    assert set(inc) == {"i1", "i2"}                         # orphan incident açmaz
    assert inc["i1"]["items"] == ["helmet", "vest"] and inc["i1"]["closed_by"] == "end" and inc["i1"]["end"] == 9.0
    assert inc["i2"]["closed_by"] == "stale" and inc["i2"]["end"] == 14.0
