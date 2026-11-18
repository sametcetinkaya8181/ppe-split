"""Bağımlılıksız küçük WebSocket istemcisi (yalnız test ölçümü): /ws'e oturum çereziyle bağlanır, kamera indeksi
başına alınan kare sayısını, kareler arası en büyük boşluğu, kopma/yeniden bağlanma sayısını ölçer. Kare içeriği
saklanmaz (yalnız bayt sayısı). Sunucu ping'lerine pong verir (uvicorn: 20 sn ping, 20 sn zaman aşımı; pong vermeyen
istemci ~40 sn'de 1011 ile kapatılır — ilk ölçümlerdeki periyodik kopmaların nedeni buydu)."""
import base64
import os
import socket
import struct
import threading
import time
from urllib.parse import urlparse


class WSProbe(threading.Thread):
    def __init__(self, url: str, cookie_fn, origin: str, reconnect_delay=1.0):
        super().__init__(daemon=True, name="wsprobe")
        self.url, self.cookie_fn, self.origin = url, cookie_fn, origin
        self.reconnect_delay = reconnect_delay
        self.lock = threading.Lock()
        self.stop_flag = False
        self.stats = {"connects": 0, "disconnects": 0, "connect_failures": 0, "frames": 0, "bytes": 0,
                      "per_cam": {}, "max_gap_sec": 0.0, "connected": False, "last_close": None}
        self._last = None

    def close_now(self):
        """İstemci tarafı kapanış (TCP): yeniden bağlanmaz."""
        self.stop_flag = True
        sock = getattr(self, "sock", None)
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()

    def snapshot_and_reset_window(self) -> dict:
        with self.lock:
            s = {k: (dict(v) if isinstance(v, dict) else v) for k, v in self.stats.items()}
            self.stats["per_cam"] = {}
            self.stats["max_gap_sec"] = 0.0
            return s

    def run(self):
        while not self.stop_flag:
            try:
                self._session()
            except OSError as exc:
                with self.lock:
                    self.stats["connect_failures"] += 1
                    self.stats["last_close"] = type(exc).__name__
            with self.lock:
                if self.stats["connected"]:
                    self.stats["disconnects"] += 1
                self.stats["connected"] = False
            time.sleep(self.reconnect_delay)

    def _session(self):
        u = urlparse(self.url)
        sock = socket.create_connection((u.hostname, u.port or 80), timeout=10)
        self.sock = sock
        try:
            key = base64.b64encode(os.urandom(16)).decode()
            req = (f"GET {u.path or '/'} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\nUpgrade: websocket\r\n"
                   f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
                   f"Origin: {self.origin}\r\nCookie: {self.cookie_fn()}\r\n\r\n")
            sock.sendall(req.encode())
            head = b""
            while b"\r\n\r\n" not in head:
                chunk = sock.recv(4096)
                if not chunk:
                    raise ConnectionError("handshake kapandı")
                head += chunk
            status = head.split(b"\r\n", 1)[0]
            if b" 101 " not in status:
                raise ConnectionError(status.decode(errors="replace"))
            buf = head.split(b"\r\n\r\n", 1)[1]
            with self.lock:
                self.stats["connects"] += 1
                self.stats["connected"] = True
            sock.settimeout(30)
            while not self.stop_flag:
                op, payload, buf = self._frame(sock, buf)
                if op == 9:                                # ping -> pong (RFC 6455; tarayıcılar otomatik yapar)
                    mask = os.urandom(4)
                    body = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
                    sock.sendall(bytes([0x8A, 0x80 | len(payload)]) + mask + body)
                    with self.lock:
                        self.stats["pongs"] = self.stats.get("pongs", 0) + 1
                    continue
                if op == 8:
                    code = struct.unpack("!H", payload[:2])[0] if len(payload) >= 2 else None
                    with self.lock:
                        self.stats["last_close"] = code
                    return
                if op == 2 and payload:
                    now = time.monotonic()
                    with self.lock:
                        self.stats["frames"] += 1
                        self.stats["bytes"] += len(payload)
                        idx = str(payload[0])
                        self.stats["per_cam"][idx] = self.stats["per_cam"].get(idx, 0) + 1
                        if self._last is not None:
                            self.stats["max_gap_sec"] = max(self.stats["max_gap_sec"], round(now - self._last, 3))
                        self._last = now
        finally:
            sock.close()

    @staticmethod
    def _read(sock, buf, n):
        while len(buf) < n:
            chunk = sock.recv(65536)
            if not chunk:
                raise ConnectionError("akış kapandı")
            buf += chunk
        return buf[:n], buf[n:]

    def _frame(self, sock, buf):
        hdr, buf = self._read(sock, buf, 2)
        op, ln = hdr[0] & 0x0F, hdr[1] & 0x7F
        if ln == 126:
            ext, buf = self._read(sock, buf, 2)
            ln = struct.unpack("!H", ext)[0]
        elif ln == 127:
            ext, buf = self._read(sock, buf, 8)
            ln = struct.unpack("!Q", ext)[0]
        payload, buf = self._read(sock, buf, ln)
        return op, payload, buf
