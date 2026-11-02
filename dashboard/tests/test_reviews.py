"""FAZ 10-A.6: insan inceleme kararları. Otomatik kayıt ↔ insan kararı ayrımı, eski kayıtların UNREVIEWED kalması,
rol + kamera kapsamı (404 ile gizleme), CSRF, geçersiz geçişler, tekrar gönderim, iyimser kilit / eşzamanlılık,
yalnız-ekleme geçmiş ve denetim kaydı, filtre/sayım, migration veri koruma. Gerçek PostgreSQL 17 + ppe_app_test."""
import json
import threading
import uuid
from datetime import timedelta

import psycopg
import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import text

import app as app_module
from tests.conftest import APP_ROLE, ORIGIN, alembic_cfg, count, login, make_user, psycopg_conn, regrant_app, url_for
from tests.helpers import B64_A, T0, event, frame_payload, lifecycle, post_frame
from tests.test_access_control import client_as

OLD_INCIDENT_KEYS = {"incident_id", "camera_id", "camera_name", "camera_current_name", "source_verified",
                     "source_kind", "source_ref", "run_id", "track_id", "status", "status_reason", "verified",
                     "started_at", "ended_at", "last_event_at", "duration_sec", "video_first_sec", "video_last_sec",
                     "missing_helmet", "missing_vest", "missing", "missing_at_start", "event_count", "ongoing_count",
                     "snapshot", "snapshot_missing", "snapshot_restricted"}


def seed(api):
    """cam1'de kapalı (snapshot'lı), cam2'de kapalı incident."""
    r1, r2 = uuid.uuid4(), uuid.uuid4()
    inc1, evs1 = lifecycle(r1, cam="cam1")
    inc2, evs2 = lifecycle(r2, cam="cam2", snapshot=B64_A)
    assert post_frame(api, frame_payload(evs1, cam="cam1", run_id=r1)).status_code == 200
    assert post_frame(api, frame_payload(evs2, cam="cam2", run_id=r2)).status_code == 200
    return {"r1": r1, "r2": r2, "inc1": inc1, "inc2": inc2, "evs1": evs1}


def review(c, inc, status, version=0, note=None, request_id=None, **extra):
    body = {"status": status, "expected_version": version, "request_id": str(request_id or uuid.uuid4()), **extra}
    if note is not None:
        body["note"] = note
    return c.post(f"/api/history/incidents/{inc}/review", json=body, headers=ORIGIN)


def audit_rows(engine, event="incident_review"):
    with engine.connect() as c:
        return [dict(r._mapping) for r in c.execute(
            text("SELECT outcome, user_id, detail FROM audit_log WHERE event = :e ORDER BY id"), {"e": event})]


# ================================================================ varsayılan ve ayrım
def test_existing_incidents_default_to_unreviewed_without_rows(api, engine):
    s = seed(api)
    items = api.get("/api/history/incidents").json()["items"]
    assert {i["review"]["status"] for i in items} == {"UNREVIEWED"}
    assert all(i["review"]["version"] == 0 and i["review"]["reviewed_at"] is None for i in items)
    assert all(OLD_INCIDENT_KEYS <= set(i) for i in items)                   # eski istemci alanları korunur
    assert count(engine, "incident_reviews") == count(engine, "incident_review_history") == 0
    d = api.get(f"/api/history/incidents/{s['inc1']}").json()
    assert d["review"]["status"] == "UNREVIEWED" and d["verified"] is True    # verified = start olayı var (insan değil)


def test_supervisor_decision_does_not_change_lifecycle_or_automatic_counts(api, engine):
    s = seed(api)
    sup = client_as("supervisor", ["cam1"])
    before = api.get(f"/api/history/incidents/{s['inc1']}").json()
    summary_before = api.get("/api/history/summary").json()
    events_before = count(engine, "violation_events")
    r = review(sup, s["inc1"], "CONFIRMED_VIOLATION", note="Baret takılı değil, kare 2 net.")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["replayed"] is False and body["review"]["status"] == "CONFIRMED_VIOLATION"
    assert body["review"]["version"] == 1 and body["review"]["reviewed_by"].startswith("u_supervisor")
    after = api.get(f"/api/history/incidents/{s['inc1']}").json()
    for k in ("status", "status_reason", "verified", "started_at", "ended_at", "event_count", "timeline"):
        assert after[k] == before[k], k
    assert count(engine, "violation_events") == events_before
    summary = api.get("/api/history/summary").json()
    assert summary["incidents"] == summary_before["incidents"] and summary["by_status"] == summary_before["by_status"]
    assert summary["by_review_status"] == {"UNREVIEWED": 1, "CONFIRMED_VIOLATION": 1, "FALSE_ALARM": 0,
                                           "INCONCLUSIVE": 0}


