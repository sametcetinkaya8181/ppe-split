"""FAZ 4: geçmiş API'si (gerçek PostgreSQL 17, ayrı ppe_test veritabanı). Veriler gerçek ingest uçlarıyla yazılır;
yalnızca zaman/metadata koşulları (eski heartbeat vb.) doğrudan SQL ile hazırlanır."""
import logging
import re
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import text

import app as app_module
from tests.conftest import count
from tests.helpers import B64_A, T0, event, frame_payload, iso, lifecycle, post_frame, report

URL = "/api/history"


def ev(kind, run, inc, *, equip="helmet", cam="cam1", **kw):
    """equip: helmet | vest | both (end için yok sayılır)."""
    e = event(kind, run_id=run, incident_id=inc, cam=cam, **kw)
    if kind != "end":
        h, v = equip in ("helmet", "both"), equip in ("vest", "both")
        e.update(missing_helmet=h, missing_vest=v,
                 missing=" + ".join(x for x, f in (("Baretsiz", h), ("Yeleksiz", v)) if f))
    return e


def send(api, events, run, cam="cam1"):
    r = post_frame(api, frame_payload(events, cam=cam, run_id=run))
    assert r.status_code == 200, r.text


def start(api, run, *, cam="cam1", pid=7, at=T0, equip="helmet", snapshot=None):
    inc = uuid.uuid4()
    send(api, [ev("start", run, inc, cam=cam, pid=pid, at=at, equip=equip, snapshot=snapshot)], run, cam)
    return inc


def sql(engine, stmt, **p):
    with engine.begin() as c:
        c.execute(text(stmt), p)


def items(api, **params):
    r = api.get(f"{URL}/incidents", params={"limit": 200, **params})
    assert r.status_code == 200, r.text
    return r.json()


def by_id(api, **params):
    return {i["incident_id"]: i for i in items(api, **params)["items"]}


# ================================================================ boş DB
def test_empty_database(api):
    assert items(api) == {"items": [], "total": 0, "next_cursor": None, "limit": 200}
    s = api.get(f"{URL}/summary").json()
    assert s["incidents"] == {"incidents": 0, "verified": 0, "helmet": 0, "vest": 0, "both": 0, "helmet_only": 0,
                              "vest_only": 0}
    assert s["by_status"] == dict.fromkeys(("closed", "active", "stale", "run_end", "orphan", "unknown"), 0)
    assert s["events"] == {"total": 0, "start": 0, "ongoing": 0, "end": 0}
    assert s["by_camera"] == [] and s["daily"] == []
    ranged = api.get(f"{URL}/summary", params={"from": "2026-10-01T00:00:00Z", "to": "2026-10-04T00:00:00Z"}).json()
    assert [d["day"] for d in ranged["daily"]] == ["2026-10-01", "2026-10-02", "2026-10-03"]
    assert all(d["verified"] == 0 for d in ranged["daily"])
    assert api.get(f"{URL}/cameras").json() == []
    assert api.get(f"{URL}/runs").json() == {"items": [], "total": 0, "next_cursor": None, "limit": 50}


# ================================================================ incident = tek ihlal
def test_lifecycle_with_ongoing_is_one_closed_incident(api, engine):
    run, inc = uuid.uuid4(), uuid.uuid4()
    evs = [ev("start", run, inc, t=2.1, at=T0, snapshot=B64_A),
           ev("ongoing", run, inc, t=5.1, at=T0 + timedelta(seconds=3)),
           ev("ongoing", run, inc, t=8.1, at=T0 + timedelta(seconds=6)),
           ev("end", run, inc, t=9.4, at=T0 + timedelta(seconds=7.5))]
    for e in evs:
        send(api, [e], run)
    res = items(api)
    assert res["total"] == 1
    (i,) = res["items"]
    assert i["incident_id"] == str(inc) and i["status"] == "closed" and i["status_reason"] == "end_event"
    assert i["verified"] is True and i["track_id"] == 7 and i["camera_id"] == "cam1"
    assert i["camera_name"] == "Kamera cam1" and i["run_id"] == str(run)
    assert i["started_at"] == "2026-10-28T11:29:00Z" and i["ended_at"] == "2026-10-28T11:29:07.500000Z"
    assert i["duration_sec"] == 7.5 and i["video_first_sec"] == 2.1 and i["video_last_sec"] == 9.4
    assert i["event_count"] == 4 and i["ongoing_count"] == 2
    assert i["missing"] == "Baretsiz" and i["missing_helmet"] and not i["missing_vest"]
    assert i["snapshot"] == f"/snapshots/alert_{evs[0]['event_id']}.jpg" and i["snapshot_missing"] is False
    s = api.get(f"{URL}/summary").json()
    assert s["incidents"]["verified"] == 1 and s["incidents"]["incidents"] == 1
    assert s["events"] == {"total": 4, "start": 1, "ongoing": 2, "end": 1}


