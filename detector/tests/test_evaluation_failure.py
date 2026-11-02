"""FAZ 10-A.5A hata analizi katmanı (evaluation/failure.py): eşya kanıtının kaybolduğu katmanın sınıflandırılması,
iz kaybı / parçalanma adayları / eşzamanlı çift tespit, kapsam kırılımı, karar katmanı yeniden oynatmasının üretimle
birebirliği ve sabitleri geri yüklemesi, kimlik parçalanmasının mükerrer incident mekanizması, doldurulmamış inceleme
şablonunun reddi. GPU/DB gerekmez."""
import pytest

import ppe_core as core
from evaluation import baseline as bl
from evaluation import failure as fa
from evaluation.pipeline import DecisionStep

WH = (1920, 1080)
P = [400, 200, 600, 800]                       # kişi kutusu; baş bölgesi ~ (370, 80, 630, 410)
HEAD = [460, 210, 540, 280]                    # baş bölgesinde
FAR = [1500, 900, 1560, 960]                   # kişi kutusu dışında


def det(c, s, b, i=None):
    d = {"c": c, "s": s, "b": list(b)}
    if i is not None:
        d["id"] = i
    return d


def person(pid, box=P, conf=0.9, gate=(), decided=False, helmet_box=None, vest_box=None, status="ANALIZ"):
    return {"pid": pid, "box": list(box), "conf": conf, "gate": list(gate), "decided": decided, "status": status,
            "helmet_box": helmet_box, "vest_box": vest_box, "h_rate": 0.0, "v_rate": 0.0,
            "helmet_viol": False, "vest_viol": False}


def frame(t, people, raw=(), tracked=(), probe=(), idx=None):
    return {"idx": idx or int(round(t * 10)), "frame": int(round(t * 10)), "t": t, "people": list(people),
            "raw": list(raw), "tracked": list(tracked), "probe": list(probe), "events": []}


# ---------------------------------------------------------------- E-01
@pytest.mark.parametrize("fr_kw, pkw, cause", [
    ({}, {"helmet_box": HEAD}, "matched"),
    ({"tracked": [det(0, 0.8, HEAD, 9)]}, {}, "tracked_item_unassigned"),
    ({"tracked": [det(0, 0.8, [420, 600, 480, 660], 9)]}, {}, "item_outside_region"),
    ({"raw": [det(0, 0.6, HEAD)]}, {}, "raw_item_dropped_by_tracker"),
    ({"probe": [det(0, 0.12, HEAD)]}, {}, "item_below_conf"),
    ({"probe": [det(0, 0.12, FAR)]}, {}, "no_detection"),
    ({}, {}, "no_detection"),
])
def test_item_cause_layers(fr_kw, pkw, cause):
    p = person(1, **pkw)
    assert fa.item_cause(p, frame(1.0, [p], **fr_kw))["cause"] == cause


def test_no_helmet_evidence_reported_separately():
    p = person(1)
    r = fa.item_cause(p, frame(1.0, [p], raw=[det(7, 0.5, HEAD)], probe=[det(7, 0.1, HEAD)]))
    assert r["cause"] == "no_detection" and r["no_helmet_raw_conf"] == 0.5 and r["no_helmet_probe_conf"] == 0.1


def test_item_cause_table_counts_only_gated_frames_by_default():
    diag = [frame(0.1, [person(1, helmet_box=HEAD)]), frame(0.2, [person(1)]),
            frame(0.3, [person(1, gate=["edge"])])]
    t = fa.item_cause_table(diag)
    assert t["person_frames"] == 2 and t["causes"]["matched"] == 1 and t["causes"]["no_detection"] == 1
    assert t["unmatched_no_helmet_evidence"] == {"no_helmet_absent": 1}
    assert fa.item_cause_table(diag, gated_only=False)["person_frames"] == 3