# ================================================================ yetki
def test_review_authorization_matrix(api, anon, engine):
    s = seed(api)
    assert review(anon, s["inc1"], "FALSE_ALARM").status_code == 401
    assert review(client_as("viewer"), s["inc1"], "FALSE_ALARM").status_code == 403
    sup1 = client_as("supervisor", ["cam1"])
    out = review(sup1, s["inc2"], "FALSE_ALARM")                             # kapsam dışı kamera
    missing = review(sup1, uuid.uuid4(), "FALSE_ALARM")                      # hiç olmayan incident
    assert out.status_code == missing.status_code == 404 and out.json() == missing.json()
    assert review(sup1, s["inc1"], "FALSE_ALARM").status_code == 200
    assert review(client_as("admin"), s["inc2"], "INCONCLUSIVE").status_code == 200
    scoped_admin = client_as("admin", ["cam1"])                              # kapsam admin için de geçerli
    assert review(scoped_admin, s["inc2"], "FALSE_ALARM", version=1, note="x").status_code == 404
    denied = [a for a in audit_rows(engine) if a["outcome"] == "denied"]
    assert len(denied) == 3 and all(a["detail"]["reason"] == "not_found_or_out_of_scope" for a in denied)
    assert count(engine, "incident_review_history") == 2


def test_review_requires_csrf_and_same_origin(api, engine):
    s = seed(api)
    sup = client_as("supervisor")
    tok = sup.headers.pop("X-CSRF-Token")
    assert review(sup, s["inc1"], "FALSE_ALARM").status_code == 403
    sup.headers["X-CSRF-Token"] = "x" * 43
    assert review(sup, s["inc1"], "FALSE_ALARM").status_code == 403
    sup.headers["X-CSRF-Token"] = tok
    r = sup.post(f"/api/history/incidents/{s['inc1']}/review", headers={"Origin": "http://evil.example"},
                 json={"status": "FALSE_ALARM", "expected_version": 0, "request_id": str(uuid.uuid4())})
    assert r.status_code == 403
    assert count(engine, "incident_reviews") == 0


def test_viewer_sees_outcome_but_not_reviewer_note_or_history(api):
    s = seed(api)
    assert review(api, s["inc1"], "FALSE_ALARM", note="Gölge baret gibi algılandı").status_code == 200
    viewer, scoped = client_as("viewer"), client_as("viewer", ["cam2"])
    item = [i for i in viewer.get("/api/history/incidents").json()["items"] if i["incident_id"] == str(s["inc1"])][0]
    assert item["review"]["status"] == "FALSE_ALARM" and item["review"]["version"] == 1
    assert not {"reviewed_by", "note", "annotation_candidate"} & set(item["review"])
    assert "Gölge" not in json.dumps(viewer.get(f"/api/history/incidents/{s['inc1']}").json())
    assert viewer.get(f"/api/history/incidents/{s['inc1']}/reviews").status_code == 403
    assert scoped.get(f"/api/history/incidents/{s['inc1']}").status_code == 404
    assert {i["incident_id"] for i in scoped.get("/api/history/incidents?review_status=FALSE_ALARM").json()["items"]} \
        == set()
    sup_out = client_as("supervisor", ["cam2"])
    a = sup_out.get(f"/api/history/incidents/{s['inc1']}/reviews")
    b = sup_out.get(f"/api/history/incidents/{uuid.uuid4()}/reviews")
    assert a.status_code == b.status_code == 404 and a.json() == b.json()


def test_me_reports_review_permission(api):
    assert api.get("/api/auth/me").json()["permissions"]["review"] is True
    assert client_as("supervisor").get("/api/auth/me").json()["permissions"]["review"] is True
    assert client_as("viewer").get("/api/auth/me").json()["permissions"]["review"] is False


def test_snapshot_rules_unchanged_by_review(api):
    s = seed(api)
    assert review(api, s["inc2"], "CONFIRMED_VIOLATION").status_code == 200
    snap = api.get(f"/api/history/incidents/{s['inc2']}").json()["snapshot"]
    assert snap and api.get(snap).status_code == 200
    assert client_as("viewer").get(snap).status_code == 403
    assert client_as("supervisor", ["cam1"]).get(snap).status_code == 404
    assert client_as("viewer").get(f"/api/history/incidents/{s['inc2']}").json()["snapshot_restricted"] is True


