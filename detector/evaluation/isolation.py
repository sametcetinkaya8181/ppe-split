"""FAZ 10-A.5B gizli bilgi izolasyonu (yalnız değerlendirme/test süreçleri; üretim detector.py DEĞİŞMEZ).

Sorun (FAZ 10-A.4/10-A.5A §1.1): production_settings() -> `import detector` -> detector/.env süreç ortamına yüklenir;
değerlendirme süreci üretim INGEST_TOKEN'ını taşır ve ortamı devralan her alt süreç onu görebilir.

İki katman:
  1. scrub_process_env(): üretim ayarları okunduktan hemen sonra gizli anahtarlar süreç ortamından silinir ve
     detector modülündeki kopya boşaltılır. Değerlendirme hiçbir zaman üretim token'ına ihtiyaç duymaz.
  2. assert_env_isolated(): test alt süreci (compose, dedektör) başlatılmadan önce ortamdaki HER değer üretim gizli
     değerleriyle karşılaştırılır; eşleşme varsa süreç başlatılmaz. Hata iletisi yalnız anahtar adlarını içerir.
Hiçbir fonksiyon gizli değer döndürmez veya yazdırmaz."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROD_ENV_FILES = (ROOT / ".env", ROOT / "detector" / ".env")
# değerlendirme sürecinde bulunmaması gereken anahtarlar (DASHBOARD_URL: test araçları kendi adresini açıkça verir)
SECRET_KEYS = ("INGEST_TOKEN", "INGEST_TOKEN_NEXT", "POSTGRES_PASSWORD", "PPE_APP_PASSWORD", "PPE_MIGRATOR_PASSWORD",
               "ADMIN_PASSWORD", "DASHBOARD_URL")
PROD_SECRET_KEYS = ("INGEST_TOKEN", "INGEST_TOKEN_NEXT", "POSTGRES_PASSWORD", "PPE_APP_PASSWORD",
                    "PPE_MIGRATOR_PASSWORD")
MIN_LEN = 12                    # kısa değerler karşılaştırılmaz (yanlış eşleşme)


class IsolationError(RuntimeError):
    pass


def _read(path: Path) -> dict:
    out = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                v = v.strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                    v = v[1:-1]
                out[k.strip()] = v
    return out


def production_secrets(files=PROD_ENV_FILES) -> dict:
    """{etiket: değer} — yalnız karşılaştırma için bellekte tutulur, hiçbir yere yazılmaz."""
    out = {}
    for p in files:
        for k, v in _read(Path(p)).items():
            if k in PROD_SECRET_KEYS and len(v) >= MIN_LEN:
                out[f"{Path(p).parent.name}/{Path(p).name}:{k}"] = v
    return out


def scrub_process_env(environ=None, module=None) -> list:
    """Gizli anahtarları süreç ortamından siler; detector modülü verildiyse INGEST_TOKEN kopyasını boşaltır.
    Döner: silinen anahtar ADLARI."""
    environ = os.environ if environ is None else environ
    removed = [k for k in SECRET_KEYS if k in environ]
    for k in removed:
        del environ[k]
    if module is not None and getattr(module, "INGEST_TOKEN", ""):
        module.INGEST_TOKEN = ""
        removed.append("detector.INGEST_TOKEN")
    return removed


def leaked_keys(env: dict, secrets: dict) -> list:
    """Ortamda değeri bir üretim gizli değerine eşit (veya onu içeren) anahtarlar: ["ANAHTAR==etiket", ...]."""
    vals = list(secrets.items())
    return sorted(f"{k}=={lbl}" for k, v in env.items() if v for lbl, s in vals if s in str(v))


def assert_env_isolated(env: dict, secrets: dict | None = None, *, context: str = "") -> None:
    hits = leaked_keys(env, production_secrets() if secrets is None else secrets)
    if hits:
        raise IsolationError(f"{context or 'alt süreç'}: üretim gizli değeri test ortamında — başlatılmadı "
                             f"({', '.join(hits)})")
