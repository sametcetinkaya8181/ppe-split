"""Dashboard bellek probu (FAZ 10-A.1; YALNIZ izole ppe-rel yığını). Ürün koduna dokunmaz: container içinde
uvicorn'u aynı ayarlarla bu betikten başlatır ve aynı süreçte bir örnekleyici thread çalıştırır. Her örnek stderr'e
tek satır `MEMPROBE {json}` olarak yazılır (docker logs ile toplanır; dosya/volume gerekmez).

Ayrı ölçülenler:
  - süreç: VmRSS / RssAnon / RssFile (/proc/self/status), thread ve açık dosya tanıtıcısı sayısı
  - C ayırıcı (glibc mallinfo2): arena (sbrk yığını), uordblks (kullanımda), fordblks (boş ama OS'e dönmemiş),
    hblkhd (mmap bloklar) — "Python nesneleri büyüyor" ile "ayırıcı parçalanması"nı ayırır
  - Python: sys.getallocatedblocks() (pymalloc blokları), gc sayıları/toplamaları, GC izlenen nesne sayısı,
    seyrek tip histogramı (en çok 30), isteğe bağlı tracemalloc (taban çizgisine göre en büyük artışlar)
  - uygulama yapıları: CAMS, ALERTS, oturum önbelleği, denetim dedupe sözlüğü, IP sınırlayıcı, metrik kayıtları,
    SQLAlchemy derlenmiş ifade önbelleği ve havuz, sessionmaker alt sınıfı sayısı
Görüntü, token, oturum, kaynak adresi içermez (yalnız sayılar ve kaynak kodu satır konumları).

Ortam: MEMPROBE_INTERVAL (sn, 30), MEMPROBE_DEEP_EVERY (sn, 600; tip histogramı + tracemalloc karşılaştırması),
MEMPROBE_TRACE (tracemalloc çerçeve sayısı; 0 = kapalı), MEMPROBE_TRACE_BASELINE_SEC (900)."""
import collections
import ctypes
import gc
import json
import os
import sys
import threading
import time
import tracemalloc

INTERVAL = float(os.getenv("MEMPROBE_INTERVAL", 30))
DEEP_EVERY = float(os.getenv("MEMPROBE_DEEP_EVERY", 600))
TRACE = int(os.getenv("MEMPROBE_TRACE", 0))
TRACE_BASELINE_SEC = float(os.getenv("MEMPROBE_TRACE_BASELINE_SEC", 900))
if TRACE:
    tracemalloc.start(TRACE)
sys.path.insert(0, "/app")


class _MallInfo2(ctypes.Structure):
    _fields_ = [(n, ctypes.c_size_t) for n in ("arena", "ordblks", "smblks", "hblks", "hblkhd", "usmblks",
                                               "fsmblks", "uordblks", "fordblks", "keepcost")]


try:
    _libc = ctypes.CDLL("libc.so.6")
    _libc.mallinfo2.restype = _MallInfo2
except (OSError, AttributeError):
    _libc = None


def _status() -> dict:
    out = {}
    with open("/proc/self/status") as f:
        for line in f:
            k, _, v = line.partition(":")
            if k in ("VmRSS", "RssAnon", "RssFile", "VmData", "Threads"):
                out[k] = int(v.split()[0])
    return out


def _malloc() -> dict:
    if _libc is None:
        return {}
    m = _libc.mallinfo2()
    return {"arena_kb": m.arena >> 10, "in_use_kb": m.uordblks >> 10, "free_kb": m.fordblks >> 10,
            "mmap_kb": m.hblkhd >> 10, "mmap_blocks": m.hblks, "free_chunks": m.ordblks, "keepcost_kb": m.keepcost >> 10}