# ================================================================ geçersiz geçişler / doğrulama
@pytest.mark.parametrize("body,field", [
    ({"status": "UNREVIEWED"}, "status"), ({"status": "confirmed_violation"}, "status"), ({"status": "OK"}, "status"),
    ({"status": "FALSE_ALARM", "note": "a" * 501}, "note"), ({"status": "FALSE_ALARM", "note": "kötü\x00not"}, "note"),
    ({"status": "FALSE_ALARM", "expected_version": -1}, "expected_version"),
    ({"status": "FALSE_ALARM", "request_id": "yok"}, "request_id"), ({"status": "FALSE_ALARM", "extra": 1}, "extra"),
])
def test_invalid_review_requests_rejected(api, engine, body, field):
    s = seed(api)
    full = {"expected_version": 0, "request_id": str(uuid.uuid4()), **body}
    r = api.post(f"/api/history/incidents/{s['inc1']}/review", json=full, headers=ORIGIN)
    assert r.status_code == 422, r.text
    assert field in json.dumps(r.json())
    assert count(engine, "incident_reviews") == count(engine, "incident_review_history") == 0


def test_change_requires_note_and_real_change(api):
    s = seed(api)
    assert review(api, s["inc1"], "FALSE_ALARM").status_code == 200                     # ilk karar: not isteğe bağlı
    r = review(api, s["inc1"], "CONFIRMED_VIOLATION", version=1)
    assert r.status_code == 422 and r.json()["detail"][0]["type"] == "note_required"
    r = review(api, s["inc1"], "FALSE_ALARM", version=1, note="aynı")
    assert r.status_code == 422 and r.json()["detail"][0]["type"] == "no_change"
    assert review(api, s["inc1"], "FALSE_ALARM", version=1, note="   ").json()["detail"][0]["type"] == "no_change"


def test_changed_decision_keeps_full_history_and_audit(api, engine):
    s = seed(api)
    sup = client_as("supervisor", ["cam1"], name="isg_uzmani")
    assert review(sup, s["inc1"], "FALSE_ALARM", note="Makine kolu").status_code == 200
    r = review(api, s["inc1"], "CONFIRMED_VIOLATION", version=1, note="İkinci inceleme: gerçek ihlal")
    assert r.status_code == 200 and r.json()["review"]["version"] == 2
    h = sup.get(f"/api/history/incidents/{s['inc1']}/reviews").json()
    assert h["current"]["status"] == "CONFIRMED_VIOLATION" and h["current"]["reviewed_by"] == "test_admin"
    assert [(x["version"], x["status"], x["previous_status"], x["reviewed_by"]) for x in h["history"]] == [
        (1, "FALSE_ALARM", None, "isg_uzmani"), (2, "CONFIRMED_VIOLATION", "FALSE_ALARM", "test_admin")]
    rows = [a for a in audit_rows(engine) if a["outcome"] == "success"]
    assert [(a["detail"]["review_status"], a["detail"].get("previous_status"), a["detail"]["version"]) for a in rows] \
        == [("FALSE_ALARM", None, 1), ("CONFIRMED_VIOLATION", "FALSE_ALARM", 2)]
    assert all(a["detail"]["incident_id"] == str(s["inc1"]) and a["detail"]["camera_id"] == "cam1" for a in rows)
    assert "Makine" not in json.dumps([a["detail"] for a in audit_rows(engine)])     # serbest not denetime yazılmaz


def test_history_and_audit_are_append_only_for_app_role(api):
    s = seed(api)
    assert review(api, s["inc1"], "FALSE_ALARM").status_code == 200
    with psycopg_conn(url_for(role=APP_ROLE)) as c:
        for stmt in ("UPDATE incident_review_history SET status = 'CONFIRMED_VIOLATION'",
                     "DELETE FROM incident_review_history", "DELETE FROM incident_reviews",
                     "UPDATE audit_log SET event = 'x'"):
            with pytest.raises(psycopg.Error) as ei:
                c.execute(stmt)
            assert ei.value.sqlstate == "42501", stmt
            c.rollback()


