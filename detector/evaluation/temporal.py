"""Sentetik zamansal senaryolar: senaryoya göre üretilmiş (ideal veya kontrollü bozulmuş) tespitler, ÜRETİM karar
zincirinden (pipeline.DecisionStep = match_items + PPEMonitor + IncidentTracker) geçirilir.

Bu bir MODEL doğruluk ölçümü DEĞİLDİR: karar mantığının gecikme, kapanış, tekrar alarm ve hata eşiklerini belgelemek
içindir. Gerçek tespit hataları zamana bağlı ve ilişkilidir; i.i.d. benzetim yalnız duyarlılık göstergesidir."""
import random

import ppe_core as core

from .metrics import match_incidents, incidents_from_events, false_alarms_per_camera_hour
from .pipeline import DecisionStep

W, H = 1280, 720


def person_at(x: float, h: float = 300.0, y1: float = 200.0):
    """Kişi kutusu + ppe_core bölgelerine düşen baret/yelek kutuları (sol üst x)."""
    w = h / 3
    p = (x, y1, x + w, y1 + h)
    helmet = (x + 0.2 * w, y1 - 0.07 * h, x + 0.8 * w, y1 + 0.1 * h)
    vest = (x + 0.1 * w, y1 + 0.33 * h, x + 0.9 * w, y1 + 0.73 * h)
    return p, helmet, vest


class Script:
    """Kare kare kişi listesi üretir. actors: [{"pid": fn(t)->id|None, "x": fn(t), "helmet": fn(t)->bool,
    "vest": fn(t)->bool, "conf": float, "h": float, "y1": float}]"""

    def __init__(self, actors, duration: float, rate: float):
        self.actors, self.duration, self.rate = actors, duration, rate

    def frames(self):
        n = int(self.duration * self.rate)
        for i in range(1, n + 1):
            t = i / self.rate
            persons, helmets, vests = [], [], []
            for a in self.actors:
                pid = a["pid"](t)
                if pid is None:
                    continue
                p, hb, vb = person_at(a["x"](t), a.get("h", 300.0), a.get("y1", 200.0))
                persons.append((pid, p, a.get("conf", 0.9)))
                if a["helmet"](t):
                    helmets.append(hb)
                if a.get("vest", lambda _t: True)(t):
                    vests.append(vb)
            yield t, persons, helmets, vests


def run_script(script: Script, video_id: str = "synthetic"):
    step = DecisionStep("run-synthetic", "cam-synthetic", (W, H))
    step.person_frames = step.violation_frames = 0
    t = 0.0
    for t, persons, helmets, vests in script.frames():
        rec = step.step(t, persons, helmets, vests)
        step.person_frames += len(rec["people"])
        step.violation_frames += sum(p["status"] == "IHLAL" for p in rec["people"])
    step.close(t)
    pred = incidents_from_events(step.events, video_id=video_id, unclosed=step.tracker.unclosed)
    return step, pred, t


def _const(v):
    return lambda _t: v


def _between(a, b):
    return lambda t: a <= t < b


def _scenario(name, desc, script, gt, rate, pid_to_track=None, extra=None):
    step, pred, end = run_script(script)
    m = match_incidents(gt, pred, video_end=end, tol_start=1.0, tol_end=2.0, pid_to_track=pid_to_track)
    events = [{"type": e["type"], "pid": e["pid"], "t": e["t"], "missing": e["missing"]} for e in step.events]
    return {"scenario": name, "description": desc, "processed_rate_hz": rate, "duration_sec": end,
            "gt_incidents": gt, "pred_incidents": pred, "events": events, "unclosed": step.tracker.unclosed,
            "metrics": m, **(extra or {})}


