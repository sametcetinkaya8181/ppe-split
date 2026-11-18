"""Dedektör -> dashboard gönderim protokolü: olay kimlikleri, incident yaşam döngüsü, kaynak maskeleme ve
güvenilir Sender. YOLO/torch/cv2 içermez; GPU olmadan test edilebilir.

Teslim garantisi: olaylar ve raporlar YALNIZCA BELLEKTE tutulur. Süreç yaşadığı ve dashboard'a ulaşıldığı sürece
her olay en az bir kez gönderilir (aynı event_id ile; tekrarları sunucu ayıklar). Süreç çöker veya zorla
sonlandırılırsa (Görev Yöneticisi, terminate, elektrik kesintisi) kuyruktaki olaylar KAYBOLUR."""
import email.utils
import json
import random
import re
import threading
import time
import uuid
from collections import Counter, deque
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests


# ---------------------------------------------------------------- zaman / kimlik
def utc_now_iso() -> str:
    """Gerçek dünya zamanı, UTC, ISO 8601 (ör. 2026-10-28T11:29:00.123Z). Video saniyesi (t) ile karıştırılmaz."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------- missing metni -> boolean
class ProtocolError(ValueError):
    """ppe_core olay sözleşmesinin dışında kalan değer (sessizce kabul edilmez)."""


HELMET_TEXT, VEST_TEXT = "Baretsiz", "Yeleksiz"     # ppe_core.PPEMonitor.update() ile aynı sabitler


def missing_flags(kind: str, missing: str) -> tuple[bool, bool]:
    """'Baretsiz' / 'Yeleksiz' / 'Baretsiz + Yeleksiz' -> (missing_helmet, missing_vest). end -> (False, False)."""
    if kind == "end":
        if missing:
            raise ProtocolError(f"end olayında missing boş olmalı, gelen: {missing!r}")
        return False, False
    if kind not in ("start", "ongoing"):
        raise ProtocolError(f"bilinmeyen olay türü: {kind!r}")
    parts = missing.split(" + ") if isinstance(missing, str) and missing else []
    if not parts or len(set(parts)) != len(parts) or not set(parts) <= {HELMET_TEXT, VEST_TEXT}:
        raise ProtocolError(f"beklenmeyen missing metni: {missing!r}")
    return HELMET_TEXT in parts, VEST_TEXT in parts


# ---------------------------------------------------------------- kaynak türü / maskeleme
SENSITIVE_KEYS = {"password", "passwd", "pass", "pwd", "user", "username", "login", "token", "access_token",
                  "auth", "authorization", "key", "apikey", "api_key", "secret", "signature", "sig", "credential",
                  "credentials", "session", "sessionid"}
_URL_USERINFO = re.compile(r"\b([a-zA-Z][a-zA-Z0-9+.\-]*://)[^/\s@]+@")
_QUERY_SECRET = re.compile(r"([?&](?:%s)=)[^&\s#]*" % "|".join(sorted(SENSITIVE_KEYS, key=len, reverse=True)),
                           re.IGNORECASE)


def _is_url(s: str) -> bool:
    p = urlsplit(s)
    return len(p.scheme) > 1 and bool(p.netloc)       # 'C:\\video.mp4' URL değildir


def source_kind(source) -> str:
    s = str(source).strip()
    if s.isdigit():
        return "webcam"
    if "youtube.com/" in s or "youtu.be/" in s:
        return "youtube"
    if _is_url(s):
        return "rtsp" if urlsplit(s).scheme.lower() in ("rtsp", "rtsps") else "other"
    return "file"


def redact_source(source) -> str:
    """URL'deki kullanıcı adı/parolayı ve hassas sorgu parametrelerini gizler; fragment'ı atar. Dosya yolu aynen döner."""
    s = str(source)
    if not _is_url(s):
        return s
    p = urlsplit(s)
    netloc = p.netloc
    if "@" in netloc:
        netloc = "***@" + netloc.rsplit("@", 1)[1]
    query = p.query
    if query:
        pairs = parse_qsl(query, keep_blank_values=True)
        query = urlencode([(k, "***" if k.lower() in SENSITIVE_KEYS else v) for k, v in pairs], safe="*")
    return urlunsplit((p.scheme, netloc, p.path, query, ""))


def redact_text(text) -> str:
    """Serbest metindeki (hata mesajları) URL kimlik bilgilerini ve hassas parametreleri gizler."""
    return _QUERY_SECRET.sub(r"\1***", _URL_USERINFO.sub(r"\1***@", str(text)))


# ---------------------------------------------------------------- kamera kimliği
CAMERA_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")      # dashboard ve DB kısıtıyla aynı
_EXPLICIT_ID = re.compile(r"^\[([^\]]*)\]\s*(.*)$", re.S)
CAMERA_ID_MODES = ("index", "source")


def _split_label(item: str, fallback: str) -> tuple[str, str]:
    """'Kapı=rtsp://...' -> ('Kapı', 'rtsp://...'). Etiket yoksa: dosya adı veya fallback."""
    if "=" in item:
        left, right = item.split("=", 1)
        if "://" not in left and "/" not in left and "\\" not in left and left.strip():
            return left.strip(), right.strip()
    if source_kind(item) == "file":
        stem = re.split(r"[\\/]", item)[-1].rsplit(".", 1)[0]
        return (stem or fallback), item
    return fallback, item


def _source_camera_id(source: str) -> str:
    """Kaynağın kendisinden türetilen, sıradan bağımsız kimlik. Kimlik bilgisi (kullanıcı/parola/token) kimliğe
    girmez: URL'ler maskelenmiş hâlinin özetiyle temsil edilir."""
    kind = source_kind(source)
    s = source.strip()
    if kind == "webcam":
        return f"webcam{int(s)}"[:40]
    if kind == "file":
        stem = re.split(r"[\\/]", s)[-1].rsplit(".", 1)[0]
        return (re.sub(r"[^A-Za-z0-9_-]", "_", stem).strip("_") or "file")[:40]
    import hashlib
    digest = hashlib.sha256(redact_source(s).encode("utf-8")).hexdigest()[:12]
    return f"{kind}_{digest}"


