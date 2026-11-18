"""FAZ 10-A.4 taban çizgisi analiz katmanı (evaluation/baseline.py): GT yokken doğruluk metriği üretilmez; alarm
varsayılan olarak doğru sayılmaz; bilinen hata sinyalleri ve çözüm kapısı; takip/kapsam özetleri; teslim
karşılaştırmasında eksik/fazla/mükerrer ayrımı; çalıştırma karşılaştırması; Markdown. GPU/DB gerekmez."""
import json
from pathlib import Path

import pytest

import telemetry
from evaluation import baseline as bl

KNOWN = json.loads((Path(__file__).resolve().parents[2] / "tools" / "reliability" / "baseline" /
                    "known_errors.json").read_text(encoding="utf-8"))["known_errors"]
WH = (1920, 1080)


def person(pid, box, status="ANALIZ", decided=False, conf=0.9, **kw):
    return {"pid": pid, "box": list(box), "conf": conf, "status": status, "decided": decided,
            "helmet_viol": kw.get("hv", False), "vest_viol": False, "h_rate": 0.0, "v_rate": 0.0,
            "helmet_box": None, "vest_box": None}


def run_of(frames, events=(), unclosed=()):
    return {"frames": frames, "events": list(events), "unclosed": list(unclosed), "processed_frames": len(frames),
            "video_sec": frames[-1]["t"] if frames else 0.0, "width": WH[0], "height": WH[1], "report": [],
            "timing_ms": {}}


