"""FAZ 10-A.1 bellek: çok satırlı yazımlar (olaylar, incident'lar, rapor kişi satırları) executemany ile yapılır.
PostgreSQL upsert ifadeleri SQLAlchemy'de önbelleğe alınmaz; önceki `insert(...).values(rows)` her çağrıda satır
başına ~5 KB'lık çok satırlı ifadeyi derliyordu (5000 kişilik raporda ~51 MB geçici tepe). Ayrıca executemany'nin
sayfa sınırını (insertmanyvalues, 1000 satır) aşan rapor ve tekrar/çakışma davranışı korunur.
Gerçek PostgreSQL 17, ppe_app_test rolü."""
import tracemalloc
import uuid

from sqlalchemy import text

from tests.helpers import event, frame_payload, post_frame, report


def person_rows(n):
    return [{"pid": i + 1, "first": 0.1, "last": 9.5, "n": 40, "helmet": 10, "vest": 95, "violation": i % 2 == 0}
            for i in range(n)]


def started_run(api):
    r = uuid.uuid4()
    assert post_frame(api, frame_payload([event("start", run_id=r, incident_id=uuid.uuid4())], run_id=r)).status_code == 200
    return r


def test_readiness_check_does_not_grow_statement_cache(api):
    """Önceden her /health/ready ve /api/ops/metrics DB kontrolü (MigrationContext) yeni bir önbellek girdisi
    ekliyor ve alembic_version Table/MetaData nesnelerini canlı tutuyordu (LRU 500 girdiye kadar büyüme)."""
    from db.health import check_ready
    from db.session import get_engine
    assert check_ready()[0]
    before = len(get_engine()._compiled_cache)
    for _ in range(50):
        assert check_ready()[0]
    assert api.get("/health/ready").status_code == 200
    assert len(get_engine()._compiled_cache) - before <= 1


def test_large_report_write_has_bounded_transient_memory(api):
    r = started_run(api)
    body = report(r, rows=person_rows(5000))
    tracemalloc.start()
    try:
        assert api.post("/ingest/report", json=body).status_code == 200
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    # ölçülen: values(rows) ~51 MB, executemany ~15 MB (istek ayrıştırma dahil)
    assert peak < 30 * 2**20, f"5000 satırlık rapor yazımı {peak / 2**20:.1f} MB geçici bellek kullandı"


def test_report_larger_than_executemany_page_and_identical_replay(api, engine):
    r = started_run(api)
    body = report(r, rows=person_rows(2500))
    assert api.post("/ingest/report", json=body).status_code == 200
    assert api.post("/ingest/report", json=body).status_code == 200          # birebir tekrar: değişiklik yok
    with engine.begin() as c:
        n, viol = c.execute(text("SELECT count(*), count(*) FILTER (WHERE ever_violation) FROM person_run_reports "
                                 "WHERE run_id = :r"), {"r": r}).one()
    assert (n, viol) == (2500, 1250)
    changed = report(r, rows=person_rows(2499))
    assert api.post("/ingest/report", json=changed).status_code == 409       # farklı rapor hâlâ çelişki


def test_mixed_new_and_existing_events_report_only_new(api, engine):
    r = uuid.uuid4()
    old = [event("start", run_id=r, incident_id=uuid.uuid4(), pid=i + 1) for i in range(3)]
    body = {"cam": "cam1", "name": "Kamera cam1", "events": old}
    assert api.post("/ingest/events", json=body).json()["inserted"] == 3
    new = [event("start", run_id=r, incident_id=uuid.uuid4(), pid=i + 10) for i in range(2)]
    res = api.post("/ingest/events", json={**body, "events": old + new})
    assert res.status_code == 200 and res.json()["inserted"] == 2
    with engine.begin() as c:
        assert c.execute(text("SELECT count(*), count(DISTINCT event_id) FROM violation_events WHERE run_id = :r"),
                         {"r": r}).one() == (5, 5)
