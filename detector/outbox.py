"""Kalıcı outbox (SQLite WAL) ve DurableSender (FAZ 7).

Teslim sözleşmesi (en az bir kez + sunucuda event_id ile tekilleştirme; "exactly-once delivery" İDDİA EDİLMEZ):
  1. Olay/rapor, outbox transaction'ı COMMIT edildiğinde "kabul edilmiş" sayılır (synchronous=FULL: commit WAL'ı fsync
     eder). Bundan önceki çökmede mesaj kaybolabilir (garanti dışı).
  2. Gönderim yalnız HTTP 2xx'ten sonra "teslim edildi" işaretlenir; dashboard 2xx'i yalnız PostgreSQL commit'inden
     sonra döner (FAZ 3). ACK kaybolursa aynı gövde tekrar gönderilir; sunucu event_id/rapor tekrarını ayıklar.
  3. Fiziksel disk arızası, işletim sistemi/disk önbelleğinin fsync'e uymaması ve dosyanın kullanıcı tarafından
     silinmesi garanti dışıdır.

Tasarım:
  - Kamera başına ayrı dosya (OUTBOX_DIR/<camera_id>.sqlite3). Dedektör kamera başına ayrı süreç çalıştırdığından
    dosyalar arasında yazma çekişmesi yoktur; bir kameranın sorunu diğerlerini bloke etmez; FIFO doğal olarak korunur.
  - locking_mode=EXCLUSIVE: dosyayı açan süreç kapanana kadar özel kilidi tutar; aynı kamera için ikinci süreç
    (ör. yanlışlıkla iki dedektör) OutboxLocked alır ve o kamerayı başlatmaz.
  - Aynı süreç içindeki üretici (run_once) ve gönderici thread tek bağlantıyı bir kilitle paylaşır.
  - Gövde kuyruğa girerken JSON metni olarak dondurulur ve SHA-256'sı saklanır; gönderilen bayt dizisi değişmez.
  - Teslim edilen satırın gövdesi hemen boşaltılır (snapshot görüntüsü diskte kalmaz); meta satır
    OUTBOX_DELIVERED_RETENTION saniye sonra silinir. Teslim edilmemiş satır HİÇBİR durumda otomatik silinmez
    (tek istisna: kapasite dolunca en eski bekleyen 'ongoing' — sayılır ve loglanır).
  - Bozuk dosya silinmez/sıfırlanmaz: OutboxCorrupt + operatör prosedürü (docs/OUTBOX.md)."""
import hashlib
import json
import os
import random
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import protocol

