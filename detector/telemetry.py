"""FAZ 9 operasyonel gözlem — dedektör tarafı. Karar mantığını DEĞİŞTİRMEZ; yalnız ölçer ve raporlar.

Kamera (süreç) başına tek CameraTelemetry: süreç çalışma süresi, kamera bağlantı durumu, işlenen/kaynak FPS, kare
işleme süresi p50/p95/p99, kaynak zaman çizelgesine göre gecikme (lag), YOLO/ByteTrack/PPEMonitor süreleri,
CPU/RAM/GPU, sinyal kaybı (tek renk kare) ve donmuş görüntü işaretleri, ANALİZ KAPSAMI (güvenilirlik kapısı).

Gizlilik/kardinalite: kaynak adresi, token, görüntü içeriği, event_id/incident_id YOK. Track kimlikleri yalnız
süreç içi sayımda kullanılır; dışarı yalnız sayılar çıkar. Bellek sınırlı (kayan pencere + kapanan track'ler sayaca
dönüşür). Maliyet: kare başına birkaç sayaç güncellemesi + 64x36 küçültülmüş griden sinyal/donma denetimi; kaynak
örnekleme ve dosya yazımı HEALTH_EVERY saniyede bir.

Track sayısı benzersiz kişi sayısı DEĞİLDİR (ByteTrack aynı kişiye yeni kimlik verebilir; FAZ 8 parçalanma bulgusu).
ANALIZ durumu uygun (COMPLIANT) sayılmaz; burada 'değerlendirilemeyen' olarak ayrı raporlanır."""
import json
import os
import threading
import time
from collections import deque
from pathlib import Path

import ppe_core as core

GATE_REASONS = ("low_conf", "too_small", "edge")     # ppe_core.PPEMonitor güvenilirlik kapısının üç koşulu
HEALTH_EVERY = float(os.getenv("HEALTH_EVERY", 5))   # kaynak örnekleme + health.json yazma aralığı (sn)
LATENCY_WINDOW = int(os.getenv("HEALTH_WINDOW", 600))   # yüzdelikler için son N kare
LAG_WARN_SEC = float(os.getenv("LAG_WARN_SEC", 2.0))     # kaynak zaman çizelgesinin bu kadar gerisinde: uyarı
FREEZE_WARN_SEC = float(os.getenv("FREEZE_WARN_SEC", 10.0))   # bu kadar süre birebir aynı kare: donmuş olabilir
LONG_UNEVALUATED_SEC = float(os.getenv("LONG_UNEVALUATED_SEC", 10.0))   # bu kadar süredir karar alamayan kişi
STALL_WARN_SEC = float(os.getenv("STALL_WARN_SEC", 5.0))      # bağlıyken bu kadar süre hiç kare gelmedi: 'stalled'
NO_SIGNAL_STD = 2.0                                  # küçültülmüş griden std < 2: tek renk kare (sinyal yok)


def gate_reasons(box, conf, frame_wh) -> tuple:
    """PPEMonitor.update içindeki 'reliable' koşulunun tersinin nedenleri (aynı sabitler, aynı karşılaştırmalar).
    Boş demet = kişi bu karede değerlendirmeye alınır. tests/test_telemetry.py eşdeğerliği PPEMonitor ile doğrular."""
    W, H = frame_wh
    x1, y1, x2, y2 = box
    out = []
    if not conf >= core.PERSON_CONF_MIN:
        out.append("low_conf")
    if not (y2 - y1) >= core.MIN_PERSON_H_PX:
        out.append("too_small")
    if not (x1 > core.EDGE_MARGIN_PX and y1 > core.EDGE_MARGIN_PX
            and x2 < W - core.EDGE_MARGIN_PX and y2 < H - core.EDGE_MARGIN_PX):
        out.append("edge")
    return tuple(out)


def percentiles(values, ps=(50, 95, 99)) -> dict:
    """Doğrusal enterpolasyonlu yüzdelikler (numpy 'linear' ile aynı). Boş -> None değerler (uydurma yok)."""
    xs = sorted(v for v in values if v is not None)
    out = {}
    for p in ps:
        if not xs:
            out[f"p{p}"] = None
            continue
        k = (len(xs) - 1) * p / 100
        f = int(k)
        c = min(f + 1, len(xs) - 1)
        out[f"p{p}"] = round(xs[f] + (xs[c] - xs[f]) * (k - f), 3)
    out["max"] = round(xs[-1], 3) if xs else None
    out["n"] = len(xs)
    return out