def test_many_ongoing_do_not_inflate_incident_count(api):
    run, inc = uuid.uuid4(), uuid.uuid4()
    send(api, [ev("start", run, inc, at=T0)] +
         [ev("ongoing", run, inc, t=3.0 + i, at=T0 + timedelta(seconds=3 + i)) for i in range(40)], run)
    s = api.get(f"{URL}/summary").json()
    assert s["incidents"]["verified"] == 1 and s["events"]["ongoing"] == 40 and s["events"]["total"] == 41
    assert s["by_camera"][0]["verified"] == 1 and s["by_camera"][0]["events"] == 41
    assert items(api)["total"] == 1


def test_equipment_union_over_incident(api):
    """ppe_core ihlal sürerken eksik ekipman kümesini değiştirebilir: incident ekipmanı tüm olayların birleşimi."""
    run, inc = uuid.uuid4(), uuid.uuid4()
    send(api, [ev("start", run, inc, equip="helmet"),
               ev("ongoing", run, inc, equip="both", t=5.1, at=T0 + timedelta(seconds=3))], run)
    (i,) = items(api)["items"]
    assert i["missing"] == "Baretsiz + Yeleksiz" and i["missing_at_start"] == "Baretsiz"
    assert items(api, equipment="vest")["total"] == 1 and items(api, equipment="helmet_only")["total"] == 0


# ================================================================ durum sınıflandırması
def test_status_classification(api, engine):
    expect = {}

    # closed (run tamamlandı)
    r_closed = uuid.uuid4()
    inc, evs = lifecycle(r_closed, snapshot=None)
    send(api, evs, r_closed)
    assert api.post("/ingest/report", json=report(r_closed)).status_code == 200
    expect[inc] = ("closed", "end_event")

    # stale / run_end / superseded / raporda yok -> aynı tamamlanmış run
    r_done = uuid.uuid4()
    stale, run_end, sup, missing = (start(api, r_done, pid=p) for p in (1, 2, 3, 4))
    unclosed = [{"incident_id": str(stale), "pid": 1, "reason": "stale", "t": 12.0},
                {"incident_id": str(run_end), "pid": 2, "reason": "run_end", "t": 30.0},
                {"incident_id": str(sup), "pid": 3, "reason": "superseded", "t": None}]
    assert api.post("/ingest/report", json=report(r_done, unclosed_incidents=unclosed)).status_code == 200
    expect.update({stale: ("stale", "stale"), run_end: ("run_end", "run_end"), sup: ("unknown", "superseded"),
                   missing: ("unknown", "not_reported")})

    # rapor listesi taşmış ve incident listede yok -> belirsiz (yanlışlıkla stale/run_end sayılmaz)
    r_ovf = uuid.uuid4()
    ovf = start(api, r_ovf)
    assert api.post("/ingest/report", json=report(r_ovf, unclosed_overflow=3)).status_code == 200
    expect[ovf] = ("unknown", "metadata_overflow")

    # tamamlanmış ama metadata'sı olmayan run
    r_nomd = uuid.uuid4()
    nomd = start(api, r_nomd)
    assert api.post("/ingest/report", json=report(r_nomd)).status_code == 200
    sql(engine, "UPDATE detector_runs SET report_metadata = NULL WHERE id = :r", r=r_nomd)
    expect[nomd] = ("unknown", "missing_metadata")

    # orphan: start'sız end ve ongoing
    r_orph = uuid.uuid4()
    o1, o2 = uuid.uuid4(), uuid.uuid4()
    send(api, [ev("end", r_orph, o1, orphan=True), ev("ongoing", r_orph, o2, orphan=True)], r_orph)
    expect.update({o1: ("orphan", "no_start_event"), o2: ("orphan", "no_start_event")})

    # run çalışıyor: canlı -> active; heartbeat eski -> unknown; run canlı ama incident sessiz -> unknown
    r_live, r_dead, r_quiet = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    live = start(api, r_live)
    dead = start(api, r_dead, cam="cam2")
    quiet = start(api, r_quiet, cam="cam3")
    sql(engine, "UPDATE detector_runs SET last_seen_at = now() - interval '1 hour' WHERE id = :r", r=r_dead)
    sql(engine, "UPDATE violation_events SET ingested_at = now() - interval '1 hour' WHERE incident_id = :i", i=quiet)
    expect.update({live: ("active", "recent_activity"), dead: ("unknown", "heartbeat_lost"),
                   quiet: ("unknown", "no_recent_event")})

    got = by_id(api)
    assert {uuid.UUID(k): (v["status"], v["status_reason"]) for k, v in got.items()} == expect
    assert got[str(o1)]["verified"] is False and got[str(stale)]["verified"] is True

    s = api.get(f"{URL}/summary").json()
    assert s["by_status"] == {"closed": 1, "active": 1, "stale": 1, "run_end": 1, "orphan": 2, "unknown": 6}
    assert s["incidents"]["incidents"] == 12 and s["incidents"]["verified"] == 10      # orphan doğrulanmış sayılmaz

    assert set(by_id(api, status="active")) == {str(live)}
    assert set(by_id(api, status=["stale", "run_end"])) == {str(stale), str(run_end)}


