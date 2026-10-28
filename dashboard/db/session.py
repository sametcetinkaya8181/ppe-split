"""Engine/Session. Bağlantı import sırasında değil, ilk kullanımda kurulur."""
import os
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

URL_PREFIX = "postgresql+psycopg://"


def database_url() -> str:
    """DATABASE_URL'yi döndürür; eksik/yanlışsa parolayı göstermeden anlaşılır hata verir."""
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL tanımlı değil. Proje kökündeki .env.example dosyasını .env olarak kopyalayıp doldurun.")
    if not url.startswith(URL_PREFIX):
        raise RuntimeError(f"DATABASE_URL '{URL_PREFIX}' ile başlamalı (psycopg 3 sürücüsü).")
    return url


def get_engine(url: str | None = None) -> Engine:
    """Adres her çağrıda çözülür; önbellek anahtarı çözülmüş adrestir (None değil)."""
    return _engine_for(url or database_url())


@lru_cache(maxsize=4)
def _engine_for(url: str) -> Engine:
    return create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5, connect_args={"connect_timeout": 3})


def get_session(url: str | None = None) -> Session:
    return sessionmaker(bind=get_engine(url or database_url()), expire_on_commit=False)()
