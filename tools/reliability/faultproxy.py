"""HTTP arıza enjeksiyon vekili (yalnız 127.0.0.1, yalnız test). Dedektör/sentetik kamera DASHBOARD_URL olarak bu
vekile bağlanır; vekil istekleri izole dashboard'a iletir. Modlar (çalışırken değiştirilebilir):
  pass          : olduğu gibi ilet
  status:<kod>  : iletmeden <kod> döndür (503, 401, 403, 500 ...)
  hang          : isteği oku, yanıt verme (istemci zaman aşımı), iletme
  ack_loss      : İLET (DB commit olur), sonra yanıt vermeden bağlantıyı kopar -> istemci aynı gövdeyi tekrar yollar
  refuse        : bağlantıyı hemen kapat
Gövde içeriği kaydedilmez; yalnız yol/mod/durum sayılır."""
import socket
import threading
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests


class FaultProxy:
    def __init__(self, upstream: str, port: int = 0):
        self.upstream = upstream.rstrip("/")
        self.mode = "pass"
        self.counts = Counter()
        self.lock = threading.Lock()
        self.sess = requests.Session()
        proxy = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _handle(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n else b""
                mode = proxy.mode
                with proxy.lock:
                    proxy.counts[(self.path.split("?")[0], mode)] += 1
                if mode == "refuse":
                    self.close_connection = True
                    self.connection.shutdown(socket.SHUT_RDWR)
                    return
                if mode == "hang":
                    time.sleep(30)
                    self.close_connection = True
                    return
                if mode.startswith("status:"):
                    code = int(mode.split(":")[1])
                    data = b'{"detail":"fault-injected"}'
                    self.send_response(code)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                headers = {k: v for k, v in self.headers.items()
                           if k.lower() in ("content-type", "x-token", "cookie", "origin", "x-csrf-token")}
                try:
                    r = proxy.sess.request(self.command, proxy.upstream + self.path, data=body, headers=headers,
                                           timeout=20)
                except requests.RequestException:
                    self.send_response(502)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                with proxy.lock:
                    proxy.counts[("upstream_status", r.status_code)] += 1
                if mode == "ack_loss":                     # sunucu işledi; istemci yanıtı ALMAZ
                    self.close_connection = True
                    self.connection.shutdown(socket.SHUT_RDWR)
                    return
                self.send_response(r.status_code)
                for k, v in r.headers.items():
                    if k.lower() in ("content-type", "retry-after"):
                        self.send_header(k, v)
                self.send_header("Content-Length", str(len(r.content)))
                self.end_headers()
                self.wfile.write(r.content)

            do_GET = do_POST = _handle

        self.server = ThreadingHTTPServer(("127.0.0.1", port), H)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self):
        self.thread.start()
        return self

    def set(self, mode: str):
        self.mode = mode

    def stop(self):
        self.server.shutdown()
