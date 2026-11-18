"""Dedektör testleri: YOLO/GPU gerekmez. detector.py içe aktarılmadan önce test ortamı sabitlenir.

FAZ 01 ortam izolasyonu (FAIL-CLOSED):
  1. Test süreci üretim gizli değeri DEVRALMIŞSA (kabuktan dışa aktarılmış INGEST_TOKEN, parola vb.) oturum başlamaz.
  2. DETECTOR_ENV_FILE=none: detector._load_env() ve detector_env (token/URL yenileme) detector/.env'i HİÇ okumaz;
     üretim token'ı, kamera/YouTube adresleri ve ileride eklenecek anahtarlar test sürecine girmez.
     Belirli bir dosyayı sınayan testler DETECTOR_ENV_FILE'ı kendi geçici dosyalarına yönlendirir."""
import os
import threading
import time

import pytest

from evaluation.isolation import leaked_keys, production_secrets

_inherited = leaked_keys(dict(os.environ), production_secrets())
if _inherited:
    pytest.exit("Güvenlik (FAIL-CLOSED): test süreci üretim gizli değeri devralmış — temiz bir kabukta çalıştırın "
                f"({', '.join(_inherited)}).", returncode=2)

os.environ["DETECTOR_ENV_FILE"] = "none"
os.environ["VID_STRIDE"] = "1"
os.environ["DASHBOARD_URL"] = "http://dashboard.test"
os.environ["INGEST_TOKEN"] = ""


def wait_until(cond, timeout=5.0, step=0.01):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(step)
    return cond()


class FakeResp:
    def __init__(self, status, headers=None):
        self.status_code, self.headers = status, headers or {}


class FakeSession:
    """requests.Session yerine. script: sırayla yanıtlar (int durum | (durum, başlıklar) | istisna | callable(çağrı))."""
    def __init__(self, script=(), default=200):
        self.headers, self.calls = {}, []
        self.script, self.default = list(script), default
        self.lock = threading.Lock()

    def post(self, url, files=None, data=None, headers=None, timeout=None):
        import json
        call = {"thread": threading.get_ident(), "url": url, "t": time.monotonic()}
        if files is not None:
            call.update(kind="frame", jpg=files["frame"][1], raw=data["payload"], payload=json.loads(data["payload"]))
        else:
            call.update(kind="report", raw=data, body=json.loads(data))
        with self.lock:
            self.calls.append(call)
            action = self.script.pop(0) if self.script else self.default
        if callable(action) and not isinstance(action, type):
            action = action(call)
        if isinstance(action, BaseException):
            raise action
        if isinstance(action, tuple):
            return FakeResp(*action)
        return FakeResp(action)

    def frames(self):
        return [c for c in self.calls if c["kind"] == "frame"]

    def reports(self):
        return [c for c in self.calls if c["kind"] == "report"]