def test_end_missing_is_never_active_after_run_completes(api, engine):
    run = uuid.uuid4()
    inc = start(api, run)
    assert by_id(api)[str(inc)]["status"] == "active"
    assert api.post("/ingest/report", json=report(run)).status_code == 200          # raporda listelenmedi
    assert by_id(api)[str(inc)]["status"] == "unknown"


# ================================================================ sayfalama
def test_keyset_pagination_is_stable_with_ties_and_inserts(api):
    run = uuid.uuid4()
    ids = [start(api, run, pid=p, at=T0 + timedelta(minutes=p % 4)) for p in range(9)]   # aynı started_at'li gruplar
    seen, cursor, pages = [], None, 0
    while True:
        params = {"limit": 4, **({"cursor": cursor} if cursor else {})}
        r = api.get(f"{URL}/incidents", params=params).json()
        assert r["total"] == 9 + (1 if pages else 0)
        seen += [(i["started_at"], i["incident_id"]) for i in r["items"]]
        pages += 1
        if pages == 1:
            start(api, run, pid=50, at=T0 + timedelta(hours=1))                     # sayfalar arası yeni kayıt
        cursor = r["next_cursor"]
        if cursor is None:
            break
    assert pages == 3 and len(seen) == 9 and {s[1] for s in seen} == {str(i) for i in ids}
    assert seen == sorted(seen, reverse=True)                                        # (started_at, id) DESC


@pytest.mark.parametrize("cursor", ["bozuk", "e30", "eyJ0IjoiMjAyNi0xMC0yOFQxMToyOTowMCIsImsiOiJ4In0", "A" * 300])
def test_invalid_cursor_422(api, cursor):
    r = api.get(f"{URL}/incidents", params={"cursor": cursor})
    assert r.status_code == 422


# ================================================================ filtreler
def test_filters_camera_run_equipment(api):
    r1, r2 = uuid.uuid4(), uuid.uuid4()
    h = start(api, r1, equip="helmet")
    v = start(api, r1, equip="vest", pid=2)
    b = start(api, r2, cam="cam2", equip="both")
    assert set(by_id(api, camera_id="cam2")) == {str(b)}
    assert set(by_id(api, run_id=str(r1))) == {str(h), str(v)}
    assert set(by_id(api, equipment="helmet")) == {str(h), str(b)}
    assert set(by_id(api, equipment="vest")) == {str(v), str(b)}
    assert set(by_id(api, equipment="both")) == {str(b)}
    assert set(by_id(api, equipment="helmet_only")) == {str(h)}
    assert set(by_id(api, equipment="vest_only")) == {str(v)}
    assert by_id(api, camera_id="yok") == {}
    s = api.get(f"{URL}/summary").json()["incidents"]
    assert (s["helmet"], s["vest"], s["both"], s["helmet_only"], s["vest_only"]) == (2, 2, 1, 1, 1)


