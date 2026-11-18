"""İhlal snapshot'ları: katı base64 çözme, JPEG yapı kontrolü ve idempotent dosya yazımı.

Dosya adı yalnızca doğrulanmış event_id'den üretilir: alert_<event_id>.jpg (payload'daki yol/ad yok sayılır).
Yazım: geçici dosya + fsync + os.replace; dosya zaten varsa üzerine YAZILMAZ: içerik aynıysa yeniden kullanılır,
farklıysa SnapshotConflict. Aynı event_id için eşzamanlı yazımlar süreç içi kilitle sıralanır (dashboard tek
uvicorn süreci olarak çalışır; çok süreçli dağıtımda bu kilit yetmez).

Dosya DB commit'inden ÖNCE yazılır: commit başarısız olursa dosya sahipsiz kalabilir (DB kaydı yoktur). Aynı
olayın retry'ı aynı dosyayı yeniden kullanır. Bu fazda otomatik temizlik yoktur; mevcut dosyalar silinmez."""
import base64
import binascii
import os
import threading
import uuid
from pathlib import Path

_SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
_LOCKS = [threading.Lock() for _ in range(64)]


class SnapshotInvalid(ValueError):
    """Bozuk base64, JPEG olmayan veya çok büyük snapshot (422)."""


class SnapshotConflict(Exception):
    """Aynı event_id için diskte farklı içerikli snapshot var (409)."""


def decode(b64: str, max_bytes: int) -> bytes:
    if not isinstance(b64, str) or not b64:
        raise SnapshotInvalid("snapshot_b64 boş")
    if len(b64) > (max_bytes + 2) // 3 * 4 + 4:
        raise SnapshotInvalid(f"snapshot {max_bytes} bayt sınırını aşıyor")
    try:
        data = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError):
        raise SnapshotInvalid("snapshot_b64 geçerli base64 değil") from None
    if len(data) > max_bytes:
        raise SnapshotInvalid(f"snapshot {max_bytes} bayt sınırını aşıyor")
    if not is_jpeg(data):
        raise SnapshotInvalid("snapshot geçerli bir JPEG değil")
    return data


def is_jpeg(b: bytes) -> bool:
    """SOI ... (SOFn: genişlik/yükseklik > 0) ... SOS ... EOI yapısını denetler."""
    if len(b) < 20 or b[:2] != b"\xff\xd8" or b[-2:] != b"\xff\xd9":
        return False
    i, sof = 2, False
    while i + 4 <= len(b):
        if b[i] != 0xFF:
            return False
        m = b[i + 1]
        if m == 0xFF:                                  # dolgu baytı
            i += 1
            continue
        if m in (0xD8, 0xD9):
            return False
        if 0xD0 <= m <= 0xD7 or m == 0x01:
            i += 2
            continue
        seg = int.from_bytes(b[i + 2:i + 4], "big")
        if seg < 2 or i + 2 + seg > len(b):
            return False
        if m in _SOF:
            if seg < 8:
                return False
            h, w = int.from_bytes(b[i + 5:i + 7], "big"), int.from_bytes(b[i + 7:i + 9], "big")
            if h == 0 or w == 0:
                return False
            sof = True
        if m == 0xDA:
            return sof
        i += 2 + seg
    return False


def file_name(event_id: uuid.UUID) -> str:
    return f"alert_{uuid.UUID(str(event_id))}.jpg"


def store(snap_dir: Path, event_id: uuid.UUID, data: bytes) -> str:
    """Döner: DB'deki göreli yol 'snapshots/alert_<event_id>.jpg'."""
    name = file_name(event_id)
    final = snap_dir / name
    with _LOCKS[hash(name) % len(_LOCKS)]:
        if final.exists():
            if final.read_bytes() != data:
                raise SnapshotConflict(f"{name} zaten farklı içerikle mevcut")
            return f"snapshots/{name}"
        tmp = snap_dir / f".tmp-{uuid.uuid4().hex}.jpg"
        try:
            with open(tmp, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, final)
        finally:
            if tmp.exists():
                tmp.unlink()
    return f"snapshots/{name}"
