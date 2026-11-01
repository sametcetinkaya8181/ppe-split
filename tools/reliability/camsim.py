"""Ağ kamerası benzeticisi (yalnız test, yalnız 127.0.0.1): TCP üzerinden MPEG-TS. Her bağlanan istemciye ayrı bir
ffmpeg (gerçek zaman hızında, döngüde) akıtılır; dedektörün kısa yoklama bağlantısı + asıl bağlantı birlikte çalışır.
  cut()     : dinleyici ve tüm bağlantılar kapanır (kamera/ağ koptu; yeni bağlantı reddedilir)
  restore() : yeniden dinlemeye başlar (kamera geri geldi)
  freeze()  : bağlantılar açık kalır ama veri akmaz (donmuş kamera / takılmış ağ)
  unfreeze(): veri akışı sürer
RTSP sunucusu kurulu olmadığından RTSP yerine bu benzetim kullanılır (OpenCV/FFmpeg okuma yolu aynı aileden)."""
import socket
import subprocess
import threading
import time
from pathlib import Path


class CameraSim:
    def __init__(self, video: Path, port: int):
        self.video, self.port = video, port
        self.lsock = None
        self.clients = []                   # (sock, proc)
        self.lock = threading.Lock()
        self.frozen = threading.Event()
        self.accepts = 0

    @property
    def url(self) -> str:
        return f"tcp://127.0.0.1:{self.port}"

    def restore(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", self.port))
        s.listen(8)
        self.lsock = s
        threading.Thread(target=self._accept_loop, args=(s,), daemon=True).start()
        return self

    start = restore

    def _accept_loop(self, s):
        while True:
            try:
                conn, _ = s.accept()
            except OSError:
                return
            self.accepts += 1
            p = subprocess.Popen(["ffmpeg", "-hide_banner", "-loglevel", "error", "-re", "-stream_loop", "-1", "-i",
                                  str(self.video), "-an", "-c:v", "copy", "-f", "mpegts", "pipe:1"],
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            with self.lock:
                self.clients.append((conn, p))
            threading.Thread(target=self._pump, args=(conn, p), daemon=True).start()

    def _pump(self, conn, p):
        try:
            while True:
                if self.frozen.is_set():
                    time.sleep(0.05)
                    continue
                chunk = p.stdout.read(188 * 64)
                if not chunk:
                    break
                conn.sendall(chunk)
        except OSError:
            pass
        finally:
            self._drop(conn, p)

    def _drop(self, conn, p):
        try:
            conn.close()
        except OSError:
            pass
        if p.poll() is None:
            p.kill()
        with self.lock:
            self.clients = [x for x in self.clients if x[0] is not conn]

    def cut(self):
        if self.lsock is not None:
            self.lsock.close()
            self.lsock = None
        with self.lock:
            cl = list(self.clients)
        for conn, p in cl:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self._drop(conn, p)

    def freeze(self):
        self.frozen.set()

    def unfreeze(self):
        self.frozen.clear()

    stop = cut