def test_utc_date_range_inclusive_from_exclusive_to(api):
    run = uuid.uuid4()
    a = start(api, run, at=T0)                                      # 11:29:00Z
    b = start(api, run, pid=2, at=T0 + timedelta(hours=1))          # 12:29:00Z
    assert set(by_id(api, **{"from": "2026-10-28T11:29:00Z"})) == {str(a), str(b)}
    assert set(by_id(api, **{"to": "2026-10-28T12:29:00Z"})) == {str(a)}
    # aynı an, farklı yazım: +03:00 -> UTC
    assert set(by_id(api, **{"from": "2026-10-28T15:00:00+03:00"})) == {str(b)}
    assert set(by_id(api, **{"from": "2026-10-28T12:00:00Z", "to": "2026-10-28T13:00:00Z"})) == {str(b)}


def test_incident_belongs_to_period_it_started_in(api):
    run, inc = uuid.uuid4(), uuid.uuid4()
    send(api, [ev("start", run, inc, at=T0), ev("end", run, inc, at=T0 + timedelta(hours=2))], run)
    assert by_id(api, **{"from": "2026-10-28T12:00:00Z"}) == {}
    assert set(by_id(api, **{"to": "2026-10-28T12:00:00Z"})) == {str(inc)}
    (i,) = by_id(api, **{"to": "2026-10-28T12:00:00Z"}).values()
    assert i["event_count"] == 2 and i["status"] == "closed"                   # aralık dışı end de sayıldı


# ================================================================ detay
def test_incident_detail_timeline_camera_run(api):
    run, inc = uuid.uuid4(), uuid.uuid4()
    evs = [ev("start", run, inc, at=T0, snapshot=B64_A),
           ev("ongoing", run, inc, t=5.1, at=T0 + timedelta(seconds=3)),
           ev("end", run, inc, t=7.4, at=T0 + timedelta(seconds=5))]
    send(api, list(reversed(evs)), run)                             # ters gönderim: zaman çizelgesi yine sıralı
    d = api.get(f"{URL}/incidents/{inc}").json()
    assert [e["type"] for e in d["timeline"]] == ["start", "ongoing", "end"]
    assert [e["event_id"] for e in d["timeline"]] == [e["event_id"] for e in evs]
    assert d["timeline"][0]["snapshot"] == f"/snapshots/alert_{evs[0]['event_id']}.jpg"
    assert d["timeline"][1]["snapshot"] is None and d["timeline"][2]["missing"] == ""
    assert d["camera"] == {"id": "cam1", "name": "Kamera cam1"}
    assert d["run"]["id"] == str(run) and d["run"]["status"] == "running" and d["run"]["loop_no"] == 1
    assert d["status"] == "closed" and d["unclosed"] is None and d["timeline_truncated"] is False


def test_incident_detail_shows_unclosed_reason(api):
    run = uuid.uuid4()
    inc = start(api, run)
    body = report(run, unclosed_incidents=[{"incident_id": str(inc), "pid": 7, "reason": "stale", "t": 12.0}])
    assert api.post("/ingest/report", json=body).status_code == 200
    d = api.get(f"{URL}/incidents/{inc}").json()
    assert d["status"] == "stale" and d["unclosed"] == {"reason": "stale", "video_time_sec": 12.0}
    assert d["run"]["status"] == "finished" and d["run"]["finished_at"] == "2026-10-28T11:29:30Z"


def test_incident_detail_404_and_422(api):
    assert api.get(f"{URL}/incidents/{uuid.uuid4()}").status_code == 404
    assert api.get(f"{URL}/incidents/degil-uuid").status_code == 422
    assert api.get(f"{URL}/runs/{uuid.uuid4()}").status_code == 404
    assert api.get(f"{URL}/runs/1%27%20OR%201=1").status_code == 422


