"""detector/.env okuma yardımcıları (torch/cv2 içermez; outbox komutları ve token yenileme için)."""
import os
from pathlib import Path

ENV_FILE = Path(__file__).with_name(".env")
DISABLED = "none"


def env_file() -> Path | None:
    """Okunacak ayar dosyası. TEK kaynak: hem detector._load_env() hem token/URL yenileme bunu kullanır (FAZ 01).
      DETECTOR_ENV_FILE yok/boş -> detector/.env (üretim varsayılanı; davranış değişmedi)
      DETECTOR_ENV_FILE=<yol>   -> o dosya (yoksa hiçbir değer okunmaz)
      DETECTOR_ENV_FILE=none    -> hiçbir dosya okunmaz (testler/değerlendirme: üretim token'ı ve kamera adresleri
                                   sürece girmez). Yalnız açıkça verilirse etkilidir; sessiz bir atlatma yoktur."""
    v = (os.getenv("DETECTOR_ENV_FILE") or "").strip()
    if not v:
        return ENV_FILE
    return None if v.lower() == DISABLED else Path(v)


def read_env_file(path: Path | None = None) -> dict:
    path = path or env_file()
    out = {}
    if path is not None and path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                v = v.strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                    v = v[1:-1]
                out[k.strip()] = v
    return out


def env_token() -> str:
    """Güncel INGEST_TOKEN: detector/.env dosyası her çağrıda yeniden okunur (operatör token'ı düzeltince dedektörü
    yeniden başlatmadan devam edebilsin); dosyada yoksa süreç ortamı."""
    values = read_env_file()
    return values["INGEST_TOKEN"] if "INGEST_TOKEN" in values else os.getenv("INGEST_TOKEN", "")


def dashboard_url() -> str:
    return (os.getenv("DASHBOARD_URL") or read_env_file().get("DASHBOARD_URL") or "http://localhost:8000").rstrip("/")
