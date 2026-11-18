"""FAZ 8 değerlendirme akışı: (1) evaluation.pipeline karar kopyası detector.run_once ile birebir aynı olayları
üretir, (2) no_helmet kararı etkilemez, (3) GT karşılaştırması kişi-kare / incident / takip metriklerini ayrı üretir,
(4) sentetik zamansal senaryolar deterministik ve beklenen üretim davranışını belgeler, (5) CLI GT doğrulama."""
import json

import pytest

import detector as d
import ppe_core as core
from evaluation import annotations as ann
from evaluation import cli
from evaluation import temporal as tp
from evaluation.pipeline import DecisionStep, extract, evaluate_against_gt, aggregate
from tests.test_run_once import SCENARIO, FakeModel, RecSender, _Result, FakeCap, FPS, W, H, SRC

KEYS = ("type", "pid", "t", "missing", "helmet_rate", "vest_rate", "box", "missing_helmet", "missing_vest")


@pytest.fixture
def _prod(monkeypatch):
    monkeypatch.setattr(d.cv2, "VideoCapture", FakeCap)
    monkeypatch.setattr(d, "VID_STRIDE", 1)
    monkeypatch.setattr(d, "LOOP", True)


def _replay(frames, stride=1, fps=FPS):
    step = DecisionStep("r", "cam1", (W, H))
    t = 0.0
    for idx, rows in enumerate(frames, start=1):
        t = idx * stride / fps
        persons, helmets, vests, _ = extract(_Result(rows))
        step.step(t, persons, helmets, vests)
    step.close(t)
    return step


def _incident_shape(events):
    order, out = {}, []
    for e in events:
        out.append(order.setdefault(e["incident_id"], len(order)))
    return out


def test_decision_replica_matches_production_run_once(_prod):
    s = RecSender()
    d.run_once(FakeModel(SCENARIO), "cam1", "Giriş", SRC, s, 1)
    step = _replay(SCENARIO)
    assert [tuple(e[k] for k in KEYS) for e in s.events] == [tuple(e[k] for k in KEYS) for e in step.events]
    assert _incident_shape(s.events) == _incident_shape(step.events)
    rep = s.reports[0]
    assert [(u["pid"], u["reason"], u["t"]) for u in rep["unclosed_incidents"]] == \
           [(u["pid"], u["reason"], u["t"]) for u in step.tracker.unclosed]
    assert len(step.events) >= 4                         # senaryo gerçekten olay üretiyor


def test_no_helmet_class_never_changes_decisions():
    noh = (7, (130, 90, 170, 130), 0.95, 777)
    with_noh = [rows + [noh] if rows else rows for rows in SCENARIO]
    a, b = _replay(SCENARIO), _replay(with_noh)
    assert [tuple(e[k] for k in KEYS) for e in a.events] == [tuple(e[k] for k in KEYS) for e in b.events]
    assert extract(_Result(with_noh[0]))[3] == 1         # yalnız sayılır


# ---------------------------------------------------------------- GT karşılaştırması (YOLO'suz uçtan uca)
def _synthetic_run(script, stride=1, fps=12.5):
    """temporal.Script çıktısını run_video biçimine çevirir (kare indeksi = idx*stride-1)."""
    step = DecisionStep("r", "cam_a", (tp.W, tp.H))
    frames, t = [], 0.0
    for idx, (t, persons, helmets, vests) in enumerate(script.frames(), start=1):
        rec = step.step(t, persons, helmets, vests)
        frames.append({"idx": idx, "frame": idx * stride - 1, "t": t, "people": rec["people"]})
    step.close(t)
    return {"frames": frames, "events": step.events, "unclosed": step.tracker.unclosed, "video_sec": t}