def test_schema_constraints_reject_inconsistent_history(api, engine):
    s = seed(api)
    uid = api.get("/api/admin/users").json()[0]["id"]
    bad = [("INSERT INTO incident_review_history (incident_id, version, status, previous_status, reviewer_id, "
            "request_id) VALUES (:i, 2, 'FALSE_ALARM', NULL, :u, :r)", "first_has_no_previous"),
           ("INSERT INTO incident_review_history (incident_id, version, status, reviewer_id, request_id) "
            "VALUES (:i, 1, 'UNREVIEWED', :u, :r)", "status"),
           ("INSERT INTO incident_reviews (incident_id, status, version, reviewed_by) "
            "VALUES (:i, 'FALSE_ALARM', 0, :u)", "version_positive"),
           ("INSERT INTO incident_reviews (incident_id, status, version, reviewed_by) "
            "VALUES (gen_random_uuid(), 'FALSE_ALARM', 1, :u)", "incident_id_incidents")]
    for sql, name in bad:
        with engine.connect() as c, pytest.raises(Exception, match=name):
            c.execute(text(sql), {"i": s["inc1"], "u": uid, "r": uuid.uuid4()})


# ================================================================ tekrar gönderim / eşzamanlılık
def test_resubmission_with_same_request_id_is_idempotent(api, engine):
    s = seed(api)
    rid = uuid.uuid4()
    a = review(api, s["inc1"], "FALSE_ALARM", request_id=rid)
    b = review(api, s["inc1"], "FALSE_ALARM", request_id=rid)                 # çift tıklama / ağ tekrar denemesi
    assert a.status_code == b.status_code == 200
    assert a.json()["replayed"] is False and b.json()["replayed"] is True
    assert b.json()["review"] == a.json()["review"]
    assert count(engine, "incident_review_history") == 1
    assert len([x for x in audit_rows(engine) if x["outcome"] == "success"]) == 1
    r = review(api, s["inc1"], "INCONCLUSIVE", version=1, note="x", request_id=rid)
    assert r.status_code == 422 and r.json()["detail"][0]["type"] == "request_id_reused"
    other = review(api, s["inc2"], "FALSE_ALARM", request_id=rid)            # başka incident için aynı anahtar
    assert other.status_code == 422 and count(engine, "incident_reviews") == 1


def test_stale_version_gets_conflict_with_current_state(api, engine):
    s = seed(api)
    a, b = client_as("supervisor", name="uzman_a"), client_as("supervisor", name="uzman_b")
    assert a.get(f"/api/history/incidents/{s['inc1']}").json()["review"]["version"] == 0
    assert b.get(f"/api/history/incidents/{s['inc1']}").json()["review"]["version"] == 0
    assert review(a, s["inc1"], "FALSE_ALARM").status_code == 200
    r = review(b, s["inc1"], "CONFIRMED_VIOLATION")                          # eski sürümü görerek karar
    assert r.status_code == 409
    d = r.json()["detail"]
    assert d["code"] == "version_conflict" and d["current"]["status"] == "FALSE_ALARM"
    assert d["current"]["reviewed_by"] == "uzman_a" and d["current"]["version"] == 1
    r = review(b, s["inc1"], "CONFIRMED_VIOLATION", version=1, note="Görüntüyü yeniden inceledim")
    assert r.status_code == 200 and r.json()["review"]["version"] == 2
    r = review(a, s["inc1"], "INCONCLUSIVE", version=1, note="x")
    assert r.status_code == 409 and r.json()["detail"]["current"]["version"] == 2
    assert count(engine, "incident_review_history") == 2
    assert [x["detail"]["reason"] for x in audit_rows(engine) if x["outcome"] == "failure"] == ["version_conflict"] * 2