def install_tracker_timer(model, sink: dict) -> None:
    """ByteTrack süresini ölçmek için predictor.trackers[*].update sarılır. Argümanlar/dönüş değeri aynen geçer
    (takip kararı değişmez; FAZ 8 eşdeğerlik testi aynı sarmalayıcıyla 60/60 kare özdeş)."""
    pred = getattr(model, "predictor", None)
    for trk in getattr(pred, "trackers", None) or []:
        if getattr(trk.update, "_ppe_timed", False):
            continue
        orig = trk.update

        def timed(*a, _orig=orig, **kw):
            s = time.perf_counter()
            try:
                return _orig(*a, **kw)
            finally:
                sink["bytetrack_ms"] = (time.perf_counter() - s) * 1e3
        timed._ppe_timed = True
        trk.update = timed


class _Resources:
    """Süreç CPU/RAM/tanıtıcı/thread + GPU (torch ayrılan bellek, NVML kullanım). Bağımlılık yoksa alan None."""

    def __init__(self, device: str | None):
        self.proc = None
        try:
            import psutil
            self.proc = psutil.Process()
            self.proc.cpu_percent(None)
        except Exception:                                  # noqa: BLE001
            pass
        self.gpu_index = int(device) if isinstance(device, str) and device.isdigit() else None
        self.nvml = None
        if self.gpu_index is not None:
            try:
                import pynvml
                pynvml.nvmlInit()
                self.nvml = (pynvml, pynvml.nvmlDeviceGetHandleByIndex(self.gpu_index))
            except Exception:                              # noqa: BLE001
                self.nvml = None

    def sample(self) -> dict:
        out = {"cpu_percent": None, "rss_mb": None, "threads": None, "handles": None,
               "gpu_util_percent": None, "gpu_mem_used_mb": None, "gpu_mem_total_mb": None,
               "torch_allocated_mb": None, "torch_reserved_mb": None}
        if self.proc is not None:
            try:
                with self.proc.oneshot():
                    out["cpu_percent"] = round(self.proc.cpu_percent(None), 1)
                    out["rss_mb"] = round(self.proc.memory_info().rss / 2**20, 1)
                    out["threads"] = self.proc.num_threads()
                    out["handles"] = (self.proc.num_handles() if hasattr(self.proc, "num_handles")
                                      else self.proc.num_fds())
            except Exception:                              # noqa: BLE001
                pass
        if self.nvml is not None:
            nv, h = self.nvml
            try:
                out["gpu_util_percent"] = nv.nvmlDeviceGetUtilizationRates(h).gpu
                mem = nv.nvmlDeviceGetMemoryInfo(h)
                out["gpu_mem_used_mb"] = round(mem.used / 2**20, 1)
                out["gpu_mem_total_mb"] = round(mem.total / 2**20, 1)
            except Exception:                              # noqa: BLE001
                pass
        if self.gpu_index is not None:
            try:
                import torch
                if torch.cuda.is_available():
                    out["torch_allocated_mb"] = round(torch.cuda.memory_allocated(self.gpu_index) / 2**20, 1)
                    out["torch_reserved_mb"] = round(torch.cuda.memory_reserved(self.gpu_index) / 2**20, 1)
            except Exception:                              # noqa: BLE001
                pass
        return out