def _gt_doc(script, fps, every, incidents, helmet_fn, vis_fn=lambda t: "full"):
    frames = []
    for idx, (t, persons, _, _) in enumerate(script.frames(), start=1):
        if idx % every:
            continue
        ps = []
        for pid, box, _ in persons:
            vis = vis_fn(t)
            hs = helmet_fn(t)
            if vis == "head_hidden":
                hs = "unknown"
            p, hb, vb = tp.person_at(box[0])
            ps.append({"track_id": "A", "box": list(box), "visibility": vis, "helmet": hs,
                       "helmet_box": list(hb) if hs == "present" else None, "vest": "present", "vest_box": list(vb)})
        frames.append({"frame": idx - 1, "t": round((idx - 1) / fps, 4), "persons": ps})
    n = int(script.duration * fps) + 1
    return {"schema": ann.SCHEMA, "annotators": ["ann_A"], "frames": frames, "incidents": incidents,
            "video": {"video_id": "v1", "camera_id": "cam_a", "fps": fps, "frame_count": n, "width": tp.W,
                      "height": tp.H, "split": "test", "conditions": ["daylight"]}}


def test_evaluate_against_gt_person_frame_and_incident_levels_are_separate():
    fps = 12.5
    off = lambda t: not 10 <= t < 20                                             # noqa: E731
    script = tp.Script([{"pid": lambda t: 1, "x": lambda t: 400, "helmet": off}], 30, fps)
    run = _synthetic_run(script, fps=fps)
    doc = _gt_doc(script, fps, 5, [{"incident_id": "g1", "track_id": "A", "item": "helmet", "start": 10.0,
                                    "end": 20.0}], lambda t: "present" if off(t) else "missing")
    # GT kare zamanı (idx-1)/fps; run t = idx/fps -> aynı kare indeksi, üretim formülüyle 1 kare kayma belgeli
    assert ann.validate(doc)[0] == []
    ev = evaluate_against_gt(run, doc)
    pf = ev["person_frame"]["helmet"]
    assert pf["tp"] > 0 and pf["fn"] > 0                             # başlangıç gecikmesi süresince FN
    assert pf["fp"] > 0                                              # bitiş gecikmesi (EXIT_RATE) süresince FP
    assert pf["abstain_present"] > 0 and pf["fn_undecided"] == 0     # ilk MIN_SPAN: karar yok, TN sayılmaz
    inc = ev["incidents"]
    assert (inc["gt_incidents"], inc["detected"], inc["false_incidents"], inc["duplicates"]) == (1, 1, 0, 0)
    assert 3.0 <= inc["start_latency_sec"]["p50"] <= 3.4
    assert ev["tracking"]["id_switches"] == 0 and ev["tracking"]["fragmentation_extra_ids"] == 0
    assert ev["association"]["helmet"]["correct_item"] > 0
    assert inc["false_alarms_per_camera_hour"] == 0
    agg = aggregate([ev])
    assert agg["overall"]["incidents"]["recall"] == 1.0 and "daylight" in agg["by_condition"]


def test_head_hidden_frames_are_excluded_not_counted_as_violation():
    fps = 12.5
    hidden = lambda t: 10 <= t < 16                                          # noqa: E731
    script = tp.Script([{"pid": lambda t: 1, "x": lambda t: 400, "helmet": lambda t: not hidden(t)}], 30, fps)
    run = _synthetic_run(script, fps=fps)
    doc = _gt_doc(script, fps, 5, [], lambda t: "present",
                  vis_fn=lambda t: "head_hidden" if hidden(t) else "full")
    ev = evaluate_against_gt(run, doc)
    pf = ev["person_frame"]["helmet"]
    assert pf["excluded_unknown"] > 0 and pf["violation_on_unknown"] > 0      # sistem alarm verdi, GT unknown
    assert ev["incidents"]["false_incidents"] == 1                           # incident düzeyinde yanlış alarm
    by = ev["breakdown"]["visibility=head_hidden"]["helmet"]
    assert by["tp"] == 0 and by["fp"] == 0