# ---------------------------------------------------------------- iz özellikleri
def test_class_history_and_person_track_features():
    diag = [frame(0.1, [person(5, conf=0.4)], raw=[det(6, 0.4, P)], tracked=[det(6, 0.4, P, 5)]),
            frame(0.2, [person(5, conf=0.6, helmet_box=HEAD)], raw=[], tracked=[det(6, 0.6, P, 5), det(2, 0.7, HEAD, 8)]),
            frame(0.3, [], tracked=[det(2, 0.7, P, 5)])]
    assert fa.class_history(diag) == {5: {6: 2, 2: 1}, 8: {2: 1}}
    t = fa.person_tracks(diag, WH)[5]
    assert t["frames"] == 2 and t["conf_median"] == 0.5 and t["first_conf"] == 0.4
    assert t["helmet_matched_ratio"] == 0.5 and t["raw_person_support_ratio"] == 0.5


def test_track_losses_kinds_and_last_frame_excluded():
    other = [900, 200, 1100, 800]
    diag = [frame(0.1, [person(1), person(2, other), person(3, [1300, 200, 1500, 800])]),
            frame(0.2, [person(4, other)], tracked=[det(2, 0.7, P, 1)], raw=[]),
            frame(0.3, [person(4, other)], raw=[det(6, 0.5, [1310, 210, 1500, 800])])]
    kinds = {x["pid"]: x["kind"] for x in fa.track_losses(diag, WH, buffer_sec=1.0)}
    assert kinds == {1: "id_continued_as_other_class", 2: "detection_gap", 3: "raw_person_near_after_loss"}
    # 2'nin kutusu 4'e geçti; son karede görünen 4 kayıp sayılmaz
    loss2 = next(x for x in fa.track_losses(diag, WH, 1.0) if x["pid"] == 2)
    assert loss2["box_taken_by_pids"] == {4: 2}


def test_fragment_candidates_heuristic():
    tracks = {1: {"pid": 1, "first_t": 0.1, "last_t": 1.0, "first_box": P, "last_box": P, "first_conf": 0.8},
              2: {"pid": 2, "first_t": 1.3, "last_t": 3.0, "first_box": [410, 200, 610, 800],
                  "last_box": P, "first_conf": 0.5},
              3: {"pid": 3, "first_t": 0.1, "last_t": 3.0, "first_box": FAR, "last_box": FAR, "first_conf": 0.9},
              4: {"pid": 4, "first_t": 9.0, "last_t": 9.5, "first_box": P, "last_box": P, "first_conf": 0.9}}
    c = fa.fragment_candidates(tracks, max_gap_sec=2.0, t0=0.1)
    assert [(x["prev_pid"], x["next_pid"]) for x in c] == [(1, 2)]      # 3 başta var, 4 boşluk çok uzun
    assert c[0]["iou_x_conf"] == pytest.approx(c[0]["iou_last_first"] * 0.5, abs=1e-3)


def test_concurrent_duplicates_and_max_concurrent():
    inner = [450, 300, 580, 700]
    diag = [frame(0.1, [person(1), person(2, inner)]), frame(0.2, [person(1), person(3, FAR)])]
    assert fa.concurrent_duplicates(diag) == {"1-2": 1}
    assert fa.max_concurrent(diag) == 2


# ---------------------------------------------------------------- E-03
def test_coverage_breakdown_edge_sides_and_warmup():
    bottom = [400, 200, 600, 1080]
    diag = [frame(0.1, [person(1, bottom, gate=["edge"]), person(2, gate=["low_conf"], conf=0.3),
                        person(3), person(4, decided=True)])]
    c = fa.coverage_breakdown(diag, WH)
    assert c["by_gate"] == {"edge": 1, "low_conf": 1, "evaluated": 2}
    assert c["edge_sides"] == {"bottom": 1} and c["edge_only_by_sides"] == {"bottom": 1}
    assert c["gated_in_warmup"] == 1 and c["gated_decided"] == 1


