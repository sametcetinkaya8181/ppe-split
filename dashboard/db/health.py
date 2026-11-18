"""/health/ready kontrolü: SELECT 1 + DB'deki Alembic revision'ı beklenen head ile karşılaştırır.
Yanıtta bağlantı adresi, kullanıcı veya parola yer almaz."""
import logging
from functools import lru_cache
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .session import get_engine

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"
log = logging.getLogger("ppe.health")


@lru_cache(maxsize=1)
def expected_heads() -> frozenset[str]:
    return frozenset(ScriptDirectory.from_config(Config(str(ALEMBIC_INI))).get_heads())


# Sabit metinli sorgular SQL dizesine göre TEK derlenmiş ifade olarak önbelleklenir. MigrationContext.configure()
# her çağrıda yeni bir alembic_version Table/MetaData nesnesi kurar; SQLAlchemy önbellek anahtarı o nesneye bağlı
# olduğundan her hazırlık kontrolü yeni bir önbellek girdisi ekliyor ve nesneleri canlı tutuyordu (LRU 500 girdiye
# kadar sürekli bellek artışı; FAZ 10-A.1 bellek probu). Anlam aynı: tablo yoksa revision yok.
_VERSION_TABLE_EXISTS = text("SELECT to_regclass('alembic_version') IS NOT NULL")
_VERSIONS = text("SELECT version_num FROM alembic_version")


def _current_heads(conn) -> list[str]:
    if not conn.execute(_VERSION_TABLE_EXISTS).scalar():
        return []
    return [row[0] for row in conn.execute(_VERSIONS)]


def check_ready() -> tuple[bool, dict]:
    expected = sorted(expected_heads())
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
            current = sorted(_current_heads(conn))
    except RuntimeError:                          # DATABASE_URL eksik/yanlış (mesaj parola içermez)
        log.error("readiness: veritabanı yapılandırması eksik veya hatalı")
        return False, {"status": "not_ready", "db": "misconfigured"}
    except SQLAlchemyError as exc:
        log.warning("readiness: veritabanına ulaşılamadı (%s)", type(exc).__name__)
        return False, {"status": "not_ready", "db": "unreachable"}
    if current != expected:
        return False, {"status": "not_ready", "db": "ok", "schema": "outdated",
                       "current": current, "expected": expected}
    return True, {"status": "ready", "db": "ok", "schema": "current", "revision": current}
