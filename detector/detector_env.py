"""detector/.env okuma yardımcıları (torch/cv2 içermez; outbox komutları ve token yenileme için)."""
import os
from pathlib import Path

ENV_FILE = Path(__file__).with_name(".env")


def read_env_file(path: Path | None = None) -> dict:
    path = path or Path(os.getenv("DETECTOR_ENV_FILE") or ENV_FILE)    # testler gerçek .env'i okumasın
    out = {}
    if path.exists():
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