class CameraTelemetry:
    """Tek kameranın ölçümleri. Üretici (run_once) thread'inden güncellenir; snapshot() başka thread'den okunabilir."""
    STAGES = ("frame_ms", "wait_ms", "preprocess_ms", "inference_ms", "postprocess_ms", "bytetrack_ms", "match_ms",
              "monitor_ms", "render_ms", "submit_ms")

    def __init__(self, camera_id: str, *, device: str | None = None, window: int = LATENCY_WINDOW,
                 clock=time.monotonic, wall=time.time, sample_resources: bool = True):
        self.camera_id = camera_id
        self.clock, self.wall = clock, wall
        self.lock = threading.Lock()
        self.started_mono = clock()
        self.started_at = wall()
        self.timings = {k: deque(maxlen=window) for k in self.STAGES}
        self.lag = deque(maxlen=window)
        self.startup = deque(maxlen=100)                   # run başlangıcı -> ilk kare (kaynak açma + model hazırlığı)
        self.state, self.state_since, self.last_error = "starting", clock(), None
        self.run = {"run_id": None, "loop": 0, "source_kind": None, "source_fps": None, "width": None, "height": None,
                    "vid_stride": None, "started_mono": None, "first_mono": None, "first_t": None,
                    "startup_sec": None, "frames": 0, "t": 0.0, "max_gap_sec": 0.0, "no_signal": 0}
        self.counters = {"frames_processed": 0, "runs_started": 0, "runs_finished": 0, "runs_failed": 0,
                         "source_open_failures": 0, "reconnect_attempts": 0, "no_signal_frames": 0,
                         "events_start": 0, "events_end": 0, "events_ongoing": 0}
        self.cov = {"track_frames": 0, "track_frames_unevaluated": 0,
                    "unevaluated_by_reason": {r: 0 for r in GATE_REASONS},
                    "track_frames_analyzing": 0, "tracks_closed": 0, "tracks_closed_never_evaluated": 0,
                    "never_evaluated_sec": deque(maxlen=1000), "never_evaluated_reason": {r: 0 for r in
                                                                                          GATE_REASONS + ("warmup",)}}
        self.tracks = {}                                   # pid -> {first, last, frames, gated, decided, reasons}
        self.current = {"visible": 0, "decided": 0, "analyzing": 0, "analyzing_by_reason": {}, "stale_decision": 0}
        self.proc_fps, self._fps_tick, self._fps_n = 0.0, clock(), 0
        self.signal = {"no_signal": False, "frozen_sec": 0.0}
        self._last_sig, self._same_since = None, None
        self.resources = _Resources(device) if sample_resources else None
        self.res_last = {}
        self.out_path: Path | None = None
        self._written = 0.0
        self.last_frame_mono = None
        self._write_lock = threading.Lock()
        self._flusher = None

    # ------------------------------------------------ yaşam döngüsü
    def set_output(self, path) -> None:
        self.out_path = Path(path) if path else None

    def start_flusher(self) -> None:
        """Sağlık dosyasını kare döngüsünden BAĞIMSIZ yazar: kaynak okuması bloke olsa (donmuş akış) bile dosya
        güncel kalır ve 'stalled' işareti görünür."""
        if self._flusher is not None or self.out_path is None:
            return

        def loop():
            while True:
                time.sleep(HEALTH_EVERY)
                self.flush(force=True)
        self._flusher = threading.Thread(target=loop, daemon=True, name=f"health-{self.camera_id}")
        self._flusher.start()

    def _set_state(self, state, error=None):
        if state != self.state:
            self.state, self.state_since = state, self.clock()
        if error is not None:
            self.last_error = str(error)[:200]

    def run_started(self, run_id, *, loop, source_kind, fps, width, height, vid_stride):
        with self.lock:
            self._close_tracks()
            self.run = {"run_id": str(run_id), "loop": loop, "source_kind": source_kind, "source_fps": round(fps, 2),
                        "width": width, "height": height, "vid_stride": vid_stride, "started_mono": self.clock(),
                        "first_mono": None, "first_t": None, "startup_sec": None, "frames": 0, "t": 0.0,
                        "max_gap_sec": 0.0, "no_signal": 0}
            self.counters["runs_started"] += 1
            self._last_sig, self._same_since = None, None
            self.last_frame_mono = None                    # önceki run'ın son karesi yeni run'da 'stalled' sayılmasın
            self._set_state("connected")

    def run_disturbed(self) -> bool:
        """Bu run'da akış dondu (kareler arası >= STALL_WARN_SEC) veya sinyal kaybı (>= 3 tek renk kare) görüldü mü.
        Ultralytics okuma hatasında kendisi TEK bir siyah kare üretir; bu tek kare donma/sinyal kaybı sayılmaz."""
        with self.lock:
            return self.run["no_signal"] >= 3 or self.run["max_gap_sec"] >= STALL_WARN_SEC

    def run_ended(self, status: str, error=None):
        with self.lock:
            self._close_tracks()
            self.counters["runs_finished" if status in ("finished", "interrupted") else "runs_failed"] += 1
            if status == "disconnected":
                self.counters["source_lost"] = self.counters.get("source_lost", 0) + 1
            self._set_state({"finished": "ended", "interrupted": "stopped"}.get(status, "disconnected"), error)
            self.current = {"visible": 0, "decided": 0, "analyzing": 0, "analyzing_by_reason": {}, "stale_decision": 0}
        self.flush(force=True)

    def open_failed(self, error):
        with self.lock:
            self.counters["source_open_failures"] += 1
            self._set_state("disconnected", error)
        self.flush(force=True)

    def reconnecting(self):
        with self.lock:
            self.counters["reconnect_attempts"] += 1
            self._set_state("reconnecting")

    # ------------------------------------------------ kare
    def frame(self, *, t, frame, persons, statuses, stage_ms: dict, events=()):
        """Bir karenin ölçümleri. persons: [(pid, box, conf)], statuses: PPEMonitor.update çıktısı."""
        now = self.clock()
        sig = _signal(frame)
        W, H = self.run["width"] or 0, self.run["height"] or 0
        if frame is not None and getattr(frame, "shape", None) is not None:
            H, W = frame.shape[:2]
        with self.lock:
            if self.run["first_mono"] is not None and self.last_frame_mono is not None:
                self.run["max_gap_sec"] = max(self.run["max_gap_sec"], round(now - self.last_frame_mono, 2))
            self.last_frame_mono = now
            self.counters["frames_processed"] += 1
            self.run["frames"] += 1
            self.run["t"] = t
            for k, v in stage_ms.items():
                if k in self.timings and v is not None:
                    self.timings[k].append(float(v))
            if self.run["first_mono"] is None:          # ilk kare: başlangıç süresi ayrı ölçülür
                self.run["first_mono"], self.run["first_t"] = now, t
                if self.run["started_mono"] is not None:
                    self.run["startup_sec"] = round(now - self.run["started_mono"], 3)
                    self.startup.append(self.run["startup_sec"])
            # gecikme: ilk kareden bu yana geçen gerçek süre - işlenen kaynak süresi (birikme; başlangıç hariç)
            self.lag.append((now - self.run["first_mono"]) - (t - self.run["first_t"]))
            self._fps_n += 1
            if now - self._fps_tick >= 1.0:
                self.proc_fps = round(self._fps_n / (now - self._fps_tick), 2)
                self._fps_tick, self._fps_n = now, 0
            for e in events:
                key = f"events_{e.get('type')}"
                if key in self.counters:
                    self.counters[key] += 1
            # sinyal / donma
            if sig is not None:
                std, digest = sig
                self.signal["no_signal"] = std < NO_SIGNAL_STD
                if self.signal["no_signal"]:
                    self.counters["no_signal_frames"] += 1
                    self.run["no_signal"] += 1
                if digest == self._last_sig:
                    self._same_since = self._same_since or now
                else:
                    self._same_since = None
                self._last_sig = digest
                self.signal["frozen_sec"] = round(now - self._same_since, 1) if self._same_since else 0.0
            # kapsam
            cur = {"visible": 0, "decided": 0, "analyzing": 0, "analyzing_by_reason": {}, "stale_decision": 0}
            for pid, box, conf in persons:
                pid = int(pid)
                reasons = gate_reasons(box, conf, (W, H))
                st = statuses.get(pid, ("ANALIZ", 0, 0))[0]
                decided = st != "ANALIZ"
                tr = self.tracks.get(pid)
                if tr is None:
                    tr = self.tracks[pid] = {"first": t, "last": t, "frames": 0, "gated": 0, "decided": False,
                                             "reasons": {}}
                tr["last"], tr["frames"] = t, tr["frames"] + 1
                tr["decided"] = tr["decided"] or decided
                self.cov["track_frames"] += 1
                cur["visible"] += 1
                if reasons:
                    tr["gated"] += 1
                    self.cov["track_frames_unevaluated"] += 1
                    for r in reasons:
                        self.cov["unevaluated_by_reason"][r] += 1
                        tr["reasons"][r] = tr["reasons"].get(r, 0) + 1
                if decided:
                    cur["decided"] += 1
                    if reasons:
                        cur["stale_decision"] += 1         # karar önceki güvenilir karelerden; şu an ölçülemiyor
                else:
                    cur["analyzing"] += 1
                    self.cov["track_frames_analyzing"] += 1
                    why = reasons[0] if reasons else "warmup"
                    cur["analyzing_by_reason"][why] = cur["analyzing_by_reason"].get(why, 0) + 1
            self.current = cur
            # PPEMonitor STALE_SEC sonra kişiyi unutur; burada da kapanmış say (bellek sınırlı kalır)
            for pid in [p for p, tr in self.tracks.items() if t - tr["last"] > core.STALE_SEC]:
                self._close_track(pid)
        if self._flusher is None:
            self.flush()

    def timings_add(self, stage_ms: dict) -> None:
        with self.lock:
            for k, v in stage_ms.items():
                if k in self.timings and v is not None:
                    self.timings[k].append(float(v))

    def _close_track(self, pid):
        tr = self.tracks.pop(pid)
        self.cov["tracks_closed"] += 1
        if not tr["decided"]:
            self.cov["tracks_closed_never_evaluated"] += 1
            self.cov["never_evaluated_sec"].append(round(tr["last"] - tr["first"], 2))
            why = max(tr["reasons"], key=tr["reasons"].get) if tr["reasons"] else "warmup"
            self.cov["never_evaluated_reason"][why] += 1

    def _close_tracks(self):
        for pid in list(self.tracks):
            self._close_track(pid)

    # ------------------------------------------------ çıktı
    def snapshot(self, resources: bool = True) -> dict:
        now = self.clock()
        if resources and self.resources is not None and (not self.res_last or now - self._written >= HEALTH_EVERY):
            self.res_last = self.resources.sample()
        with self.lock:
            lag = list(self.lag)
            cov = self.cov
            open_never = sum(1 for tr in self.tracks.values() if not tr["decided"])
            open_ages = [tr["last"] - tr["first"] for tr in self.tracks.values() if not tr["decided"]]
            open_long = sum(1 for a in open_ages if a >= LONG_UNEVALUATED_SEC)
            out = {
                "camera_id": self.camera_id,
                "updated_at": _iso(self.wall()),
                "process_uptime_sec": round(now - self.started_mono, 1),
                "state": self.state, "state_for_sec": round(now - self.state_since, 1),
                "last_error": self.last_error,
                "run": {k: v for k, v in self.run.items() if k not in ("started_mono", "first_mono", "first_t")},
                "startup_sec": percentiles(self.startup, (50, 95)),
                "proc_fps": self.proc_fps,
                "last_frame_age_sec": round(now - self.last_frame_mono, 2) if self.last_frame_mono else None,
                "lag_sec": {"current": round(lag[-1], 3) if lag else None, **percentiles(lag, (50, 95))},
                "timings_ms": {k: percentiles(v) for k, v in self.timings.items()},
                "signal": dict(self.signal),
                "counters": dict(self.counters),
                "coverage": {
                    "track_frames": cov["track_frames"],
                    "track_frames_unevaluated": cov["track_frames_unevaluated"],
                    "unevaluated_by_reason": dict(cov["unevaluated_by_reason"]),
                    "track_frames_analyzing": cov["track_frames_analyzing"],
                    "tracks_seen": cov["tracks_closed"] + len(self.tracks),
                    "tracks_closed": cov["tracks_closed"],
                    "tracks_closed_never_evaluated": cov["tracks_closed_never_evaluated"],
                    "tracks_open_not_yet_evaluated": open_never,
                    "open_unevaluated_long": open_long,
                    "open_unevaluated_max_sec": round(max(open_ages), 1) if open_ages else 0.0,
                    "never_evaluated_reason": dict(cov["never_evaluated_reason"]),
                    "never_evaluated_duration_sec": percentiles(cov["never_evaluated_sec"], (50, 95)),
                    "current": dict(self.current),
                    "note": "track sayısı benzersiz kişi sayısı değildir; ANALIZ uygun sayılmaz",
                },
                "resources": dict(self.res_last),
            }
        out["flags"] = health_flags(out)
        return out

    def compact(self) -> dict:
        """Dashboard'a kare meta'sıyla giden küçük özet (yalnız sayılar ve sabit sözlük değerleri)."""
        s = self.snapshot(resources=False)
        t = s["timings_ms"]
        cur = s["coverage"]["current"]
        return {"state": s["state"], "uptime_sec": s["process_uptime_sec"], "proc_fps": s["proc_fps"],
                "source_fps": s["run"]["source_fps"], "vid_stride": s["run"]["vid_stride"],
                "lag_sec": s["lag_sec"]["current"], "last_frame_age_sec": s["last_frame_age_sec"], "frame_ms_p95": t["frame_ms"]["p95"],
                "inference_ms_p95": t["inference_ms"]["p95"], "flags": s["flags"],
                "coverage": {"visible": cur["visible"], "decided": cur["decided"], "analyzing": cur["analyzing"],
                             "analyzing_by_reason": cur["analyzing_by_reason"],
                             "stale_decision": cur["stale_decision"],
                             "track_frames": s["coverage"]["track_frames"],
                             "track_frames_unevaluated": s["coverage"]["track_frames_unevaluated"],
                             "track_frames_analyzing": s["coverage"]["track_frames_analyzing"],
                             "unevaluated_by_reason": s["coverage"]["unevaluated_by_reason"],
                             "tracks_closed_never_evaluated": s["coverage"]["tracks_closed_never_evaluated"],
                             "never_evaluated_max_sec": s["coverage"]["never_evaluated_duration_sec"]["max"],
                             "open_unevaluated_long": s["coverage"]["open_unevaluated_long"],
                             "open_unevaluated_max_sec": s["coverage"]["open_unevaluated_max_sec"],
                             "tracks_seen": s["coverage"]["tracks_seen"]},
                "reconnects": s["counters"]["reconnect_attempts"]}

    def flush(self, force: bool = False) -> None:
        if self.out_path is None:
            return
        now = self.clock()
        if not force and now - self._written < HEALTH_EVERY:
            return
        data = self.snapshot()
        self._written = now
        with self._write_lock:                             # kare thread'i ve sağlık thread'i aynı dosyaya yazar
            try:
                tmp = Path(str(self.out_path) + ".tmp")
                tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
                os.replace(tmp, self.out_path)
            except OSError:
                pass