# ================================================================ özet / analitik
def test_summary_camera_distribution_and_daily_timezone(api):
    r1, r2 = uuid.uuid4(), uuid.uuid4()
    start(api, r1, at=T0, equip="helmet")
    start(api, r1, pid=2, at=T0.replace(hour=22, minute=30), equip="vest")      # 22:30Z = ertesi gün 01:30 (+03)
    start(api, r2, cam="cam2", at=T0, equip="both")
    o = uuid.uuid4()
    send(api, [ev("end", r2, o, cam="cam2", orphan=True)], r2, cam="cam2")
    s = api.get(f"{URL}/summary").json()
    assert s["incidents"]["incidents"] == 4 and s["incidents"]["verified"] == 3
    cams = {c["camera_id"]: c for c in s["by_camera"]}
    assert (cams["cam1"]["verified"], cams["cam1"]["helmet"], cams["cam1"]["vest"], cams["cam1"]["events"]) == (2, 1, 1, 2)
    assert (cams["cam2"]["incidents"], cams["cam2"]["verified"], cams["cam2"]["events"]) == (2, 1, 2)
    assert s["daily"] == [{"day": "2026-10-28", "verified": 3, "helmet": 2, "vest": 2}]
    ist = api.get(f"{URL}/summary", params={"tz": "Europe/Istanbul"}).json()
    assert [(d["day"], d["verified"]) for d in ist["daily"]] == [("2026-10-28", 2), ("2026-10-29", 1)]
    only = api.get(f"{URL}/summary", params={"camera_id": "cam2"}).json()
    assert only["incidents"]["verified"] == 1 and [c["camera_id"] for c in only["by_camera"]] == ["cam2"]
    run_only = api.get(f"{URL}/summary", params={"run_id": str(r1)}).json()
    assert run_only["incidents"]["verified"] == 2 and run_only["events"]["total"] == 2


def test_summary_events_counted_by_own_time(api):
    run, inc = uuid.uuid4(), uuid.uuid4()
    send(api, [ev("start", run, inc, at=T0), ev("ongoing", run, inc, at=T0 + timedelta(hours=2)),
               ev("end", run, inc, at=T0 + timedelta(hours=2, seconds=1))], run)
    late = api.get(f"{URL}/summary", params={"from": "2026-10-28T13:00:00Z"}).json()
    assert late["incidents"]["verified"] == 0 and late["events"] == {"total": 2, "start": 0, "ongoing": 1, "end": 1}


# ================================================================ kameralar / run'lar
def test_cameras_and_runs(api, engine):
    r1, r2, r3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    inc, evs = lifecycle(r1)
    send(api, evs, r1)
    stale = start(api, r1, pid=9)
    body = report(r1, rows=[{"pid": 7, "first": 0.1, "last": 9.5, "n": 40, "helmet": 10, "vest": 95, "violation": True},
                            {"pid": 8, "first": 1.0, "last": 2.0, "n": 9, "helmet": 100, "vest": 100, "violation": False}],
                  unclosed_incidents=[{"incident_id": str(stale), "pid": 9, "reason": "stale", "t": 5.0}],
                  orphan_events=1, error="koptu rtsp://admin:S3cr3t@10.0.0.5/s")
    assert api.post("/ingest/report", json=body).status_code == 200
    send(api, [], r2)                                               # yalnızca heartbeat: canlı run
    start(api, r3, cam="cam2", at=T0 - timedelta(days=1))
    sql(engine, "UPDATE detector_runs SET started_at = :t WHERE id = :r", t=T0 - timedelta(days=1), r=r3)
    sql(engine, "UPDATE detector_runs SET last_seen_at = now() - interval '1 hour' WHERE id = :r", r=r3)

    cams = {c["id"]: c for c in api.get(f"{URL}/cameras").json()}
    assert set(cams) == {"cam1", "cam2"}
    assert cams["cam1"]["run_count"] == 2 and cams["cam1"]["incident_count"] == 2 and cams["cam1"]["event_count"] == 4
    assert cams["cam1"]["live_runs"] == 1 and cams["cam2"]["live_runs"] == 0 and cams["cam1"]["source_kind"] == "file"

    runs = api.get(f"{URL}/runs").json()
    assert runs["total"] == 3
    got = {r["run_id"]: r for r in runs["items"]}
    a = got[str(r1)]
    assert a["status"] == "finished" and a["liveness"] is None and a["person_count"] == 2
    assert a["persons_with_violation"] == 1 and a["incident_count"] == 2 and a["verified_incidents"] == 2
    assert a["event_count"] == 4 and a["report"]["unclosed_by_reason"] == {"stale": 1}
    assert a["report"]["orphan_events"] == 1 and "S3cr3t" not in a["report"]["error"]
    assert got[str(r2)]["liveness"] == "live" and got[str(r2)]["incident_count"] == 0
    assert got[str(r3)]["liveness"] == "no_heartbeat"

    assert [r["run_id"] for r in api.get(f"{URL}/runs", params={"camera_id": "cam2"}).json()["items"]] == [str(r3)]
    assert [r["run_id"] for r in api.get(f"{URL}/runs", params={"status": "finished"}).json()["items"]] == [str(r1)]
    assert [r["run_id"] for r in api.get(f"{URL}/runs", params={"to": iso(T0 - timedelta(hours=1))}).json()["items"]] \
        == [str(r3)]
    p1 = api.get(f"{URL}/runs", params={"limit": 2}).json()
    p2 = api.get(f"{URL}/runs", params={"limit": 2, "cursor": p1["next_cursor"]}).json()
    assert len(p1["items"]) == 2 and len(p2["items"]) == 1 and p2["next_cursor"] is None
    assert {r["run_id"] for r in p1["items"] + p2["items"]} == {str(r1), str(r2), str(r3)}

    d = api.get(f"{URL}/runs/{r1}").json()
    assert [p["track_id"] for p in d["persons"]] == [7, 8] and d["persons"][0]["first_sec"] == 0.1
    assert d["incidents_by_status"]["closed"] == 1 and d["incidents_by_status"]["stale"] == 1


