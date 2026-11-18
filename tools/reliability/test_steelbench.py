"""SteelBench saf mantık testleri (Docker/GPU/video gerektirmez). Proje kökünden:
    .venv/bin/python -m pytest -q tools/reliability/test_steelbench.py        (Windows: .venv\\Scripts\\python)"""
import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import steelbench as sb  # noqa: E402

EXP = json.loads(sb.EXPECTED.read_text(encoding="utf-8"))
CAMS = {v["camera_id"]: v["key"] for v in EXP["videos"]}


def ev(cam, t, box, kind="start", helmet=True, vest=False, iid="i1", pid=3):
    return {"camera_id": cam, "t": t, "box": box, "type": kind, "missing_helmet": helmet, "missing_vest": vest,
            "missing": "Baretsiz", "helmet_rate": 0, "vest_rate": 100, "pid": pid, "incident_id": iid}


E01_BOX, E02_BOX = [904, 121, 1492, 1070], [1053, 677, 1304, 1084]


def test_known_false_alarms_matched_never_counted_as_detections():
    alarms = sb.classify([ev("sbv2", 4.8, E01_BOX), ev("sbv3", 9.0, E02_BOX, iid="i2")], EXP, CAMS)
    assert [(a["classification"], a["known_error"]) for a in alarms] == [("KNOWN_FALSE_ALARM", "E-01"),
                                                                         ("KNOWN_FALSE_ALARM", "E-02")]
    assert not any(a["classification"] == "VERIFIED_TRUE_VIOLATION" for a in alarms)


@pytest.mark.parametrize("t,box,helmet", [(7.0, E01_BOX, True),                 # zaman penceresi dışında
                                          (4.8, [0, 0, 100, 100], True),        # farklı konum
                                          (4.8, E01_BOX, False)])               # farklı ihlal türü (yelek)
def test_unmatched_alarm_is_new_and_unreviewed(t, box, helmet):
    a = sb.classify([ev("sbv2", t, box, helmet=helmet, vest=not helmet)], EXP, CAMS)[0]
    assert (a["classification"], a["known_error"]) == ("UNREVIEWED_NEW_ALARM", None)


def _video(tracks=(), track_ids=6, ge10=5):
    return {"tracks": list(tracks), "track_ids": track_ids, "track_ids_ge10_frames": ge10, "system_coverage": 0.5,
            "run": {"video_sec": 15.0}}


def test_known_error_not_reproduced_is_fix_candidate_not_pass():
    videos = {"V1": _video(track_ids=2), "V2": _video([{"pid": 1, "visible_sec": 24, "evaluated_fraction": 0.6,
                                                       "analiz_reasons": {}}]), "V3": _video()}
    st = sb.known_error_status(EXP, [], videos)
    assert {k: v["status"] for k, v in st.items()} == {"E-01": "NOT_REPRODUCED", "E-02": "NOT_REPRODUCED",
                                                       "E-03": "NOT_REPRODUCED", "E-04": "NOT_REPRODUCED"}
    assert all("bağımsız insan doğrulaması" in v["note"] for v in st.values())


def test_coverage_gap_and_fragmentation_reproduced():
    videos = {"V1": _video(track_ids=6), "V2": _video([{"pid": 1, "visible_sec": 23.9, "evaluated_fraction": 0.0,
                                                       "analiz_reasons": {"edge": 282}}]), "V3": _video()}
    st = sb.known_error_status(EXP, [], videos)
    assert st["E-03"]["status"] == st["E-04"]["status"] == "REPRODUCED"


def test_metrics_never_claim_success_without_ground_truth():
    alarms = sb.classify([ev("sbv2", 4.8, E01_BOX)], EXP, CAMS)
    videos = {k: _video() for k in ("V1", "V2", "V3")}
    m = sb.metrics(EXP, alarms, videos)
    assert m["precision"]["status"] == "INSUFFICIENT_GROUND_TRUTH" and m["precision"]["value"] is None
    assert m["recall"]["status"] == "NOT_MEASURABLE" and m["recall"]["value"] is None
    assert m["false_alarm_rate_per_camera_hour"]["status"] == "INSUFFICIENT_GROUND_TRUTH"
    assert m["ground_truth_coverage"]["status"] == "NOT_MEASURABLE"
    new = sb.classify([ev("sbv1", 2.0, [0, 0, 50, 50])], EXP, CAMS)
    assert sb.metrics(EXP, new, videos)["precision"]["status"] == "NOT_MEASURABLE"     # incelenmemiş alarm var
    assert not any(v["status"] in ("PASS", "MEASURED") for v in m.values())