def camera_entries(items, mode: str = "index") -> tuple[list[tuple[str, str, str]], list[str]]:
    """Kaynak listesini (kimlik, etiket, kaynak) üçlülerine çevirir.

    - '[kimlik]Etiket=kaynak' veya '[kimlik]kaynak': kullanıcının açıkça verdiği SABİT kimlik (önerilen).
    - mode='source': öneksiz kaynaklar için kimlik kaynaktan türetilir (dosya adı, webcam indeksi, URL özeti);
      kaynak sırası değişse de aynı kalır.
    - mode='index' (varsayılan, eski davranış): öneksiz kaynak 'cam<sıra>' alır. Sıra değişirse aynı kimlik başka
      kaynağa geçer; bu kimlikler için uyarı döner.
    Geçersiz/tekrarlanan kimlik ProtocolError'dır (sessizce düzeltilmez). Döner: (girdiler, uyarılar)."""
    if mode not in CAMERA_ID_MODES:
        raise ProtocolError(f"CAMERA_ID_MODE '{mode}' geçersiz; izin verilenler: {', '.join(CAMERA_ID_MODES)}")
    out, warnings, seen = [], [], {}
    for n, raw in enumerate(items, start=1):
        item = str(raw).strip()
        m = _EXPLICIT_ID.match(item)
        if m:
            cid, item = m.group(1).strip(), m.group(2).strip()
            if not CAMERA_ID_RE.match(cid):
                raise ProtocolError(f"{n}. kaynak: kamera kimliği geçersiz ({cid!r}); yalnızca harf, rakam, _ ve - "
                                    "(en fazla 40 karakter)")
            label, source = _split_label(item, f"Kamera {cid}")
        elif mode == "source":
            label, source = _split_label(item, "")
            cid = _source_camera_id(source)
            label = label or f"Kamera {cid}"
        else:
            cid = f"cam{n}"
            label, source = _split_label(item, f"YouTube {n}" if source_kind(item) == "youtube" else f"Kamera {n}")
            warnings.append(f"{cid}: kimlik kaynak SIRASINDAN türetildi; kaynak listesi değişirse bu kimlik başka bir "
                            f"kaynağa geçer. Sabit kimlik için '[kimlik]...' öneki veya CAMERA_ID_MODE=source kullanın.")
        if not source:
            raise ProtocolError(f"{n}. kaynak boş")
        if cid in seen:
            raise ProtocolError(f"kamera kimliği '{cid}' birden fazla kaynakta kullanılıyor ({seen[cid]}. ve {n}. "
                                "kaynak); her kaynağa benzersiz '[kimlik]' verin")
        seen[cid] = n
        out.append((cid, label, source))
    return out, warnings