def scenarios(rate: float = 12.5) -> list[dict]:
    """rate: işlenen kare hızı (üretim: 25 fps / VID_STRIDE 2 = 12.5 Hz)."""
    out = []
    A = {"pid": _const(1), "x": _const(400)}
    gt = lambda iid, item, s, e, tr="A": {"incident_id": iid, "video_id": "synthetic", "track_id": tr,  # noqa: E731
                                         "item": item, "start": s, "end": e}

    out.append(_scenario("helmet_off_on", "Uyumlu 0-10 sn, baret çıkarıldı 10-20 sn, tekrar takıldı 20-30 sn",
                         Script([dict(A, helmet=lambda t: not 10 <= t < 20)], 30, rate),
                         [gt("g1", "helmet", 10.0, 20.0)], rate))
    out.append(_scenario("vest_off_on", "Uyumlu 0-10 sn, yelek çıkarıldı 10-20 sn, tekrar giyildi 20-30 sn",
                         Script([dict(A, helmet=_const(True), vest=lambda t: not 10 <= t < 20)], 30, rate),
                         [gt("g1", "vest", 10.0, 20.0)], rate))
    out.append(_scenario("enter_without_helmet", "Kişi kadraja baretsiz girer (0-15 sn)",
                         Script([dict(A, helmet=_const(False))], 15, rate), [gt("g1", "helmet", 0.0, None)], rate))
    out.append(_scenario(
        "leave_return_long", "Baretsiz kişi 0-10 sn görünür, 10-22 sn kadraj dışında (> STALE_SEC), yeni ID ile döner",
        Script([dict(A, pid=lambda t: 1 if t < 10 else (None if t < 22 else 2), helmet=_const(False))], 35, rate),
        [gt("g1", "helmet", 0.0, None)], rate, pid_to_track={("synthetic", 1): "A", ("synthetic", 2): "A"}))
    out.append(_scenario(
        "leave_return_short", "Baretsiz kişi 3 sn kaybolur; ByteTrack track_buffer (30 kare ≈ 2.4 sn) aşıldığı için "
                              "yeni ID varsayılır",
        Script([dict(A, pid=lambda t: 1 if t < 10 else (None if t < 13 else 2), helmet=_const(False))], 30, rate),
        [gt("g1", "helmet", 0.0, None)], rate, pid_to_track={("synthetic", 1): "A", ("synthetic", 2): "A"}))
    out.append(_scenario(
        "id_swap", "A uyumlu (ID1), B baretsiz (ID2); t=15'te kesişip ID'ler yer değiştirir",
        Script([{"pid": lambda t: 1 if t < 15 else 2, "x": _const(300), "helmet": _const(True)},
                {"pid": lambda t: 2 if t < 15 else 1, "x": _const(700), "helmet": _const(False)}], 35, rate),
        [gt("g1", "helmet", 0.0, None, "B")], rate,
        pid_to_track=None,
        extra={"note": "pid_to_track verilmedi: ID değişimi incident eşleşmesinde yalnız zaman/ekipmanla izlenir"}))
    out.append(_scenario("person_disappears", "Baretsiz kişi t=10'da kadrajdan tamamen çıkar ve dönmez",
                         Script([dict(A, pid=lambda t: 1 if t < 10 else None, helmet=_const(False))], 30, rate),
                         [gt("g1", "helmet", 0.0, 10.0)], rate))
    out.append(_scenario("far_worker_small", "Baretsiz uzak çalışan, kişi kutusu 50 px (< MIN_PERSON_H_PX)",
                         Script([dict(A, h=50.0, helmet=_const(False))], 20, rate), [gt("g1", "helmet", 0.0, None)],
                         rate))
    out.append(_scenario("low_conf_person", "Baretsiz kişi, kişi güveni 0.35 (< PERSON_CONF_MIN)",
                         Script([dict(A, conf=0.35, helmet=_const(False))], 20, rate),
                         [gt("g1", "helmet", 0.0, None)], rate))
    out.append(_scenario("edge_person", "Baretsiz kişi kare kenarına değiyor (EDGE_MARGIN_PX)",
                         Script([dict(A, y1=3.0, helmet=_const(False))], 20, rate),
                         [gt("g1", "helmet", 0.0, None)], rate))
    return out