def test_analyze_video_reasons_births_and_incident_closure():
    P = lambda pid, st, gate=(), hb=None: {"pid": pid, "box": [0, 0, 10, 40], "conf": 0.9, "status": st,  # noqa: E731
                                           "gate": list(gate), "helmet_box": hb, "vest_box": None}
    frames = [{"t": 0.1, "persons": [P(1, "ANALIZ", ["edge"]), P(2, "ANALIZ")], "helmets": [[1, 1, 3, 3]], "vests": []},
              {"t": 0.2, "persons": [P(1, "ANALIZ", ["edge"]), P(2, "UYGUN", hb=[1, 1, 3, 3])],
               "helmets": [[1, 1, 3, 3], [5, 5, 6, 6]], "vests": []},
              {"t": 0.3, "persons": [P(7, "IHLAL")], "helmets": [], "vests": [[0, 0, 1, 1]]}]
    events = [ev("c", 0.3, [0, 0, 10, 40], iid="x", pid=7)]
    rep = {"run_status": "finished", "end_reason": "eof", "video_time_sec": 0.3,
           "unclosed_incidents": [{"incident_id": "x", "pid": 7, "reason": "run_end", "t": 0.3}]}
    a = sb.analyze_video(frames, events, rep)
    assert (a["person_frames"], a["evaluated_person_frames"], a["system_coverage"]) == (5, 2, 0.4)
    assert a["analiz_reasons"] == {"edge": 2, "insufficient_history": 1}
    assert [b["pid"] for b in a["track_births"]] == [1, 2, 7] and a["track_ids"] == 3
    assert a["unmatched_items"] == {"helmet": 2, "vest": 1}
    assert a["incidents"] == [{"pid": 7, "missing": "Baretsiz", "start": 0.3, "end": 0.3, "ongoing": 0,
                               "closed_by": "run_end"}]


def _result(alarms, known, env=None, run_id="r"):
    return {"run_id": run_id, "summary": {"environment": env or {"model_sha256": "a", "device": "mps"},
                                          "integrity": "PASS", "alarms": alarms, "known_errors": known,
                                          "videos": {"V1": {"track_ids": 6}}, "metrics_status": {}}}


def test_compare_detects_new_and_disappeared_alarms_and_env_changes():
    a1 = {"video": "V2", "t": 4.8, "violation": "missing_helmet", "box": E01_BOX, "classification": "KNOWN_FALSE_ALARM",
          "known_error": "E-01"}
    prev = _result([a1], {"E-01": "REPRODUCED"})
    assert sb.compare(prev, copy.deepcopy(prev))["verdict"] == "NO_DECISION_CHANGE"
    a2 = {**a1, "video": "V1", "t": 3.0, "classification": "UNREVIEWED_NEW_ALARM", "known_error": None}
    cur = _result([a2], {"E-01": "NOT_REPRODUCED"}, env={"model_sha256": "b", "device": "mps"})
    c = sb.compare(prev, cur)
    assert c["verdict"] == "CHANGED_REVIEW_REQUIRED" and len(c["new_alarms"]) == len(c["disappeared_alarms"]) == 1
    assert c["known_error_changes"] == {"E-01": {"previous": "REPRODUCED", "current": "NOT_REPRODUCED"}}
    assert c["environment_differences"] == ["model_sha256: a -> b"]


def test_isolation_guard_refuses_shared_stacks(monkeypatch):
    monkeypatch.setattr(sb.c, "PROJECT", "ppe-rel")
    monkeypatch.setattr(sb.c, "env", lambda: {"REL_PORT": "8100"})
    assert sb.cmd_run(type("A", (), {})()) == 1
    assert sb.c.PROJECT == "ppe-rel"                        # monkeypatch geri alınır; modül varsayılanı ppe-bench


def test_module_defaults_to_separate_stack():
    import importlib
    import common
    importlib.reload(common)
    assert common.PROJECT == "ppe-bench" and common.ENV_FILE.name == "bench.env"


def test_expected_registry_is_honest_and_consistent():
    ids = [k["id"] for k in EXP["known_errors"]]
    assert ids == ["E-01", "E-02", "E-03", "E-04"] and len(set(ids)) == 4
    assert EXP["verified_true_incidents"] == []
    assert all(k["review"]["status"] == "PENDING_INDEPENDENT_HUMAN_REVIEW" for k in EXP["known_errors"])
    assert all(not v["human_review"]["full_video_ground_truth"] for v in EXP["videos"])
    for v in EXP["videos"]:
        p = sb.c.ROOT / v["path"]
        if p.is_file():                                     # videolar Git dışı; varsa özet tutmalı
            assert sb.sha256_file(p) == v["sha256"]


def test_baseline_summary_has_no_raw_data():
    if not sb.BASELINE.exists():
        pytest.skip("referans özeti yok")
    b = json.loads(sb.BASELINE.read_text(encoding="utf-8"))
    text = json.dumps(b)
    assert b["schema"] == "steelbench-baseline/1" and "snapshot" not in text and "token" not in text.lower()
    assert {a["classification"] for a in b["summary"]["alarms"]} <= {"KNOWN_FALSE_ALARM", "UNREVIEWED_NEW_ALARM"}


def test_markdown_renders_without_comparison():
    r = json.loads(sorted((sb.HERE / "output").glob("*_steelbench/result.json"))[-1].read_text(encoding="utf-8")) \
        if list((sb.HERE / "output").glob("*_steelbench/result.json")) else None
    if r is None:
        pytest.skip("yerel çalıştırma sonucu yok")
    r["comparison"] = None
    md = sb.render_md(r)
    assert "KNOWN_FALSE_ALARM" in md and "Karşılaştırma yok." in md and "NOT_MEASURABLE" in md