# ================================================================ geçersiz girdi / güvenlik
@pytest.mark.parametrize("path,params", [
    ("incidents", {"limit": 0}), ("incidents", {"limit": 201}), ("incidents", {"limit": "x"}),
    ("incidents", {"camera_id": "cam 1"}), ("incidents", {"camera_id": "x" * 41}),
    ("incidents", {"run_id": "degil-uuid"}), ("incidents", {"status": "acik"}),
    ("incidents", {"equipment": "eldiven"}), ("incidents", {"from": "2026-10-28T11:00:00"}),
    ("incidents", {"from": "2026-10-28"}), ("incidents", {"from": "dün"}),
    ("incidents", {"from": "2026-10-28T12:00:00Z", "to": "2026-10-28T11:00:00Z"}),
    ("incidents", {"from": "2026-10-28T12:00:00Z", "to": "2026-10-28T12:00:00Z"}),
    ("summary", {"tz": "Mars/Olympus"}), ("summary", {"tz": "UTC'; DROP TABLE cameras;--"}),
    ("summary", {"to": "2026-13-45T00:00:00Z"}), ("runs", {"status": "running'--"}), ("runs", {"limit": 500}),
])
def test_invalid_input_422(api, path, params):
    r = api.get(f"{URL}/{path}", params=params)
    assert r.status_code == 422, (path, params, r.text)


@pytest.mark.parametrize("params", [
    {"camera_id": "cam1' OR '1'='1"}, {"camera_id": "cam1;DROP TABLE incidents"},
    {"equipment": "helmet' OR 1=1--"}, {"status": "closed') OR ('1'='1"},
    {"cursor": "' UNION SELECT * FROM cameras--"}, {"run_id": "00000000-0000-0000-0000-000000000000' OR '1'='1"},
])
def test_sql_injection_attempts_rejected_and_data_intact(api, engine, params):
    run = uuid.uuid4()
    start(api, run)
    r = api.get(f"{URL}/incidents", params=params)
    assert r.status_code == 422
    assert count(engine, "incidents") == 1 and count(engine, "cameras") == 1 and items(api)["total"] == 1


def test_camera_id_like_text_is_bound_not_interpolated(api):
    """Biçime uyan ama SQL'de anlamı olabilecek değerler parametre olarak bağlanır (sonuç: eşleşme yok)."""
    start(api, uuid.uuid4())
    assert items(api, camera_id="cam1--")["total"] == 0 and items(api, camera_id="_")["total"] == 0


def test_xss_payload_is_returned_as_plain_json_data(api):
    run = uuid.uuid4()
    xss = '<img src=x onerror="alert(1)">'
    payload = frame_payload([ev("start", run, uuid.uuid4())], run_id=run)
    payload["name"] = xss
    assert post_frame(api, payload).status_code == 200
    r = api.get(f"{URL}/incidents")
    assert r.headers["content-type"].startswith("application/json")
    assert r.json()["items"][0]["camera_name"] == xss                    # veri olarak; HTML'e dönüştürülmez
    assert api.get(f"{URL}/cameras").json()[0]["name"] == xss