# ---------------------------------------------------------------- yeniden oynatma
def _production_diag(boxes_by_t, helmet=False):
    """PPEMonitor'ü doğrudan çalıştırıp (üretim) iz + olay üretir."""
    mon = core.PPEMonitor()
    diag, events = [], []
    for t, box in boxes_by_t:
        hm = {1: HEAD} if helmet else {}
        st, ev = mon.update(t, WH, [(1, box, 0.9)], hm, {1: [420, 400, 580, 650]})
        events += ev
        diag.append(frame(t, [person(1, box, helmet_box=hm.get(1), vest_box=[420, 400, 580, 650])],
                          raw=[det(0, 0.3, HEAD), det(2, 0.8, [420, 400, 580, 650])]))
    return diag, events


def test_replay_reproduces_production_and_restores_constants():
    diag, events = _production_diag([(i * 0.1, P) for i in range(1, 60)])
    before = core.PERSON_CONF_MIN
    r = fa.replay(diag, WH)
    assert events and fa.event_key(r["events"]) == fa.event_key(events)
    r2 = fa.replay(diag, WH, PERSON_CONF_MIN=0.95)                       # kişi güveni 0.9 -> hiç karar yok
    assert r2["starts"] == [] and r2["gated_person_frames"] == 0
    assert core.PERSON_CONF_MIN == before


def test_replay_edge_exempt_and_items_from_raw():
    bottom = [400, 200, 600, 1078]                                        # alt kenara değiyor
    diag, events = _production_diag([(i * 0.1, bottom) for i in range(1, 60)])
    assert events == [] and fa.replay(diag, WH)["gated_person_frames"] == 0
    r = fa.replay(diag, WH, edge_exempt=("bottom",))
    assert r["gated_person_frames"] == 59 and r["starts"] and r["starts"][0][2] == "Baretsiz"
    # ham baret (0.3) eşleştirmeye verilirse baret ihlali kalkar
    r3 = fa.replay(diag, WH, edge_exempt=("bottom",), items=fa.item_boxes_from("raw"))
    assert r3["starts"] == []
    assert fa.replay(diag, WH, edge_exempt=("bottom",), items=fa.item_boxes_from("raw", 0.5))["starts"]


# ---------------------------------------------------------------- E-04 mekanizması (mevcut davranışın belgesi)
def test_id_fragmentation_creates_second_incident_for_same_person():
    """Aynı fiziksel kişi (aynı kutu, baretsiz) tracker'da 1 -> 2 kimliğine geçerse PPEMonitor durumu sıfırdan başlar
    ve ikinci bir 'start' (yeni incident_id) üretir; eski incident end almadan stale kapanır. Davranış DEĞİŞTİRİLMEDİ;
    bu test riski belgeler."""
    step = DecisionStep("run", "cam", WH)
    vest = [[420, 400, 580, 650]]
    for i in range(1, 40):                                                # 3.9 sn kimlik 1
        step.step(i * 0.1, [(1, P, 0.9)], [], vest)
    for i in range(40, 200):                                              # 16 sn kimlik 2 (aynı kişi)
        step.step(i * 0.1, [(2, P, 0.9)], [], vest)
    starts = [e for e in step.events if e["type"] == "start"]
    assert [e["pid"] for e in starts] == [1, 2]
    assert starts[0]["incident_id"] != starts[1]["incident_id"]
    assert [u["reason"] for u in step.tracker.unclosed] == ["stale"]


# ---------------------------------------------------------------- insan incelemesi
def test_unfilled_review_template_is_rejected():
    videos = {"V2": {"incidents": [{"pid": 3, "start": 4.7, "review": "UNREVIEWED"}]}}
    with pytest.raises(ValueError):
        bl.apply_review(videos, {"incidents": [{"video": "V2", "pid": 3, "start_t": 4.7, "label": None}]})
    assert videos["V2"]["incidents"][0]["review"] == "UNREVIEWED"