def health_flags(s: dict) -> list:
    """Operatöre görünür sağlık işaretleri (sabit sözlük). 'İhlal yok' ile 'kamera izlenemiyor' ayrı tutulur."""
    flags = []
    if s["state"] in ("disconnected", "reconnecting"):
        flags.append("camera_disconnected")
    age = s.get("last_frame_age_sec")
    if s["state"] == "connected" and age is not None and age >= STALL_WARN_SEC:
        flags.append("stalled")                            # kaynak bağlı görünüyor ama kare gelmiyor
    if s["signal"]["no_signal"]:
        flags.append("no_signal")
    if s["signal"]["frozen_sec"] >= FREEZE_WARN_SEC:
        flags.append("frozen")
    lag = s["lag_sec"]["current"]
    if lag is not None and lag >= LAG_WARN_SEC:
        flags.append("lagging")
    if s["coverage"]["current"]["analyzing"]:
        flags.append("unevaluated_persons")
    if s["coverage"].get("open_unevaluated_long"):
        flags.append("unevaluated_long")                   # >= LONG_UNEVALUATED_SEC karar alamayan kişi var
    return flags


def _signal(frame):
    """(seyrek örneklenmiş piksellerin std'si, bayt özeti) — tek renk kare ve birebir tekrar eden kare için.
    Dilimleme (kopya yok, ~64x36 nokta) kullanılır; 1080p karede ~0.05 ms."""
    if frame is None or getattr(frame, "ndim", 0) < 2 or frame.size == 0:
        return None
    try:
        h, w = frame.shape[:2]
        small = frame[::max(1, h // 36), ::max(1, w // 64)]
        return float(small.std()), hash(small.tobytes())
    except Exception:                                      # noqa: BLE001
        return None


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


_REGISTRY: dict = {}
_REG_LOCK = threading.Lock()


def for_camera(camera_id: str, **kw) -> CameraTelemetry:
    """Süreç içi tekil örnek (kamera başına bir süreç; yeniden bağlanmada aynı sayaçlar sürer)."""
    with _REG_LOCK:
        tel = _REGISTRY.get(camera_id)
        if tel is None:
            tel = _REGISTRY[camera_id] = CameraTelemetry(camera_id, **kw)
        return tel