def frames_fragmented():
    """2 eşzamanlı kişi, ama 6 farklı takip kimliği (V1 benzeri parçalanma)."""
    out = []
    for i in range(60):
        a, b = (1 + i // 20) * 2 - 1, (1 + i // 20) * 2          # kimlikler 1..6, 20 karede bir yenilenir
        out.append({"idx": i + 1, "frame": i, "t": round(0.08 * (i + 1), 3), "events": [],
                    "people": [person(a, (300, 200, 450, 700), "UYGUN", True),
                               person(b, (900, 200, 1050, 700), "UYGUN", True)]})
    return out


def frames_edge_and_alarm():
    """Kenarda kalan (x1=2) hiç karar almayan kişi + ihlal alarmı veren kişi."""
    frames, events = [], []
    for i in range(50):
        t = round(0.08 * (i + 1), 3)
        people = [person(7, (2, 100, 300, 900)), person(8, (800, 150, 1000, 800), "IHLAL" if i >= 30 else "ANALIZ",
                                                        i >= 30, hv=i >= 30)]
        frames.append({"idx": i + 1, "frame": i, "t": t, "events": [], "people": people})
    events.append({"type": "start", "pid": 8, "t": frames[30]["t"], "incident_id": "inc-1", "missing_helmet": True,
                   "missing_vest": False})
    events.append({"type": "ongoing", "pid": 8, "t": frames[40]["t"], "incident_id": "inc-1",
                   "missing_helmet": True, "missing_vest": False})
    return frames, events, [{"incident_id": "inc-1", "pid": 8, "reason": "run_end", "t": frames[-1]["t"]}]


@pytest.fixture
def videos():
    f2, ev2, unc2 = frames_edge_and_alarm()
    return {"V1": bl.analyze_video(run_of(frames_fragmented()), "V1", telemetry.gate_reasons),
            "V2": bl.analyze_video(run_of(f2, ev2, unc2), "V2", telemetry.gate_reasons),
            "V3": bl.analyze_video(run_of(frames_fragmented()[:5]), "V3", telemetry.gate_reasons)}


def test_no_ground_truth_means_no_accuracy_metrics():
    m = bl.metric_block(None)
    assert m["status"] == m["precision"] == m["recall"] == m["false_alarm_rate"] == "INSUFFICIENT_GROUND_TRUTH"
    assert m["coverage_vs_truth"] == "NOT_MEASURABLE"
    assert bl.metric_block([{"x": 1}])["status"] == "MEASURED"


def test_tracking_summary_flags_fragmentation(videos):
    t = videos["V1"]["tracking"]
    assert (t["track_ids"], t["max_concurrent_persons"], t["ids_per_max_concurrent"]) == (6, 2, 3.0)
    assert t["never_decided_tracks"] == 0


def test_gate_reasons_and_coverage_use_production_gate(videos):
    v = videos["V2"]
    edge = next(tr for tr in v["tracking"]["tracks"] if tr["pid"] == 7)
    assert edge["dominant_gate"] == "edge" and not edge["ever_decided"]
    c = v["coverage"]
    assert c["observed_person_frames"] == 100 and c["gate_passed"] == 50
    assert c["unevaluated_by_reason"] == {"edge": 50} and c["coverage_vs_truth"] == "NOT_MEASURABLE"


def test_incident_lifecycle_and_unreviewed_default(videos):
    inc = videos["V2"]["incidents"]
    assert len(inc) == 1
    i = inc[0]
    assert (i["pid"], i["items"], i["closed_by"], i["ongoing_events"], i["review"]) == (8, ["helmet"], "run_end", 1,
                                                                                         "UNREVIEWED")
    assert i["at_start"]["box"] == [800, 150, 1000, 800]
    assert bl.review_summary(videos)["UNREVIEWED"] == 1 and bl.review_summary(videos)["true_violation"] == 0


def test_incident_start_matches_rounded_event_time():
    """PPEMonitor olay zamanı yuvarlanır (4.7) ama kare zamanı idx*stride/fps (4.666...): yine eşleşmeli."""
    frames = [{"idx": i, "frame": 2 * i - 1, "t": i * 2 / 24, "events": [],
               "people": [person(3, (800, 150, 1000, 800))]} for i in range(1, 80)]
    ev = [{"type": "start", "pid": 3, "t": 4.7, "incident_id": "x", "missing_helmet": True, "missing_vest": False}]
    i = bl.analyze_video(run_of(frames, ev), "V2", telemetry.gate_reasons)["incidents"][0]
    assert i["start_frame"] == 111 and i["at_start"]["box"] == [800, 150, 1000, 800]


def test_known_errors_mark_alarm_never_as_detection(videos):
    ke = {k["id"]: k for k in bl.evaluate_known_errors(KNOWN, videos, {}, None)}
    assert ke["E-01"]["signal"] == "PRESENT" and ke["E-01"]["resolution"] == "OPEN"
    assert videos["V2"]["incidents"][0]["known_errors"] == ["E-01"]
    assert ke["E-03"]["signal"] == "PRESENT" and ke["E-03"]["detail"]["edge_undecided_tracks"] == [7]
    assert ke["E-04"]["signal"] == "PRESENT"
    assert ke["E-02"]["signal"] == "ABSENT" and ke["E-02"]["resolution"] == "SIGNAL_ABSENT_UNCONFIRMED"
    assert all(not k["reference_sha_verified"] for k in ke.values())


def test_resolution_requires_human_confirmation_and_regression_test(videos):
    review = {"known_errors": {"E-02": {"human_confirmed_by": "İSG uzmanı", "regression_test": "test_x"}}}
    assert {k["id"]: k["resolution"] for k in bl.evaluate_known_errors(KNOWN, videos, {}, review)}["E-02"] == \
        "RESOLVED_CONFIRMED"
    only_human = {"known_errors": {"E-02": {"human_confirmed_by": "İSG uzmanı"}}}
    assert {k["id"]: k["resolution"] for k in bl.evaluate_known_errors(KNOWN, videos, {}, only_human)}["E-02"] == \
        "SIGNAL_ABSENT_UNCONFIRMED"


def test_human_review_labels_attach_and_unmatched_reported(videos):
    review = {"incidents": [{"video": "V2", "pid": 8, "start_t": videos["V2"]["incidents"][0]["start"] + 0.5,
                             "label": "false_alarm", "reviewer": "r1"},
                            {"video": "V3", "pid": 99, "start_t": 1.0, "label": "false_alarm"}]}
    unmatched = bl.apply_review(videos, review)
    assert videos["V2"]["incidents"][0]["review"] == "false_alarm" and len(unmatched) == 1
    with pytest.raises(ValueError):
        bl.apply_review(videos, {"incidents": [{"video": "V2", "pid": 8, "start_t": 0, "label": "correct"}]})


def test_delivery_compare_separates_missing_extra_duplicate():
    ledger = [{"event_id": "a", "type": "start"}, {"event_id": "b", "type": "end"}, {"event_id": "c", "type": "start"}]
    db = [{"event_id": "a", "event_type": "start"}, {"event_id": "a", "event_type": "start"},
          {"event_id": "b", "event_type": "end"}, {"event_id": "z", "event_type": "start"}]
    d = bl.delivery_compare(ledger, db)
    assert (d["missing_in_db"], d["extra_in_db"], d["duplicate_in_db"], d["ok"]) == (["c"], ["z"], ["a"], False)
    ok = bl.delivery_compare(ledger, [{"event_id": e["event_id"], "event_type": e["type"]} for e in ledger])
    assert ok["ok"] and ok["by_type_db"] == {"start": 2, "end": 1}


def _result(videos, sha="m1", vsha="v"):
    return {"run_id": "r", "created_utc": "t", "limitations": ["x"],
            "manifest": {"environment": {"git_head": "abc", "gpu": "RTX", "torch": "2", "device": "0"},
                         "model": {"file": "best.pt", "sha256": sha},
                         "settings": {"vid_stride": 2, "conf": 0.25},
                         "videos": {k: {"file": f"{k}.mp4", "sha256": vsha + k} for k in videos}},
            "videos": videos, "known_errors": bl.evaluate_known_errors(KNOWN, videos, {}, None),
            "review_summary": bl.review_summary(videos), "metrics": bl.metric_block(None)}


def test_compare_detects_changes_and_input_identity(videos):
    a = bl.run_digest(_result(videos))
    assert bl.compare(a, a)["identical_summary"] and bl.compare(a, a)["comparable_inputs"]
    b = json.loads(json.dumps(a))
    b["videos"]["V2"]["alarms"] = 0
    b["known_errors"]["E-01"] = "ABSENT"
    diff = bl.compare(a, b)
    assert diff["videos"]["V2"]["alarms"] == [1, 0] and diff["known_errors"]["E-01"] == ["PRESENT", "ABSENT"]
    other_model = bl.run_digest(_result(videos, sha="m2"))
    assert not bl.compare(a, other_model)["comparable_inputs"]


def test_markdown_states_ground_truth_gap_and_unreviewed(videos):
    md = bl.render_markdown(_result(videos))
    assert "INSUFFICIENT_GROUND_TRUTH" in md and "**UNREVIEWED**" in md and "E-01" in md and "## Kısıtlar" in md


def test_video_entry_hashes_content(tmp_path):
    import cv2
    import numpy as np
    p = tmp_path / "v.avi"
    w = cv2.VideoWriter(str(p), cv2.VideoWriter_fourcc(*"MJPG"), 10, (64, 48))
    for i in range(5):
        w.write(np.full((48, 64, 3), i * 40, np.uint8))
    w.release()
    e = bl.video_entry(p, "VX")
    assert e["sha256"] == bl.sha256_file(p) and e["file"] == "v.avi" and (e["width"], e["height"]) == (64, 48)
    assert str(tmp_path) not in json.dumps(e)                         # kullanıcı klasörü manifeste yazılmaz