def test_id_switch_and_fragmentation_counted():
    fps = 12.5
    script = tp.Script([{"pid": lambda t: 1 if t < 10 else 2, "x": lambda t: 400, "helmet": lambda t: True}], 20, fps)
    run = _synthetic_run(script, fps=fps)
    doc = _gt_doc(script, fps, 5, [], lambda t: "present")
    tr = evaluate_against_gt(run, doc)["tracking"]
    assert tr["id_switches"] == 1 and tr["fragmentation_extra_ids"] == 1 and tr["gt_tracks"] == 1


# ---------------------------------------------------------------- sentetik zamansal senaryolar
@pytest.fixture(scope="module")
def scen():
    return {s["scenario"]: s for s in tp.scenarios(12.5)}


def test_temporal_scenarios_document_production_behaviour(scen):
    m = scen["helmet_off_on"]["metrics"]
    assert m["detected"] == 1 and m["start_latency_sec"]["p50"] == pytest.approx(3.2, abs=0.1)
    assert m["end_error_sec"]["p50"] == pytest.approx(1.6, abs=0.1)
    assert scen["enter_without_helmet"]["metrics"]["start_latency_sec"]["p50"] == pytest.approx(2.1, abs=0.1)
    for name in ("leave_return_long", "leave_return_short", "id_swap"):
        assert scen[name]["metrics"]["duplicates"] == 1, name             # aynı ihlal için ikinci alarm
    swap = [(e["type"], e["pid"]) for e in scen["id_swap"]["events"] if e["type"] != "ongoing"]
    assert ("end", 2) in swap                                             # ihlal sürerken yanlış end
    gone = scen["person_disappears"]
    assert [u["reason"] for u in gone["unclosed"]] == ["stale"] and not any(e["type"] == "end" for e in gone["events"])
    for name in ("far_worker_small", "low_conf_person", "edge_person"):   # güvenilirlik kapısı -> tasarım gereği FN
        assert scen[name]["metrics"]["missed"] == 1 and scen[name]["metrics"]["pred_incidents"] == 0, name


def test_head_hidden_threshold():
    res = {r["hidden_sec"]: r["false_incidents"] for r in tp.head_hidden_sweep(12.5, durations=(3, 3.5))}
    assert res == {3: 0, 3.5: 1}


def test_temporal_results_reproducible():
    a = tp.flicker_sweep(12.5, minutes=1.0, p_values=(0.6, 0.3), bursts=(1, 12))
    b = tp.flicker_sweep(12.5, minutes=1.0, p_values=(0.6, 0.3), bursts=(1, 12))
    assert a == b
    strip = lambda s: [(e["type"], e["pid"], e["t"]) for e in s["events"]]   # noqa: E731 — uuid'ler hariç
    x, y = tp.scenarios(12.5), tp.scenarios(12.5)
    assert [strip(s) for s in x] == [strip(s) for s in y]


def test_decision_parameters_are_production_values():
    p = tp.decision_parameters()
    assert p["CLASS_HELMET"] == core.CLASS_HELMET == 0 and p["CLASS_VEST"] == 2 and p["CLASS_PERSON"] == 6
    assert p["WINDOW_SEC"] == core.WINDOW_SEC and p["ENTER_RATE"] == core.ENTER_RATE


# ---------------------------------------------------------------- CLI
def test_cli_validate_gt_and_example(tmp_path, capsys):
    good = tmp_path / "good.json"
    good.write_text(json.dumps(ann.example_document()), encoding="utf-8")
    bad_doc = ann.example_document()
    bad_doc["frames"][0]["persons"][0].update(visibility="head_hidden", helmet="missing")
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(bad_doc), encoding="utf-8")
    assert cli.main(["validate-gt", str(good)]) == 0
    assert cli.main(["validate-gt", str(good), str(bad)]) == 1
    assert "GEÇERSİZ" in capsys.readouterr().out
    assert cli.main(["example-gt"]) == 0
    assert json.loads(capsys.readouterr().out)["schema"] == ann.SCHEMA


def test_cli_sets_ultralytics_offline():
    import os
    assert os.environ.get("YOLO_OFFLINE") == "1"
