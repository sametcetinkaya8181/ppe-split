"""KKD (baret/yelek) karar mantığı. Colab kodundaki mantığın birebir aynısı;
tek fark: olaylar artık düz metin değil, dashboard'un okuyabileceği dict."""
import os
from collections import defaultdict, deque


def _f(name, default):
    return float(os.getenv(name, default))


CLASS_HELMET = int(os.getenv("CLASS_HELMET", 0))
CLASS_VEST = int(os.getenv("CLASS_VEST", 2))
CLASS_PERSON = int(os.getenv("CLASS_PERSON", 6))

WINDOW_SEC = _f("WINDOW_SEC", 4.0)
MIN_SPAN_SEC = _f("MIN_SPAN_SEC", 2.0)
MIN_OBS = int(_f("MIN_OBS", 8))
ENTER_RATE = _f("ENTER_RATE", 0.20)
EXIT_RATE = _f("EXIT_RATE", 0.40)
PERSON_CONF_MIN = _f("PERSON_CONF_MIN", 0.40)
MIN_PERSON_H_PX = _f("MIN_PERSON_H_PX", 60)
EDGE_MARGIN_PX = _f("EDGE_MARGIN_PX", 6)
REALERT_SEC = _f("REALERT_SEC", 3.0) or None
STALE_SEC = _f("STALE_SEC", 10.0)


def head_region(p):
    x1, y1, x2, y2 = p; w, h = x2 - x1, y2 - y1
    return (x1 - 0.15*w, y1 - 0.20*h, x2 + 0.15*w, y1 + 0.35*h)


def torso_region(p):
    x1, y1, x2, y2 = p; w, h = x2 - x1, y2 - y1
    return (x1 - 0.15*w, y1 + 0.15*h, x2 + 0.15*w, y1 + 0.80*h)


def center(b):
    return ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)


def inside(pt, r):
    return r[0] <= pt[0] <= r[2] and r[1] <= pt[1] <= r[3]


def match_items(persons, items, region_fn):
    """Her eşya en fazla 1 kişiye, her kişi en fazla 1 eşyaya. Döner: {pid: item_box}"""
    cands = []
    for pid, pbox, _ in persons:
        region = region_fn(pbox)
        ph = max(1.0, pbox[3] - pbox[1])
        anchor = ((region[0] + region[2]) / 2, (region[1] + region[3]) / 2)
        for j, ib in enumerate(items):
            c = center(ib)
            if inside(c, region):
                dist = ((c[0]-anchor[0])**2 + (c[1]-anchor[1])**2) ** 0.5 / ph
                cands.append((dist, pid, j))
    cands.sort()
    used_p, used_i, result = set(), set(), {}
    for _, pid, j in cands:
        if pid in used_p or j in used_i:
            continue
        used_p.add(pid); used_i.add(j); result[pid] = items[j]
    return result


class PPEMonitor:
    def __init__(self):
        self.obs = defaultdict(deque)
        self.state = {}
        self.first_seen, self.last_seen, self.last_alert = {}, {}, {}
        self.stats = defaultdict(lambda: {"n": 0, "h": 0, "v": 0, "first": None,
                                          "last": None, "ever_violation": False})

    def update(self, t, frame_wh, persons, helmet_map, vest_map):
        W, H = frame_wh
        statuses, events = {}, []

        for pid, box, conf in persons:
            pid = int(pid)
            self.first_seen.setdefault(pid, t)
            self.last_seen[pid] = t
            st = self.state.setdefault(pid, {"helmet_viol": False, "vest_viol": False, "decided": False})
            s = self.stats[pid]
            if s["first"] is None:
                s["first"] = t
            s["last"] = t

            x1, y1, x2, y2 = box
            reliable = (conf >= PERSON_CONF_MIN and (y2 - y1) >= MIN_PERSON_H_PX
                        and x1 > EDGE_MARGIN_PX and y1 > EDGE_MARGIN_PX
                        and x2 < W - EDGE_MARGIN_PX and y2 < H - EDGE_MARGIN_PX)

            q = self.obs[pid]
            if reliable:
                h_ok, v_ok = pid in helmet_map, pid in vest_map
                q.append((t, h_ok, v_ok))
                s["n"] += 1; s["h"] += h_ok; s["v"] += v_ok
            while q and t - q[0][0] > WINDOW_SEC:
                q.popleft()

            enough = len(q) >= MIN_OBS and (t - self.first_seen[pid]) >= MIN_SPAN_SEC
            h_rate = sum(o[1] for o in q) / len(q) if q else 0.0
            v_rate = sum(o[2] for o in q) / len(q) if q else 0.0

            if reliable and enough:
                was_viol = st["helmet_viol"] or st["vest_viol"]
                st["helmet_viol"] = (h_rate < ENTER_RATE) if not st["helmet_viol"] else (h_rate < EXIT_RATE)
                st["vest_viol"] = (v_rate < ENTER_RATE) if not st["vest_viol"] else (v_rate < EXIT_RATE)
                st["decided"] = True
                now_viol = st["helmet_viol"] or st["vest_viol"]
                missing = " + ".join(x for x, f in (("Baretsiz", st["helmet_viol"]),
                                                    ("Yeleksiz", st["vest_viol"])) if f)

                def ev(kind):
                    return {"type": kind, "pid": pid, "t": round(t, 1), "missing": missing,
                            "helmet_rate": round(h_rate * 100), "vest_rate": round(v_rate * 100),
                            "box": [int(x1), int(y1), int(x2), int(y2)]}

                if now_viol:
                    s["ever_violation"] = True
                    if not was_viol:
                        self.last_alert[pid] = t
                        events.append(ev("start"))
                    elif REALERT_SEC and t - self.last_alert.get(pid, 0) >= REALERT_SEC:
                        self.last_alert[pid] = t
                        events.append(ev("ongoing"))
                elif was_viol:
                    e = ev("end"); e["missing"] = ""
                    events.append(e)

            if not st["decided"]:
                status = "ANALIZ"
            elif st["helmet_viol"] or st["vest_viol"]:
                status = "IHLAL"
            else:
                status = "UYGUN"
            statuses[pid] = (status, h_rate, v_rate)

        for pid in [p for p, ts in self.last_seen.items() if t - ts > STALE_SEC]:
            for d in (self.obs, self.state, self.first_seen, self.last_seen, self.last_alert):
                d.pop(pid, None)
        return statuses, events