@pytest.mark.parametrize("expected", [0, 1])
def test_concurrent_decisions_exactly_one_wins(api, engine, expected):
    s = seed(api)
    if expected:
        assert review(api, s["inc1"], "INCONCLUSIVE").status_code == 200
    clients = [client_as("supervisor") for _ in range(8)]
    statuses = ("FALSE_ALARM", "CONFIRMED_VIOLATION")
    results, barrier = [], threading.Barrier(len(clients))

    def go(i, c):
        barrier.wait()
        results.append(review(c, s["inc1"], statuses[i % 2], version=expected, note=f"eşzamanlı {i}").status_code)

    threads = [threading.Thread(target=go, args=(i, c)) for i, c in enumerate(clients)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert sorted(results) == [200] + [409] * 7, results
    assert count(engine, "incident_review_history") == expected + 1
    with engine.connect() as c:
        assert c.execute(text("SELECT version FROM incident_reviews")).scalar() == expected + 1


def test_concurrent_resubmission_of_same_request_writes_once(api, engine):
    s = seed(api)
    rid, clients = uuid.uuid4(), [client_as("supervisor", name=f"tekrar_{i}") for i in range(2)]
    login(clients[1], "tekrar_0")                                            # aynı kullanıcı, iki sekme
    results, barrier = [], threading.Barrier(2)

    def go(c):
        barrier.wait()
        results.append(review(c, s["inc1"], "FALSE_ALARM", request_id=rid))

    threads = [threading.Thread(target=go, args=(c,)) for c in clients]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert sorted(r.status_code for r in results) == [200, 200]
    assert sorted(r.json()["replayed"] for r in results) == [False, True]
    assert count(engine, "incident_review_history") == 1


def test_concurrent_same_request_id_with_different_decision_is_422_not_500(api, engine):
    s = seed(api)
    rid, results, barrier = uuid.uuid4(), [], threading.Barrier(2)
    clients = [client_as("supervisor") for _ in range(2)]

    def go(c, st):
        barrier.wait()
        results.append(review(c, s["inc1"], st, request_id=rid))

    threads = [threading.Thread(target=go, args=(c, st)) for c, st in zip(clients, ("FALSE_ALARM", "INCONCLUSIVE"))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert sorted(r.status_code for r in results) == [200, 422]
    bad = [r for r in results if r.status_code == 422][0]
    assert bad.json()["detail"][0]["type"] == "request_id_reused"
    assert count(engine, "incident_review_history") == 1


def test_db_unreachable_review_503_without_secrets(api, engine, monkeypatch):
    s = seed(api)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://kullanici:GIZLI_PAROLA@olmayan-db:5432/x")
    for r in (review(api, s["inc1"], "FALSE_ALARM"), api.get(f"/api/history/incidents/{s['inc1']}/reviews")):
        assert r.status_code == 503 and "GIZLI" not in r.text and "olmayan-db" not in r.text
    assert count(engine, "incident_reviews") == 0


# ================================================================ filtre / sayım / yaşam döngüsü bağımsızlığı
def test_filter_and_counts_by_review_status_with_scope(api):
    runs = [uuid.uuid4() for _ in range(4)]
    incs = []
    for i, r in enumerate(runs):
        cam = "cam1" if i < 3 else "cam2"
        inc, evs = lifecycle(r, cam=cam, pid=10 + i, snapshot=None)
        incs.append(inc)
        assert post_frame(api, frame_payload(evs, cam=cam, run_id=r)).status_code == 200
    for inc, st in zip(incs, ("CONFIRMED_VIOLATION", "FALSE_ALARM", None, "FALSE_ALARM")):
        if st:
            assert review(api, inc, st).status_code == 200

    def ids(c, q):
        return {i["incident_id"] for i in c.get(f"/api/history/incidents?{q}").json()["items"]}
    assert ids(api, "review_status=FALSE_ALARM") == {str(incs[1]), str(incs[3])}
    assert ids(api, "review_status=UNREVIEWED") == {str(incs[2])}
    assert ids(api, "review_status=UNREVIEWED&review_status=CONFIRMED_VIOLATION") == {str(incs[0]), str(incs[2])}
    assert ids(api, "review_status=FALSE_ALARM&camera_id=cam2&status=closed") == {str(incs[3])}
    assert api.get("/api/history/incidents?review_status=false_alarm").status_code == 422
    total = api.get("/api/history/incidents?review_status=FALSE_ALARM&limit=1").json()
    assert total["total"] == 2 and total["next_cursor"]
    assert api.get("/api/history/summary").json()["by_review_status"] == {
        "UNREVIEWED": 1, "CONFIRMED_VIOLATION": 1, "FALSE_ALARM": 2, "INCONCLUSIVE": 0}
    scoped = client_as("viewer", ["cam1"])
    assert scoped.get("/api/history/summary").json()["by_review_status"] == {
        "UNREVIEWED": 1, "CONFIRMED_VIOLATION": 1, "FALSE_ALARM": 1, "INCONCLUSIVE": 0}
    assert ids(scoped, "review_status=FALSE_ALARM") == {str(incs[1])}


def test_events_after_review_and_retries_do_not_touch_review(api, engine):
    r, inc = uuid.uuid4(), uuid.uuid4()
    start = event("start", run_id=r, incident_id=inc)
    assert post_frame(api, frame_payload([start], run_id=r)).status_code == 200
    assert api.get(f"/api/history/incidents/{inc}").json()["status"] == "active"
    assert review(api, inc, "CONFIRMED_VIOLATION", note="canlı izlemede doğrulandı").status_code == 200
    later = [event("ongoing", run_id=r, incident_id=inc, at=T0 + timedelta(seconds=3)),
             event("end", run_id=r, incident_id=inc, at=T0 + timedelta(seconds=6))]
    for _ in range(2):                                                       # ikinci gönderim: dedektör tekrar denemesi
        assert post_frame(api, frame_payload(later, run_id=r)).status_code == 200
    assert post_frame(api, frame_payload([start], run_id=r)).status_code == 200
    d = api.get(f"/api/history/incidents/{inc}").json()
    assert d["status"] == "closed" and d["event_count"] == 3
    assert d["review"]["status"] == "CONFIRMED_VIOLATION" and d["review"]["version"] == 1
    assert count(engine, "incident_review_history") == 1


def test_annotation_candidate_is_a_flag_only(api, engine):
    s = seed(api)
    r = review(api, s["inc1"], "CONFIRMED_VIOLATION", annotation_candidate=True)
    assert r.status_code == 200 and r.json()["review"]["annotation_candidate"] is True
    r = review(api, s["inc1"], "CONFIRMED_VIOLATION", version=1, note="izin yok", annotation_candidate=False)
    assert r.status_code == 200 and r.json()["review"]["annotation_candidate"] is False   # tek başına değişiklik
    assert [h["annotation_candidate"] for h in api.get(f"/api/history/incidents/{s['inc1']}/reviews").json()
            ["history"]] == [True, False]
    paths = {getattr(rt, "path", "") for rt in app_module.app.routes}
    assert not [p for p in paths if "export" in p or "dataset" in p or "annotation" in p]   # dışa aktarım ucu yok


def test_disabled_reviewer_history_remains_attributable(api):
    s = seed(api)
    sup = client_as("supervisor", name="ayrilan_uzman")
    assert review(sup, s["inc1"], "FALSE_ALARM").status_code == 200
    uid = [u for u in api.get("/api/admin/users").json() if u["username"] == "ayrilan_uzman"][0]["id"]
    assert api.patch(f"/api/admin/users/{uid}", json={"disabled": True}, headers=ORIGIN).status_code == 200
    assert review(sup, s["inc1"], "INCONCLUSIVE", version=1, note="x").status_code == 401
    h = api.get(f"/api/history/incidents/{s['inc1']}/reviews").json()
    assert h["history"][0]["reviewed_by"] == "ayrilan_uzman"


# ================================================================ migration 0007 (yalnız ppe_test)
def _fingerprint(engine) -> str:
    with engine.connect() as c:
        rows = {t: [list(map(str, r)) for r in c.execute(text(f"SELECT * FROM {t} ORDER BY 1, 2"))]
                for t in ("cameras", "detector_runs", "incidents", "violation_events", "person_run_reports",
                          "app_users", "audit_log")}
    return json.dumps(rows, sort_keys=True)


def test_0007_upgrade_preserves_existing_data_and_defaults_unreviewed(api, engine, db_url):
    seed(api)
    app_module.flush_heartbeats()
    before = _fingerprint(engine)
    command.downgrade(alembic_cfg(), "0006")                                 # inceleme verisi yok: izinli
    try:
        with engine.connect() as c:
            assert not c.execute(text("SELECT to_regclass('public.incident_reviews')")).scalar()
        assert _fingerprint(engine) == before
        command.upgrade(alembic_cfg(), "head")
        regrant_app()
        assert _fingerprint(engine) == before                                # mevcut satırların hiçbiri değişmedi
        assert count(engine, "incident_reviews") == 0
        items = api.get("/api/history/incidents").json()["items"]
        assert len(items) == 2 and {i["review"]["status"] for i in items} == {"UNREVIEWED"}
    finally:
        command.upgrade(alembic_cfg(), "head")
        regrant_app()


def test_0007_downgrade_refuses_when_reviews_exist(api, engine, db_url):
    s = seed(api)
    assert review(api, s["inc1"], "FALSE_ALARM").status_code == 200
    with pytest.raises(RuntimeError, match="inceleme kararları var"):
        command.downgrade(alembic_cfg(), "0006")
    assert count(engine, "incident_review_history") == 1
    with engine.connect() as c:
        assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0007"