def _app_structs() -> dict:
    out = {}
    app = sys.modules.get("app")
    if app is not None:
        with app.LOCK:
            out["cams"] = len(app.CAMS)
            out["alerts"] = len(app.ALERTS)
            out["cam_report_rows"] = sum(len(c.report) for c in app.CAMS.values())
            out["cam_people"] = sum(len(c.people) for c in app.CAMS.values())
            out["cam_jpeg_kb"] = sum(len(c.jpeg or b"") for c in app.CAMS.values()) >> 10
        out["last_log"] = len(app._LAST_LOG)
        out["hb"] = len(app._HB)
        out["hb_pending"] = len(app._HB_PENDING)
    sec = sys.modules.get("auth.security")
    if sec is not None:
        out["auth_cache"] = len(sec._CACHE)
        out["audit_seen"] = len(sec._AUDIT_SEEN)
        out["ip_limiter"] = len(sec.IP_LIMITER.hits)
    ops = sys.modules.get("observability")
    if ops is not None:
        r = ops.REG
        with r.lock:
            out["ops_request_keys"] = len(r.requests)
            out["ops_latency_routes"] = len(r.latency)
            out["ops_latency_values"] = sum(len(d) for d in r.latency.values())
            out["ops_db_write_values"] = sum(len(d) for d in r.db_write.values())
            out["ops_counters"] = len(r.counters)
            out["ws_active"] = r.ws.get("active")
            out["frames_sent"] = r.ws.get("frames_sent")
        out["frames_received"] = r.counters.get("frames_received")
        out["events_inserted"] = r.counters.get("events_inserted")
        out["reports_persisted"] = r.counters.get("reports_persisted")
    sess = sys.modules.get("db.session")
    if sess is not None:
        try:
            info = sess._engine_for.cache_info()
            out["engines"] = info.currsize
            if info.currsize:
                eng = sess.get_engine()
                cc = getattr(eng, "_compiled_cache", None)
                out["sa_compiled_cache"] = len(cc) if cc is not None else None
                p = eng.pool
                out["pool"] = [p.size(), p.checkedout(), p.overflow()]
        except Exception as exc:                        # noqa: BLE001 — prob uygulamayı asla bozmaz
            out["sa_error"] = type(exc).__name__
    try:
        from sqlalchemy.orm import Session
        out["session_subclasses"] = len(Session.__subclasses__())
    except Exception:                                   # noqa: BLE001
        pass
    return out


def _deep(baseline):
    gc_objs = gc.get_objects()
    hist = collections.Counter(type(o).__name__ for o in gc_objs).most_common(30)
    del gc_objs
    out = {"types": hist}
    sess = sys.modules.get("db.session")
    if sess is not None and sess._engine_for.cache_info().currsize:
        # derlenmiş ifade önbelleği: hangi ifadeler girdi üretiyor (yalnız SQL'in ilk 70 karakteri; parametre değeri yok)
        cc = getattr(sess.get_engine(), "_compiled_cache", None) or {}
        heads = collections.Counter(" ".join(str(getattr(v, "string", "?")).split())[:70] for v in list(cc.values()))
        out["sa_cache_heads"] = heads.most_common(15)
    if TRACE and tracemalloc.is_tracing():
        snap = tracemalloc.take_snapshot().filter_traces((
            tracemalloc.Filter(False, tracemalloc.__file__), tracemalloc.Filter(False, __file__),
            tracemalloc.Filter(False, "<frozen importlib._bootstrap>"),
            tracemalloc.Filter(False, "<frozen importlib._bootstrap_external>")))
        if baseline[0] is None:
            if time.monotonic() - T0 >= TRACE_BASELINE_SEC:
                baseline[0] = snap
                out["trace_baseline"] = True
        else:
            diffs = snap.compare_to(baseline[0], "traceback" if TRACE > 1 else "lineno")
            out["trace_top"] = [{"kb": round(d.size_diff / 1024, 1), "n": d.count_diff,
                                 "where": [f"{fr.filename.replace('/usr/local/lib/python3.11/site-packages/', '')}:"
                                           f"{fr.lineno}" for fr in list(d.traceback)[:TRACE]]}
                                for d in diffs[:20] if d.size_diff > 0]
        cur, peak = tracemalloc.get_traced_memory()
        out["traced_kb"], out["traced_peak_kb"] = cur >> 10, peak >> 10
    return out


T0 = time.monotonic()


def _sampler():
    baseline = [None]
    next_deep = time.monotonic() + 120
    while True:
        time.sleep(INTERVAL)
        try:
            rec = {"t": round(time.monotonic() - T0, 1), "wall": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                   **_status(), "fds": len(os.listdir("/proc/self/fd")), "malloc": _malloc(),
                   "py_blocks": sys.getallocatedblocks(), "gc_count": gc.get_count(),
                   "gc_collections": [s["collections"] for s in gc.get_stats()],
                   "gc_uncollectable": sum(s["uncollectable"] for s in gc.get_stats()),
                   "gc_garbage": len(gc.garbage), "threads_py": threading.active_count(), "app": _app_structs()}
            if time.monotonic() >= next_deep:
                next_deep = time.monotonic() + DEEP_EVERY
                rec["deep"] = _deep(baseline)
            print("MEMPROBE " + json.dumps(rec, separators=(",", ":")), file=sys.stderr, flush=True)
        except Exception as exc:                        # noqa: BLE001
            print("MEMPROBE " + json.dumps({"error": type(exc).__name__, "msg": str(exc)[:200]}), file=sys.stderr,
                  flush=True)


if __name__ == "__main__":
    os.chdir("/app")
    threading.Thread(target=_sampler, name="memprobe", daemon=True).start()
    import uvicorn
    # Dockerfile CMD ile aynı: uvicorn app:app --host 0.0.0.0 --port 8000 --no-server-header
    uvicorn.run("app:app", host="0.0.0.0", port=8000, server_header=False)