def head_hidden_sweep(rate: float = 12.5, durations=(1, 2, 3, 3.5, 4, 5, 6, 8)) -> list[dict]:
    """Uyumlu kişi eğilir/arkasını döner: baret tespiti D sn boyunca yok (kişi kutusu güvenilir). Yanlış alarm?"""
    out = []
    for d in durations:
        s = Script([{"pid": _const(1), "x": _const(400), "helmet": lambda t, d=d: not 10 <= t < 10 + d}],
                   10 + d + 10, rate)
        step, pred, _ = run_script(s)
        out.append({"hidden_sec": d, "false_incidents": len(pred),
                    "first_start_after_hide_sec": (pred[0]["start"] - 10) if pred else None})
    return out


def _bernoulli_flags(n, p_detect, burst_frames, rng):
    """Baret tespit dizisi: ortalama kaçırma patlaması burst_frames kare (Markov), uzun vadede tespit oranı p_detect."""
    if burst_frames <= 1:
        return [rng.random() < p_detect for _ in range(n)]
    p_end_miss = 1.0 / burst_frames
    p_start_miss = (1 - p_detect) * p_end_miss / max(p_detect, 1e-9)
    flags, miss = [], False
    for _ in range(n):
        miss = (rng.random() >= p_end_miss) if miss else (rng.random() < p_start_miss)
        flags.append(not miss)
    return flags


def flicker_sweep(rate: float = 12.5, minutes: float = 30.0, seed: int = 8,
                  p_values=(0.95, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2), bursts=(1, 12)) -> list[dict]:
    """Uyumlu kişide kare başına baret tespit olasılığı p (eşleşme dahil) -> saat başına yanlış incident.
    bursts: ortalama kaçırma patlaması (kare). 1 = bağımsız; 12 ≈ 1 sn (ilişkili kaçırma)."""
    out = []
    n = int(minutes * 60 * rate)
    for burst in bursts:
        for p in p_values:
            rng = random.Random(f"{seed}-{p}-{burst}")
            flags = _bernoulli_flags(n, p, burst, rng)
            s = Script([{"pid": _const(1), "x": _const(400), "helmet": lambda t, f=flags: f[min(int(round(t * rate)) - 1,
                                                                                                    len(f) - 1)]}],
                       minutes * 60, rate)
            step, pred, end = run_script(s)
            out.append({"p_helmet_detect": p, "mean_miss_burst_frames": burst, "simulated_min": minutes,
                        "false_incidents": len(pred),
                        "false_per_person_hour": false_alarms_per_camera_hour(len(pred), end),
                        "false_violation_time_fraction": step.violation_frames / max(1, step.person_frames)})
    return out


def false_positive_helmet_sweep(rate: float = 12.5, q_values=(0.1, 0.2, 0.3, 0.4, 0.5), seed: int = 8,
                                duration: float = 60.0, trials: int = 20) -> list[dict]:
    """Baretsiz kişide kare başına sahte baret tespiti olasılığı q (benzer şapka/saç/arka plan) -> kaçırma ve gecikme."""
    out = []
    n = int(duration * rate)
    for q in q_values:
        missed, lat = 0, []
        for k in range(trials):
            rng = random.Random(f"{seed}-{q}-{k}")
            flags = [rng.random() < q for _ in range(n)]
            s = Script([{"pid": _const(1), "x": _const(400),
                         "helmet": lambda t, f=flags: f[min(int(round(t * rate)) - 1, len(f) - 1)]}], duration, rate)
            _, pred, _ = run_script(s)
            if pred:
                lat.append(pred[0]["start"])
            else:
                missed += 1
        out.append({"q_false_helmet": q, "trials": trials, "missed": missed,
                    "mean_start_sec": (sum(lat) / len(lat)) if lat else None})
    return out


def decision_parameters() -> dict:
    return {k: getattr(core, k) for k in ("WINDOW_SEC", "MIN_SPAN_SEC", "MIN_OBS", "ENTER_RATE", "EXIT_RATE",
                                          "PERSON_CONF_MIN", "MIN_PERSON_H_PX", "EDGE_MARGIN_PX", "REALERT_SEC",
                                          "STALE_SEC", "CLASS_HELMET", "CLASS_VEST", "CLASS_PERSON")}