# ---------------------------------------------------------------- incident yaşam döngüsü
class IncidentTracker:
    """Bir run içindeki ihlal yaşam döngüleri: start -> (ongoing...) -> end aynı incident_id'yi taşır.

    - Her run kendi tracker'ını kullanır (run_once içinde oluşturulur); run/kamera karışamaz.
    - start her zaman YENİ uuid4 üretir; kapanan incident'ın kimliği hiçbir yerde saklanmadığından tekrar kullanılamaz.
    - Aktif incident yokken gelen ongoing/end (orphan) hiçbir incident'a bağlanmaz: kendine ait yeni bir incident_id
      alır ve olay "orphan": true ile işaretlenir.
    - PPEMonitor bir kişiyi STALE_SEC nedeniyle unuttuğunda (end üretmeden) incident sahte end üretilmeden kapatılır
      ve 'unclosed' listesine 'stale' nedeniyle yazılır. Run biterken açık kalanlar 'run_end' nedeniyle yazılır.
    - Bellek sınırlı: active yalnızca PPEMonitor'ün hâlâ izlediği kişileri tutar; unclosed listesi MAX_UNCLOSED ile sınırlı."""
    MAX_UNCLOSED = 500

    def __init__(self):
        self.active: dict[int, str] = {}
        self.unclosed: list[dict] = []
        self.unclosed_overflow = 0
        self.orphans = 0

    def assign(self, kind: str, pid: int) -> tuple[str, bool]:
        """Döner: (incident_id, orphan_mi)."""
        if kind == "start":
            old = self.active.pop(pid, None)
            if old is not None:                  # PPEMonitor bunu üretmez; yine de eski kimliği yeniden kullanma
                self._close(old, pid, "superseded", None)
            iid = self.active[pid] = new_id()
            return iid, False
        iid = self.active.get(pid)
        if iid is None:
            self.orphans += 1
            return new_id(), True
        if kind == "end":
            del self.active[pid]
        return iid, False

    def sweep(self, live_pids, t) -> None:
        """PPEMonitor'ün artık izlemediği kişilerin açık incident'larını kapatır (end ÜRETMEZ)."""
        live = set(live_pids)
        for pid in [p for p in self.active if p not in live]:
            self._close(self.active.pop(pid), pid, "stale", t)

    def close_all(self, reason: str, t) -> None:
        for pid in list(self.active):
            self._close(self.active.pop(pid), pid, reason, t)

    def _close(self, iid, pid, reason, t):
        if len(self.unclosed) < self.MAX_UNCLOSED:
            self.unclosed.append({"incident_id": iid, "pid": int(pid), "reason": reason,
                                  "t": None if t is None else round(float(t), 1)})
        else:
            self.unclosed_overflow += 1


def enrich_event(ev: dict, *, run_id: str, camera_id: str, tracker: IncidentTracker) -> dict:
    """PPEMonitor olayına kalıcı kimlikleri ekler (yeni dict döner; mevcut alanlar korunur).
    missing doğrulaması tracker'a dokunmadan önce yapılır; ProtocolError durumunda durum değişmez."""
    kind = ev.get("type")
    mh, mv = missing_flags(kind, ev.get("missing", ""))
    iid, orphan = tracker.assign(kind, int(ev["pid"]))
    out = dict(ev, event_id=new_id(), run_id=run_id, camera_id=camera_id, incident_id=iid,
               occurred_at=utc_now_iso(), missing_helmet=mh, missing_vest=mv)
    if orphan:
        out["orphan"] = True
    return out