def test_frontend_history_code_uses_safe_dom_apis():
    html = (app_module.STATIC / "index.html").read_text(encoding="utf-8")
    marker = "/* ---------- FAZ 4: geçmiş"
    assert marker in html
    hist = html.split(marker, 1)[1].split("</script>", 1)[0]
    assert "innerHTML" not in hist and "outerHTML" not in hist and "insertAdjacentHTML" not in hist
    assert "document.write" not in hist and "eval(" not in hist
    assert "textContent" in hist


def test_snapshot_links_only_for_safe_existing_files(api, engine):
    run = uuid.uuid4()
    inc = start(api, run, snapshot=B64_A)
    (i,) = items(api)["items"]
    assert i["snapshot"].startswith("/snapshots/alert_") and api.get(i["snapshot"]).status_code == 200
    for f in app_module.SNAP_DIR.iterdir():                          # dosya silinmiş/taşınmış
        f.unlink()
    (i,) = items(api)["items"]
    assert i["snapshot"] is None and i["snapshot_missing"] is True
    # DB kısıtına uyan ama beklenen ad biçiminde olmayan yol (ör. elle eklenmiş kayıt) servis edilmez
    (app_module.SNAP_DIR / "baska.jpg").write_bytes(b"x")
    sql(engine, "UPDATE violation_events SET snapshot_path = 'snapshots/baska.jpg' WHERE incident_id = :i", i=inc)
    d = api.get(f"{URL}/incidents/{inc}").json()
    assert d["snapshot"] is None and d["timeline"][0]["snapshot"] is None


def test_db_unreachable_history_503_without_secrets(api, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://kullanici:GIZLI_PAROLA@olmayan-db:5432/x")
    for path in ("incidents", "summary", "cameras", "runs", f"incidents/{uuid.uuid4()}", f"runs/{uuid.uuid4()}"):
        r = api.get(f"{URL}/{path}")
        assert r.status_code == 503, path
        assert "GIZLI_PAROLA" not in r.text and "olmayan-db" not in r.text
    assert "GIZLI_PAROLA" not in caplog.text


# ================================================================ kalıcılık / regresyon
def test_history_survives_dashboard_restart(api):
    run = uuid.uuid4()
    inc, evs = lifecycle(run)
    send(api, evs, run)
    with app_module.LOCK:                                            # RAM sıfırlanır (yeniden başlatma benzetimi)
        app_module.CAMS.clear()
        app_module.ALERTS.clear()
        app_module.ALERT_SEQ = 0
    assert api.get("/api/state").json()["alerts"] == []
    (i,) = items(api)["items"]
    assert i["incident_id"] == str(inc) and i["status"] == "closed"
    assert api.get(f"{URL}/summary").json()["incidents"]["verified"] == 1


def test_history_reads_do_not_touch_live_state(api, engine):
    run = uuid.uuid4()
    inc, evs = lifecycle(run)
    send(api, evs, run)
    before = (api.get("/api/state").json(), api.get("/api/alerts").json(), api.get("/healthz").json())
    for path in ("incidents", "summary", "cameras", "runs", f"incidents/{inc}", f"runs/{run}"):
        assert api.get(f"{URL}/{path}").status_code == 200
    assert (api.get("/api/state").json(), api.get("/api/alerts").json(), api.get("/healthz").json()) == before
    with api.websocket_connect("/ws") as ws:                         # binary protokol değişmedi
        msg = ws.receive_bytes()
    assert msg[0] == 0 and msg[1:].startswith(b"\xff\xd8")
    assert count(engine, "violation_events") == 3


def test_index_has_history_views():
    html = (app_module.STATIC / "index.html").read_text(encoding="utf-8")
    for needle in ('id="view-live"', 'id="view-history"', 'id="view-stats"', 'id="view-runs"',
                   "/api/history/incidents", "/api/history/summary", "/api/history/runs", "/api/history/cameras"):
        assert needle in html, needle
    assert re.search(r"setInterval\(tick, 700\)", html)                # canlı polling korunuyor