SCHEMA_VERSION = 1
CRITICAL_TYPES = ("start", "end")
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS outbox (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK (kind IN ('event', 'report')),
    event_type TEXT CHECK (event_type IS NULL OR event_type IN ('start', 'ongoing', 'end')),
    msg_id TEXT NOT NULL UNIQUE,
    run_id TEXT NOT NULL,
    body TEXT NOT NULL,
    bytes INTEGER NOT NULL CHECK (bytes >= 0),
    sha256 TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending' CHECK (state IN ('pending', 'delivered', 'dead', 'quarantined')),
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    last_attempt_at REAL,
    delivered_at REAL,
    last_status INTEGER,
    reason TEXT
);
CREATE INDEX IF NOT EXISTS ix_outbox_state_seq ON outbox (state, seq);
"""


class OutboxError(RuntimeError):
    """Outbox kullanılamıyor (mesajlar yol ve neden içerir; gizli bilgi içermez)."""


class OutboxLocked(OutboxError):
    pass


class OutboxCorrupt(OutboxError):
    pass


class OutboxFull(OutboxError):
    pass


class OutboxWriteError(OutboxError):
    pass


def default_dir() -> Path:
    return Path(os.getenv("OUTBOX_DIR") or Path(__file__).with_name("outbox"))


def path_for(camera_id: str, base: Path | None = None) -> Path:
    if not protocol.CAMERA_ID_RE.match(camera_id):
        raise OutboxError(f"geçersiz kamera kimliği: {camera_id!r}")
    return (base or default_dir()) / f"{camera_id}.sqlite3"


def restrict_dir(path: Path) -> bool:
    """Yeni oluşturulan outbox dizinini yalnız mevcut kullanıcı (+ SYSTEM, Administrators) erişebilir yapar.
    Windows: miras kesilir (icacls, SID ile — dil bağımsız). POSIX: 0700. Başarısızlık loglanır, engellemez."""
    try:
        if os.name != "nt":
            os.chmod(path, 0o700)
            return True
        sid = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, text=True,
                             timeout=10).stdout.strip().split(",")[-1].strip('"')
        if not sid.startswith("S-1-"):
            return False
        r = subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", f"*{sid}:(OI)(CI)F",
                            "*S-1-5-18:(OI)(CI)F", "*S-1-5-32-544:(OI)(CI)F"], capture_output=True, text=True,
                           timeout=10)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _freeze(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _digest(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


class Outbox:
    """Tek kameranın kalıcı kuyruğu. Tüm yöntemler thread-güvenlidir (tek bağlantı + kilit)."""

    def __init__(self, path: Path, camera_id: str, *, max_bytes: int = 512 * 1024 * 1024, max_items: int = 100_000,
                 high_watermark: float = 0.8, critical_reserve: float = 0.25, delivered_retention: float = 3600.0,
                 lock_timeout: float = 2.0):
        self.path, self.camera_id = Path(path), camera_id
        self.max_bytes, self.max_items = max_bytes, max_items
        self.high_watermark, self.critical_reserve = high_watermark, critical_reserve
        self.delivered_retention = delivered_retention
        self.lock = threading.RLock()
        created = not self.path.parent.exists()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if created:                                 # outbox snapshot görüntüsü içerir: yalnız sahibi erişsin
            restrict_dir(self.path.parent)
        existed = self.path.exists() and self.path.stat().st_size > 0
        try:
            self.db = sqlite3.connect(str(self.path), timeout=lock_timeout, isolation_level=None,
                                      check_same_thread=False)
        except sqlite3.DatabaseError as exc:
            raise OutboxCorrupt(f"{self.path}: açılamadı ({exc})") from None
        try:
            self.db.execute("PRAGMA locking_mode=EXCLUSIVE")
            mode = self.db.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute(f"PRAGMA busy_timeout={int(lock_timeout * 1000)}")
            self.db.execute("BEGIN EXCLUSIVE")              # özel kilit şimdi alınır ve kapanana kadar tutulur
            self.db.execute("COMMIT")
        except sqlite3.OperationalError as exc:
            self.db.close()
            if "locked" in str(exc) or "busy" in str(exc):
                raise OutboxLocked(f"{self.path}: başka bir süreç kullanıyor (aynı kamera için ikinci dedektör?)") \
                    from None
            raise OutboxCorrupt(f"{self.path}: {exc}") from None
        except sqlite3.DatabaseError as exc:
            self.db.close()
            raise OutboxCorrupt(f"{self.path}: {exc}") from None
        if mode.lower() != "wal":
            self.db.close()
            raise OutboxError(f"{self.path}: WAL modu etkinleştirilemedi ({mode})")
        try:
            if existed:
                check = self.db.execute("PRAGMA quick_check").fetchone()[0]
                if check != "ok":
                    raise OutboxCorrupt(f"{self.path}: bütünlük denetimi başarısız ({check[:200]})")
            self.db.executescript(SCHEMA)
            with self._tx():
                self.db.execute("INSERT OR IGNORE INTO meta VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
                self.db.execute("INSERT OR IGNORE INTO meta VALUES ('camera_id', ?)", (camera_id,))
            meta = dict(self.db.execute("SELECT key, value FROM meta"))
        except sqlite3.DatabaseError as exc:
            self.db.close()
            raise OutboxCorrupt(f"{self.path}: {exc}") from None
        except OutboxCorrupt:
            self.db.close()
            raise
        if meta.get("camera_id") != camera_id or meta.get("schema_version") != str(SCHEMA_VERSION):
            self.db.close()
            raise OutboxError(f"{self.path}: başka kameraya/sürüme ait (camera_id={meta.get('camera_id')!r}, "
                              f"schema={meta.get('schema_version')!r})")
        self.stats = {"delivered": 0, "evicted_ongoing": 0, "dropped_ongoing_full": 0, "write_errors": 0}

    # ------------------------------------------------ yardımcılar
    class _Tx:
        def __init__(self, db):
            self.db = db

        def __enter__(self):
            self.db.execute("BEGIN IMMEDIATE")

        def __exit__(self, et, ev, tb):
            self.db.execute("ROLLBACK" if et else "COMMIT")
            return False

    def _tx(self):
        return self._Tx(self.db)

    def _usage(self) -> tuple[int, int]:
        n, b = self.db.execute("SELECT count(*), coalesce(sum(bytes), 0) FROM outbox WHERE state = 'pending'").fetchone()
        return n, b

    def _write(self, fn):
        with self.lock:
            try:
                with self._tx():
                    return fn()
            except sqlite3.OperationalError as exc:
                self.stats["write_errors"] += 1
                raise OutboxWriteError(f"{self.path}: yazılamadı ({exc})") from None
            except sqlite3.DatabaseError as exc:
                self.stats["write_errors"] += 1
                raise OutboxCorrupt(f"{self.path}: {exc}") from None

    # ------------------------------------------------ üretici
    def put_events(self, events) -> list[str]:
        """Olayları tek transaction'da kalıcılaştırır. Döner: kabul edilmeyip bilinçli atılan ongoing event_id'leri.
        Kritik (start/end) olay sert sınırı aşarsa OutboxFull; disk hatasında OutboxWriteError (hiçbiri yazılmaz)."""
        rows = []
        for e in events:
            body = _freeze(e)
            rows.append((e.get("type"), str(e["event_id"]), str(e["run_id"]), body))

        def run():
            dropped = []
            n, b = self._usage()
            for kind, eid, run_id, body in rows:
                size = len(body.encode("utf-8"))
                if n + 1 > self.max_items or b + size > self.max_bytes:
                    if kind not in CRITICAL_TYPES:
                        victim = self.db.execute("SELECT seq, bytes FROM outbox WHERE state = 'pending' AND "
                                                 "event_type = 'ongoing' ORDER BY seq LIMIT 1").fetchone()
                        if victim is None:
                            dropped.append(eid)
                            self.stats["dropped_ongoing_full"] += 1
                            continue
                        self.db.execute("DELETE FROM outbox WHERE seq = ?", (victim[0],))
                        n, b = n - 1, b - victim[1]
                        self.stats["evicted_ongoing"] += 1
                    elif (n + 1 > self.max_items * (1 + self.critical_reserve)
                          or b + size > self.max_bytes * (1 + self.critical_reserve)):
                        raise OutboxFull(f"{self.path}: kritik olay için yedek kapasite de doldu")
                cur = self.db.execute(
                    "INSERT OR IGNORE INTO outbox (kind, event_type, msg_id, run_id, body, bytes, sha256, created_at) "
                    "VALUES ('event', ?, ?, ?, ?, ?, ?, ?)", (kind, eid, run_id, body, size, _digest(body), time.time()))
                if cur.rowcount:
                    n, b = n + 1, b + size
            return dropped
        return self._write(run)

    def put_report(self, body: dict) -> None:
        frozen = _freeze(body)
        size = len(frozen.encode("utf-8"))

        def run():
            n, b = self._usage()
            if (n + 1 > self.max_items * (1 + self.critical_reserve)
                    or b + size > self.max_bytes * (1 + self.critical_reserve)):
                raise OutboxFull(f"{self.path}: rapor için yedek kapasite de doldu")
            self.db.execute("INSERT OR IGNORE INTO outbox (kind, event_type, msg_id, run_id, body, bytes, sha256, "
                            "created_at) VALUES ('report', NULL, ?, ?, ?, ?, ?, ?)",
                            (f"report:{body['run_id']}", str(body["run_id"]), frozen, size, _digest(frozen),
                             time.time()))
        self._write(run)

    # ------------------------------------------------ gönderici
    def head(self, batch_max: int, batch_bytes: int) -> list[dict]:
        """FIFO baş: tek rapor VEYA ardışık olaylar (rapora kadar). Rapor, kendinden önceki tüm satırlar
        teslim/ölü/karantina olmadan başa gelemez (bariyer)."""
        with self.lock:
            cur = self.db.execute("SELECT seq, kind, event_type, msg_id, run_id, body, bytes, sha256, attempts "
                                  "FROM outbox WHERE state = 'pending' ORDER BY seq LIMIT ?", (max(1, batch_max),))
            out, total = [], 0
            for seq, kind, et, mid, run_id, body, size, sha, attempts in cur.fetchall():
                row = {"seq": seq, "kind": kind, "event_type": et, "msg_id": mid, "run_id": run_id, "body": body,
                       "bytes": size, "sha256": sha, "attempts": attempts}
                if kind == "report":
                    return out or [row]
                if out and total + size > batch_bytes:
                    break
                out.append(row)
                total += size
            return out

    def mark_delivered(self, seqs) -> None:
        now = time.time()

        def run():
            self.db.executemany("UPDATE outbox SET state = 'delivered', body = '', delivered_at = ?, "
                                "attempts = attempts + 1 WHERE seq = ? AND state = 'pending'", [(now, s) for s in seqs])
            self.db.execute("DELETE FROM outbox WHERE state = 'delivered' AND delivered_at < ?",
                            (now - self.delivered_retention,))
        self._write(run)
        self.stats["delivered"] += len(seqs)

    def mark_attempt(self, seqs, status) -> None:
        now = time.time()
        self._write(lambda: self.db.executemany(
            "UPDATE outbox SET attempts = attempts + 1, last_attempt_at = ?, last_status = ? WHERE seq = ?",
            [(now, status, s) for s in seqs]))

    def mark_failed(self, seq: int, state: str, reason: str, status) -> None:
        """Kalıcı ret: 'dead' (geçersiz) veya 'quarantined' (çelişki). Gövde inceleme/yeniden işleme için kalır."""
        assert state in ("dead", "quarantined")
        self._write(lambda: self.db.execute(
            "UPDATE outbox SET state = ?, reason = ?, last_status = ?, attempts = attempts + 1, last_attempt_at = ? "
            "WHERE seq = ?", (state, reason[:200], status, time.time(), seq)))

    def requeue(self, seq: int | None = None) -> int:
        """Operatör: ölü/karantinadaki satır(lar)ı yeniden bekleyen yapar (gövde ve kimlikler aynı)."""
        q = "UPDATE outbox SET state = 'pending', reason = NULL WHERE state IN ('dead', 'quarantined')"
        return self._write(lambda: self.db.execute(q + (" AND seq = ?" if seq is not None else ""),
                                                   (seq,) if seq is not None else ()).rowcount)

    def failed_for_run(self, run_id: str) -> int:
        with self.lock:
            return self.db.execute("SELECT count(*) FROM outbox WHERE run_id = ? AND state IN ('dead', 'quarantined')",
                                   (run_id,)).fetchone()[0]

    def list_failed(self, limit: int = 100) -> list[dict]:
        with self.lock:
            return [dict(zip(("seq", "kind", "event_type", "msg_id", "run_id", "state", "reason", "last_status",
                              "attempts"), r)) for r in self.db.execute(
                "SELECT seq, kind, event_type, msg_id, run_id, state, reason, last_status, attempts FROM outbox "
                "WHERE state IN ('dead', 'quarantined') ORDER BY seq LIMIT ?", (limit,))]

    def metrics(self) -> dict:
        with self.lock:
            rows = dict(self.db.execute("SELECT state, count(*) FROM outbox GROUP BY state").fetchall())
            n, b = self._usage()
            oldest = self.db.execute("SELECT min(created_at) FROM outbox WHERE state = 'pending'").fetchone()[0]
            retries = self.db.execute("SELECT coalesce(sum(max(attempts - 1, 0)), 0) FROM outbox "
                                      "WHERE state != 'delivered'").fetchone()[0]
        return {"pending_count": n, "pending_bytes": b,
                "oldest_pending_age_sec": round(time.time() - oldest, 1) if oldest else 0.0,
                "pending_retry_attempts": retries, "delivered_count": self.stats["delivered"],
                "dead_letter_count": rows.get("dead", 0), "quarantine_count": rows.get("quarantined", 0),
                "disk_write_errors": self.stats["write_errors"], "evicted_ongoing": self.stats["evicted_ongoing"],
                "dropped_ongoing_full": self.stats["dropped_ongoing_full"],
                "high_watermark": n >= self.max_items * self.high_watermark
                                  or b >= self.max_bytes * self.high_watermark}

    def checkpoint(self) -> None:
        with self.lock:
            try:
                self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.DatabaseError:
                pass

    def close(self) -> None:
        with self.lock:
            self.checkpoint()
            self.db.close()


# ---------------------------------------------------------------- gönderici
class DurableSender(threading.Thread):
    """protocol.Sender ile aynı üretici arayüzü (submit/submit_report/close), fakat olaylar ve raporlar önce
    kalıcı outbox'a yazılır, sonra POST /ingest/events ve /ingest/report ile FIFO teslim edilir.
    Kareler kalıcı DEĞİLDİR: en yenisi kazanır, /ingest/frame ile olaysız gönderilir.

    Yanıt sınıfları (olay/rapor):
      2xx                   -> teslim edildi (gövde diskten silinir)
      ağ/zaman aşımı, 408, 425, 429, 5xx -> aynı gövde, üstel geri çekilme + Retry-After
      401/403               -> DURAKLAT (düşürme yok), operatör uyarısı, auth_retry sn'de bir yeniden dene;
                               token_provider varsa token yeniden okunur
      404/405               -> uç yok (eski dashboard): duraklat, düşürme yok
      409                   -> çok öğeli batch bölünür; tek öğe 'quarantined'
      400/413/422/diğer 4xx -> çok öğeli batch bölünür; tek öğe 'dead'
    Kalıcılaştırılamayan mesaj (disk dolu/hata) bellekte bekletilir ve tekrar yazılmaya çalışılır; görünür hata."""
    STATE_OK, STATE_BACKOFF, STATE_AUTH, STATE_INCOMPATIBLE = "ok", "backoff", "paused_auth", "paused_incompatible"

    def __init__(self, cid, name, url, token, outbox: Outbox, *, session=None, timeout=5.0, batch_max=50,
                 batch_bytes=4 * 1024 * 1024, backoff_base=0.5, backoff_max=30.0, auth_retry=30.0,
                 retry_after_max=60.0, memory_max=10_000, status_path=None, status_every=5.0, token_provider=None,
                 log=None):
        super().__init__(daemon=True, name=f"durable-sender-{cid}")
        import requests
        self.cid, self.name, self.url, self.outbox = cid, name, url.rstrip("/"), outbox
        self.timeout, self.batch_max, self.batch_bytes = timeout, batch_max, batch_bytes
        self.backoff_base, self.backoff_max, self.auth_retry = backoff_base, backoff_max, auth_retry
        self.retry_after_max, self.memory_max = retry_after_max, memory_max
        self.sess = session or requests.Session()
        self._requests = requests
        self.token_provider = token_provider
        self._set_token(token)
        self._log = log or (lambda msg: print(msg, flush=True))
        self.cv = threading.Condition()
        self.latest, self.frame_retry_at = None, 0.0
        self.unpersisted = []                     # [('events', [dict]) | ('report', dict)] — disk yazılamadığında
        self.closing, self.deadline = False, None
        self.retry_at, self.attempts, self.batch_limit = 0.0, 0, batch_max
        self.state = self.STATE_OK
        self.counters = {"frames_sent": 0, "events_sent": 0, "reports_sent": 0, "retries": 0, "auth_pauses": 0,
                         "lost_unpersisted": 0, "memory_fallback_peak": 0}
        self.status_path, self.status_every, self._status_at = status_path, status_every, 0.0
        self._last_log = {}

    def _set_token(self, token):
        if token:
            self.sess.headers["X-Token"] = token
        else:
            self.sess.headers.pop("X-Token", None)

    # ------------------------------------------------ üretici (run_once thread'i)
    def submit(self, jpg, payload, events=()):
        # Kaynak etiketi olay doğduğu anda gövdeye dondurulur: replay sonradan (başka adla) yapılsa da run etiketi
        # olayın üretildiği çalışmadaki addır. Dashboard bilinmeyen alanları yok sayar (geri uyumlu).
        events = [e if "source_label" in e else {**e, "source_label": self.name} for e in events]
        if events:
            self._persist("events", events)
        with self.cv:
            if jpg is not None:
                self.latest = (jpg, _freeze(payload))
            self.cv.notify_all()

    def submit_report(self, body: dict):
        self._persist("report", body)
        with self.cv:
            self.cv.notify_all()

    def _persist(self, kind, item):
        try:
            self._persist_now(kind, item)
        except OutboxError as exc:
            with self.cv:
                if len(self.unpersisted) >= self.memory_max:
                    self.counters["lost_unpersisted"] += 1
                    self._log(f"[{self.cid}] HATA: outbox yazılamıyor VE bellek yedeği dolu; mesaj KAYBOLDU "
                              f"(toplam {self.counters['lost_unpersisted']}).")
                    return
                self.unpersisted.append((kind, item))
                self.counters["memory_fallback_peak"] = max(self.counters["memory_fallback_peak"],
                                                            len(self.unpersisted))
            self._rlog("persist", f"[{self.cid}] HATA: {protocol.redact_text(exc)} — mesaj bellekte bekletiliyor "
                                  f"({len(self.unpersisted)}); süreç çökerse KAYBOLUR. Disk alanını/izinleri "
                                  f"kontrol edin.")

    def _persist_now(self, kind, item):
        if kind == "report":
            self.outbox.put_report(item)
            return
        dropped = self.outbox.put_events(item)
        if dropped:
            self._rlog("full", f"[{self.cid}] UYARI: outbox dolu; {len(dropped)} 'ongoing' olayı kabul edilmedi "
                               f"(süren ihlalin tekrar bildirimi). start/end/rapor etkilenmez.")

    def _retry_unpersisted(self):
        with self.cv:
            pending, self.unpersisted = self.unpersisted, []
        for i, (kind, item) in enumerate(pending):
            try:
                self._persist_now(kind, item)
            except OutboxError:
                with self.cv:
                    self.unpersisted = pending[i:] + self.unpersisted
                return False
        if pending:
            self._log(f"[{self.cid}] outbox yeniden yazılabilir: bellekteki {len(pending)} mesaj kalıcılaştırıldı.")
        return True

    def close(self, timeout: float) -> dict:
        with self.cv:
            self.closing, self.deadline = True, time.monotonic() + max(0.0, timeout)
            self.cv.notify_all()
        self.join(timeout + self.timeout + 2.0)
        self._write_status(force=True)
        self._final = self.snapshot_stats()          # kapanıştan sonra snapshot_stats() kapalı DB'ye dokunmaz
        m = self._final
        if m["pending_count"] or self.unpersisted:
            self._log(f"[{self.cid}] kapanış: {m['pending_count']} mesaj outbox'ta (diskte) bekliyor; bir sonraki "
                      f"başlatmada aynı kimliklerle gönderilecek." + (
                          f" BELLEKTEKİ {len(self.unpersisted)} mesaj diske yazılamadığı için KAYBOLDU."
                          if self.unpersisted else ""))
        self.outbox.close()
        return {"pending": m["pending_count"], "unpersisted": len(self.unpersisted)}

    # ------------------------------------------------ gönderici thread'i
    def run(self):
        while True:
            if self.unpersisted:
                self._retry_unpersisted()
            job = None
            with self.cv:
                now = time.monotonic()
                if self.closing and now >= self.deadline:
                    return
            rows = self.outbox.head(self.batch_limit, self.batch_bytes) if now >= self.retry_at else []
            if self.closing and not rows and now >= self.retry_at and not self.unpersisted:
                return
            if rows:
                job = ("outbox", rows)
            else:
                with self.cv:
                    if self.latest is not None and now >= self.frame_retry_at and not self.closing:
                        job, self.latest = ("frame", self.latest), None
            if job is None:
                self._write_status()
                with self.cv:
                    wake = [x for x in (self.retry_at, self.frame_retry_at) if x > now]
                    wait = min(wake) - now if wake else 1.0
                    if self.closing:
                        wait = min(wait, max(0.0, self.deadline - now))
                    self.cv.wait(min(wait, 1.0))
                continue
            try:
                if job[0] == "outbox":
                    self._deliver(job[1])
                else:
                    self._send_frame(job[1])
            except Exception as exc:                        # noqa: BLE001 — thread sessizce ölmesin
                self._rlog("bug", f"[{self.cid}] gönderici hatası: {type(exc).__name__}: "
                                  f"{protocol.redact_text(exc)[:200]}")
                self.retry_at = time.monotonic() + self.backoff_max
            self._write_status()

    def _post(self, path, **kw):
        try:
            r = self.sess.post(f"{self.url}{path}", timeout=self.timeout, **kw)
        except self._requests.RequestException as exc:
            self._rlog("net", f"[{self.cid}] Dashboard'a ulaşılamıyor ({protocol.redact_source(self.url)}): "
                              f"{type(exc).__name__}")
            return None, None
        return r.status_code, r.headers.get("Retry-After")

    def _send_frame(self, frame):
        jpg, payload_json = frame
        payload = json.loads(payload_json)
        payload.update(cam=self.cid, name=self.name, events=[])
        status, _ = self._post("/ingest/frame", files={"frame": ("f.jpg", jpg, "image/jpeg")},
                               data={"payload": json.dumps(payload, ensure_ascii=False)})
        if status is not None and 200 <= status < 300:
            self.counters["frames_sent"] += 1
        elif status in protocol.AUTH_STATUS:
            self._pause_auth(status)
        elif status is None or protocol.classify(status) == "transient":
            self.frame_retry_at = time.monotonic() + min(self.backoff_max, 1.0)
        else:
            self.frame_retry_at = time.monotonic() + self.backoff_max
            self._rlog("frame", f"[{self.cid}] HATA: kare reddedildi (HTTP {status}).")

    def _deliver(self, rows):
        for r in rows:                                     # disk bozulmasına karşı gövde bütünlüğü
            if _digest(r["body"]) != r["sha256"]:
                self.outbox.mark_failed(r["seq"], "dead", "checksum_mismatch", None)
                self._log(f"[{self.cid}] HATA: outbox satırı {r['seq']} bozuk (SHA-256 uyuşmuyor); 'dead' yapıldı.")
                return
        if rows[0]["kind"] == "report":
            failed = self.outbox.failed_for_run(rows[0]["run_id"])
            if failed:
                self._rlog(f"integrity:{rows[0]['run_id']}", f"[{self.cid}] UYARI: run {rows[0]['run_id']} raporu, "
                                                             f"{failed} olayı ölü/karantinada iken gönderiliyor "
                                                             f"(veri bütünlüğü eksik; outbox list-failed).")
            status, ra = self._post("/ingest/report", data=rows[0]["body"].encode("utf-8"),
                                    headers={"Content-Type": "application/json"})
        else:
            body = ('{"cam":' + json.dumps(self.cid) + ',"name":' + json.dumps(self.name, ensure_ascii=False)
                    + ',"events":[' + ",".join(r["body"] for r in rows) + "]}")
            status, ra = self._post("/ingest/events", data=body.encode("utf-8"),
                                    headers={"Content-Type": "application/json"})
        seqs = [r["seq"] for r in rows]
        if status is not None and 200 <= status < 300:
            self.outbox.mark_delivered(seqs)
            key = "reports_sent" if rows[0]["kind"] == "report" else "events_sent"
            self.counters[key] += len(rows)
            if self.state != self.STATE_OK:
                self._log(f"[{self.cid}] teslim yeniden başladı (önceki durum: {self.state}).")
            self.state, self.attempts = self.STATE_OK, 0
            self.batch_limit = min(self.batch_max, self.batch_limit * 2)
            return
        self.outbox.mark_attempt(seqs, status)
        if status in protocol.AUTH_STATUS:
            return self._pause_auth(status)
        if status in (404, 405):
            self.state = self.STATE_INCOMPATIBLE
            self.retry_at = time.monotonic() + self.auth_retry
            return self._rlog("compat", f"[{self.cid}] HATA: dashboard /ingest/events ucunu tanımıyor (HTTP {status}); "
                                        f"olaylar DÜŞÜRÜLMEDİ, outbox'ta bekliyor. Dashboard'u güncelleyin.", 60)
        if status is None or protocol.classify(status) == "transient":
            self.state = self.STATE_BACKOFF
            self.attempts += 1
            self.counters["retries"] += 1
            delay = min(self.backoff_max, self.backoff_base * 2 ** (self.attempts - 1))
            delay = random.uniform(delay / 2, delay)
            parsed = protocol.parse_retry_after(ra, self.retry_after_max)
            self.retry_at = time.monotonic() + (parsed if parsed is not None else delay)
            if status is not None:
                self._rlog("transient", f"[{self.cid}] geçici hata HTTP {status}; aynı gövde {self.retry_at - time.monotonic():.1f} sn sonra.")
            return
        if len(rows) > 1:                                  # kalıcı 4xx: tek hatalı öğeyi bulmak için böl
            self.batch_limit = max(1, len(rows) // 2)
            return
        state = "quarantined" if status == 409 else "dead"
        self.outbox.mark_failed(seqs[0], state, f"HTTP {status}", status)
        self._log(f"[{self.cid}] HATA: {rows[0]['kind']} {rows[0]['msg_id']} kalıcı olarak reddedildi (HTTP {status}); "
                  f"'{state}' durumuna alındı (silinmedi; outbox requeue ile yeniden işlenebilir).")

    def _pause_auth(self, status):
        if self.state != self.STATE_AUTH:
            self.counters["auth_pauses"] += 1
        self.state = self.STATE_AUTH
        self.retry_at = self.frame_retry_at = time.monotonic() + self.auth_retry
        if self.token_provider is not None:
            self._set_token(self.token_provider())
        self._rlog("auth", f"[{self.cid}] HATA: dashboard kimlik doğrulamayı reddetti (HTTP {status}). Gönderim "
                           f"DURAKLATILDI; olaylar outbox'ta korunuyor. detector/.env ve kök .env INGEST_TOKEN aynı "
                           f"olmalı (detector/.env her {self.auth_retry:.0f} sn'de yeniden okunur).", 60)

    # ------------------------------------------------ durum
    def snapshot_stats(self) -> dict:
        if getattr(self, "_final", None) is not None:
            return self._final
        return {**self.outbox.metrics(), **self.counters, "state": self.state,
                "memory_fallback": len(self.unpersisted)}

    def _write_status(self, force=False):
        if self.status_path is None:
            return
        now = time.monotonic()
        if not force and now - self._status_at < self.status_every:
            return
        self._status_at = now
        try:
            data = {"camera_id": self.cid, "updated_at": protocol.utc_now_iso(), **self.snapshot_stats()}
            tmp = Path(str(self.status_path) + ".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, self.status_path)
        except (OSError, OutboxError, sqlite3.Error):
            pass

    def _rlog(self, key, msg, every=5.0):
        now = time.monotonic()
        if now - self._last_log.get(key, -every) >= every:
            self._last_log[key] = now
            self._log(msg)


# ---------------------------------------------------------------- operatör komutları
def _cli(argv) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="python outbox.py", description="Dedektör kalıcı outbox yönetimi")
    ap.add_argument("--dir", default=None, help="outbox klasörü (varsayılan OUTBOX_DIR veya detector/outbox)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="tüm kameraların durumu (çalışan dedektörün status.json'u dahil)")
    for name in ("check", "list-failed"):
        sub.add_parser(name).add_argument("camera")
    rq = sub.add_parser("requeue")
    rq.add_argument("camera")
    rq.add_argument("--seq", type=int)
    rq.add_argument("--all", action="store_true")
    dr = sub.add_parser("drain", help="video işlemeden bekleyen mesajları gönder (kamera kapalıyken)")
    dr.add_argument("camera")
    dr.add_argument("--timeout", type=float, default=60.0)
    args = ap.parse_args(argv)
    base = Path(args.dir) if args.dir else default_dir()
    if args.cmd == "status":
        for f in sorted(base.glob("*.sqlite3")):
            cid = f.stem
            try:
                ob = Outbox(f, cid, lock_timeout=0.2)
                print(cid, json.dumps(ob.metrics(), ensure_ascii=False))
                ob.close()
            except OutboxLocked:
                st = base / f"{cid}.status.json"
                print(cid, "(çalışan dedektör kullanıyor)", st.read_text(encoding="utf-8") if st.exists() else "")
            except OutboxError as exc:
                print(cid, "HATA:", exc)
        return 0
    try:
        ob = Outbox(path_for(args.camera, base), args.camera, lock_timeout=0.5)
    except OutboxError as exc:
        print("HATA:", exc, file=sys.stderr)
        return 2
    try:
        if args.cmd == "check":
            print("ok", json.dumps(ob.metrics(), ensure_ascii=False))
        elif args.cmd == "list-failed":
            for r in ob.list_failed():
                print(json.dumps(r, ensure_ascii=False))
        elif args.cmd == "requeue":
            if args.seq is None and not args.all:
                print("--seq N veya --all gerekli", file=sys.stderr)
                return 2
            print("yeniden kuyruğa alındı:", ob.requeue(None if args.all else args.seq))
        elif args.cmd == "drain":
            from detector_env import env_token, dashboard_url
            s = DurableSender(args.camera, args.camera, dashboard_url(), env_token(), ob, token_provider=env_token)
            s.start()
            res = s.close(args.timeout)
            print("bekleyen:", res["pending"])
            return 0 if res["pending"] == 0 else 1
        return 0
    finally:
        if args.cmd != "drain":
            ob.close()


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