# ---------------------------------------------------------------- HTTP sınıflandırma
TRANSIENT_STATUS = {408, 425, 429}
AUTH_STATUS = {401, 403}


def classify(status: int) -> str:
    """'ok' | 'transient' (tekrar dene) | 'permanent' (tekrar deneme)."""
    if 200 <= status < 300:
        return "ok"
    if status in TRANSIENT_STATUS or 500 <= status < 600:
        return "transient"
    if 400 <= status < 500:
        return "permanent"
    return "transient"


def parse_retry_after(value, cap: float) -> float | None:
    """Retry-After: saniye veya HTTP tarihi. [0, cap] aralığına sıkıştırılır."""
    if not value:
        return None
    try:
        sec = float(value)
    except ValueError:
        try:
            when = email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        sec = (when - datetime.now(timezone.utc)).total_seconds()
    return max(0.0, min(cap, sec))


# ---------------------------------------------------------------- gönderici (kamera başına)
class Sender(threading.Thread):
    """Kamera başına tek gönderici thread. requests.Session'ı YALNIZCA bu thread kullanır.

    Kareler: en yenisi kazanır (latest-frame-wins), eskiler atlanır.
    Olaylar ve raporlar: tek FIFO kuyruk. Öğe kuyruktan ancak 2xx yanıttan sonra çıkar. Rapor, kendisinden önce
    kuyruğa giren olaylar teslim edilmeden (veya kalıcı hatayla düşürülmeden) gönderilmez (bariyer).
    Olaylar kuyruğa girerken JSON metnine dondurulur; sonraki değişiklikler gönderilen içeriği etkilemez.

    Hata politikası:
      - 2xx: öğeler kuyruktan çıkar.
      - Bağlantı hatası/zaman aşımı, 408, 425, 429, 5xx: AYNI istek (aynı event_id'ler, aynı gövde) üstel geri
        çekilme ile tekrar denenir; Retry-After varsa ona uyulur (retry_after_max ile sınırlı).
      - Diğer 4xx (400, 404, 413, 415, 422 ...): çok olaylı batch ikiye bölünerek denenir; tek olay hâlâ
        reddediliyorsa düşürülür. 401/403 (token hatası) bölünmeden düşürülür. Her kayıp ERROR log + sayaç.
    Kuyruk sınırı (queue_max olay): doluysa önce en eski gönderimde olmayan 'ongoing' olayı atılır (yalnızca
    süren ihlalin tekrar bildirimidir); atılacak ongoing yoksa YENİ gelen olay reddedilir. Raporlar sınıra tabi
    değildir. Her atma/ret 'evicted_ongoing' / 'dropped_queue_full' sayacına yazılır ve loglanır."""

    def __init__(self, cid, name, url, token="", *, session=None, timeout=5.0, queue_max=2000, batch_max=50,
                 backoff_base=0.5, backoff_max=10.0, retry_after_max=60.0, log=None):
        super().__init__(daemon=True, name=f"sender-{cid}")
        self.cid, self.name, self.url = cid, name, url.rstrip("/")
        self.timeout, self.queue_max, self.batch_max = timeout, queue_max, batch_max
        self.backoff_base, self.backoff_max, self.retry_after_max = backoff_base, backoff_max, retry_after_max
        self.sess = session or requests.Session()
        if token:
            self.sess.headers["X-Token"] = token
        self._log = log or (lambda msg: print(msg, flush=True))
        self.cv = threading.Condition()
        self.latest = None              # (jpg, payload) — henüz gönderilmemiş en yeni kare
        self.last_frame = None          # olay taşımak için son kare
        self.queue = deque()            # ("event", json_str, type) | ("report", json_str, None)
        self.n_events = 0
        self.inflight = 0               # kuyruğun başındaki, gönderimde olan öğe sayısı (atılamaz)
        self.closing, self.deadline = False, None
        self.retry_at, self.attempts = 0.0, 0
        self.batch_limit = batch_max
        self.stats = Counter()
        self._job = None                # tekrar denenecek iş (aynı gövde)
        self._last_log = {}

    # ------------------------------------------------ üretici tarafı (run_once thread'i)
    def submit(self, jpg, payload, events=()):
        """jpg None olabilir (kodlama başarısız): olaylar yine kuyruğa girer."""
        frozen = [(json.dumps(e, ensure_ascii=False, separators=(",", ":")), e.get("type")) for e in events]
        with self.cv:
            if jpg is not None:
                self.latest = (jpg, json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
            for s, kind in frozen:
                self._enqueue_event(s, kind)
            self.cv.notify_all()

    def submit_report(self, body: dict):
        s = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
        with self.cv:
            self.queue.append(("report", s, None))
            self.cv.notify_all()

    def close(self, timeout: float) -> dict:
        """Kuyruğu en fazla `timeout` saniye boşaltmaya çalışır, sonra durur. Döner: kalan öğe sayıları."""
        with self.cv:
            self.closing, self.deadline = True, time.monotonic() + max(0.0, timeout)
            self.cv.notify_all()
        self.join(timeout + self.timeout + 1.0)
        with self.cv:
            left = {"events": self.n_events, "reports": sum(1 for q in self.queue if q[0] == "report")}
        if left["events"] or left["reports"]:
            self._log(f"[{self.cid}] UYARI: kapanışta teslim edilemeyen {left['events']} olay ve "
                      f"{left['reports']} rapor bellekte kaldı ve kayboldu.")
        return left

    STAT_KEYS = ("events_sent", "frames_sent", "reports_sent", "retries", "dropped_permanent", "dropped_queue_full",
                 "evicted_ongoing", "reports_dropped")

    def snapshot_stats(self) -> dict:
        with self.cv:
            return {**{k: self.stats[k] for k in self.STAT_KEYS}, "queued_events": self.n_events,
                    "queued_items": len(self.queue)}

    def _enqueue_event(self, s, kind):
        if self.n_events >= self.queue_max:
            idx = next((i for i in range(self.inflight, len(self.queue))
                        if self.queue[i][0] == "event" and self.queue[i][2] == "ongoing"), None)
            if idx is not None:
                del self.queue[idx]
                self.n_events -= 1
                self.stats["evicted_ongoing"] += 1
                self._rlog("evict", f"[{self.cid}] UYARI: olay kuyruğu dolu ({self.queue_max}); en eski 'ongoing' atıldı "
                                    f"(toplam {self.stats['evicted_ongoing']}).")
            else:
                self.stats["dropped_queue_full"] += 1
                self._rlog("full", f"[{self.cid}] HATA: olay kuyruğu dolu ({self.queue_max}); yeni '{kind}' olayı "
                                   f"REDDEDİLDİ (toplam {self.stats['dropped_queue_full']}).")
                return
        self.queue.append(("event", s, kind))
        self.n_events += 1

    # ------------------------------------------------ gönderici thread'i
    def run(self):
        while True:
            with self.cv:
                job = self._wait_for_job()
                if job is None:
                    return
            try:
                outcome, status, retry_after = self._send(job)
            except Exception as exc:                   # noqa: BLE001 — thread asla sessizce ölmesin
                outcome, status, retry_after = "transient", None, None
                self._rlog("bug", f"[{self.cid}] gönderici hatası: {type(exc).__name__}: {redact_text(exc)[:200]}")
            with self.cv:
                self._complete(job, outcome, status, retry_after)

    def _wait_for_job(self):
        while True:
            now = time.monotonic()
            if self.closing and (not self.queue or now >= self.deadline):
                return None
            limit = (self.deadline - now) if self.closing else None
            if now < self.retry_at:
                wait = self.retry_at - now
                self.cv.wait(wait if limit is None else min(wait, limit))
                continue
            job = self._next_job()
            if job is not None:
                return job
            self.cv.wait(limit if limit is not None else None)

    def _next_job(self):
        if self._job is not None:                       # tekrar deneme: birebir aynı istek
            return self._job
        head = self.queue[0] if self.queue else None
        if head is not None and head[0] == "report":
            self.inflight = 1
            self._job = {"kind": "report", "k": 1, "body": head[1]}
            return self._job
        k = 0
        for item in self.queue:
            if item[0] != "event" or k >= self.batch_limit:
                break
            k += 1
        if self.latest is not None:
            self.last_frame, self.latest = self.latest, None
            frame = self.last_frame
        elif k:
            frame = self.last_frame                     # olaylar son kareyle taşınır
        else:
            return None
        if frame is None:                               # henüz hiç kare yok (olay kareden önce gelmez; savunma)
            return None
        jpg, payload_json = frame
        payload = json.loads(payload_json)
        payload.update(cam=self.cid, name=self.name,
                       events=[json.loads(self.queue[i][1]) for i in range(k)])
        self.inflight = k
        job = {"kind": "frame", "k": k, "jpg": jpg, "payload": json.dumps(payload, ensure_ascii=False)}
        if k:
            self._job = job                             # olay taşıyan iş başarıya kadar aynen tekrarlanır
        return job

    def _send(self, job):
        try:
            if job["kind"] == "report":
                r = self.sess.post(f"{self.url}/ingest/report", data=job["body"].encode("utf-8"),
                                   headers={"Content-Type": "application/json"}, timeout=self.timeout)
            else:
                r = self.sess.post(f"{self.url}/ingest/frame", files={"frame": ("f.jpg", job["jpg"], "image/jpeg")},
                                   data={"payload": job["payload"]}, timeout=self.timeout)
        except requests.RequestException as exc:
            self._rlog("net", f"[{self.cid}] Dashboard'a ulaşılamıyor ({redact_source(self.url)}): "
                              f"{type(exc).__name__}")
            return "transient", None, None
        return classify(r.status_code), r.status_code, r.headers.get("Retry-After")

    def _complete(self, job, outcome, status, retry_after):
        k = job["k"]
        if outcome == "ok":
            self._pop(k)
            if job["kind"] == "report":
                self.stats["reports_sent"] += 1
            else:
                self.stats["events_sent"] += k
                self.stats["frames_sent"] += 1
            self.attempts = 0
            self.batch_limit = min(self.batch_max, self.batch_limit * 2)
            return
        if outcome == "transient":
            self.attempts += 1
            self.stats["retries"] += 1
            delay = min(self.backoff_max, self.backoff_base * 2 ** (self.attempts - 1))
            delay = random.uniform(delay / 2, delay)
            ra = parse_retry_after(retry_after, self.retry_after_max)
            if ra is not None:
                delay = ra
            self.retry_at = time.monotonic() + delay
            if status is not None:
                self._rlog("transient", f"[{self.cid}] geçici hata HTTP {status}; {delay:.1f} sn sonra aynı istek "
                                        f"tekrar denenecek (bekleyen olay: {self.n_events}).")
            if self._job is None:                       # olaysız kare: tekrar edilmez, en yeni kare kazanır
                self.inflight = 0
            return
        # kalıcı hata
        self.attempts = 0
        if job["kind"] == "report":
            self._pop(1)
            self.stats["reports_dropped"] += 1
            self._log(f"[{self.cid}] HATA: rapor kalıcı olarak reddedildi (HTTP {status}); düşürüldü.")
        elif k > 1 and status not in AUTH_STATUS:
            self._job, self.inflight = None, 0          # bölerek dene: tek hatalı olay tüm batch'i yakmasın
            self.batch_limit = max(1, k // 2)
        elif k >= 1:
            self._pop(k)
            self.stats["dropped_permanent"] += k
            hint = " (INGEST_TOKEN dashboard ile aynı mı?)" if status in AUTH_STATUS else ""
            self._log(f"[{self.cid}] HATA: {k} olay kalıcı olarak reddedildi (HTTP {status}){hint}; düşürüldü "
                      f"(toplam {self.stats['dropped_permanent']}).")
        else:
            self.inflight = 0
            self.retry_at = time.monotonic() + self.backoff_max
            self._rlog("perm_frame", f"[{self.cid}] HATA: kare reddedildi (HTTP {status}).")

    def _pop(self, k):
        for _ in range(k):
            item = self.queue.popleft()
            if item[0] == "event":
                self.n_events -= 1
        self._job, self.inflight = None, 0

    def _rlog(self, key, msg, every=5.0):
        now = time.monotonic()
        if now - self._last_log.get(key, -every) >= every:
            self._last_log[key] = now
            self._log(msg)
