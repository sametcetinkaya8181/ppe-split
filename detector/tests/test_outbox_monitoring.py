"""FAZ 9 outbox izleme: replay başarı sayacı (yeniden açılışta önceki oturumdan kalan + teslim), WAL/dosya boyutu,
kapasite/yaş/disk/ölü mektup uyarıları (yalnız görünürlük — hiçbir mesaj silinmez), uyarı logu ve kare meta'sındaki
outbox özeti (gövde/kimlik içermez). GPU/ağ gerekmez."""
import json

import outbox as ob
from tests.conftest import wait_until
from tests.test_outbox import JPG, Fake, box, ev, rep  # noqa: F401 — box fixture


def test_replay_success_count_only_for_rows_from_previous_session(box):
    a = box("cam1")
    a.put_events([ev(), ev("ongoing")])
    a.close()
    b = box("cam1")                                       # yeniden başlatma
    b.put_events([ev("end")])
    rows = b.head(50, 10 ** 7)
    b.mark_delivered([r["seq"] for r in rows])
    m = b.metrics()
    assert m["delivered_count"] == 3 and m["replay_success_count"] == 2 and m["pending_count"] == 0


def test_metrics_include_sizes_capacity_and_no_warnings_when_healthy(box):
    o = box("cam1", max_items=10)
    o.put_events([ev()])
    m = o.metrics()
    assert m["db_bytes"] > 0 and m["wal_bytes"] is not None and m["disk_free_bytes"] > 0
    assert m["capacity_used_ratio"] == 0.1 and m["warnings"] == []


def test_warnings_for_age_watermark_dead_letters_and_disk(box, monkeypatch):
    o = box("cam1", max_items=4)
    o.put_events([ev() for _ in range(5)])        # start: %25 yedek alan
    seq = o.head(1, 10 ** 7)[0]["seq"]
    o.mark_failed(seq, "dead", "HTTP 422", 422)
    monkeypatch.setattr(ob, "AGE_WARN_SEC", 0.0)
    monkeypatch.setattr(ob, "MIN_FREE_BYTES", 1 << 62)
    w = o.metrics()["warnings"]
    assert {"high_watermark", "pending_age", "dead_letters", "disk_free_low"} <= set(w)
    assert o.metrics()["pending_count"] == 4                # uyarı hiçbir satırı silmedi
    assert ob.outbox_warnings({"state": "paused_auth", "memory_fallback": 2, "lost_unpersisted": 1}) == \
        ["memory_fallback", "delivery_paused", "messages_lost"]


def test_wal_size_is_bounded_after_checkpoint(box, monkeypatch):
    o = box("cam1")
    big = "x" * 200_000
    for _ in range(40):                                   # ~8 MB yazım
        o.put_events([ev(snapshot_b64=big)])
    rows = o.head(1000, 10 ** 9)
    o.mark_delivered([r["seq"] for r in rows])
    o.checkpoint()
    assert o.metrics()["wal_bytes"] <= ob.WAL_LIMIT_BYTES


def test_frame_payload_carries_outbox_summary_without_bodies_or_ids(box, monkeypatch):
    monkeypatch.setattr(ob, "AGE_WARN_SEC", 0.0)
    o = box("cam1")
    fake = Fake(default=503)                              # olaylar teslim edilemiyor -> bekliyor
    s = ob.DurableSender("cam1", "Giriş", "http://d", "", o, session=fake, backoff_base=0.01, backoff_max=0.05)
    logs = []
    s._log = logs.append
    s.start()
    e = ev()
    s.submit(JPG, {"meta": {"status": "running"}, "summary": {}, "people": []}, [e])
    assert wait_until(lambda: fake.of("/ingest/frame"), 5)
    meta = fake.of("/ingest/frame")[0]["payload"]["meta"]
    out = meta["outbox"]
    assert out["pending_count"] == 1 and "pending_age" in out["warnings"]
    assert e["event_id"] not in json.dumps(out) and e["incident_id"] not in json.dumps(out)
    s._write_status(force=True) if s.status_path else s._check_warnings(s.snapshot_stats())
    assert any("OUTBOX UYARISI: pending_age" in m for m in logs)
    s.close(0.2)


def test_fixed_token_used_on_first_retry_not_after_second_pause(box):
    """FAZ 9 (G1 bulgusu): token 401 anında okunuyor ve bir sonraki 401'e kadar kullanılmıyordu -> düzeltme ile
    teslim arasında 2 x auth_retry. Artık her yeniden denemeden hemen önce okunur: tek 401 + bir başarılı deneme."""
    from tests.test_outbox import sender, pending
    o = box()
    o.put_events([ev("start"), ev("end")])
    token = {"v": "eski"}
    sess = Fake([lambda c: 200 if c["token"] == "yeni" else 401] * 50)
    s = sender(o, sess, token_provider=lambda: token["v"], auth_retry=0.5)
    assert wait_until(lambda: s.state == ob.DurableSender.STATE_AUTH)
    token["v"] = "yeni"                                   # operatör pause sırasında düzeltti
    assert wait_until(lambda: pending(o) == 0, 3)
    ev_calls = sess.of("/ingest/events")
    assert [c["token"] for c in ev_calls] == ["tok", "yeni"]      # ikinci 401 beklenmedi
    s.close(1)
