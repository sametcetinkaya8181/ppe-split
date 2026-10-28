"""/health/ready kontrolü: SELECT 1 + DB'deki Alembic revision'ı beklenen head ile karşılaştırır.
Yanıtta bağlantı adresi, kullanıcı veya parola yer almaz."""
import logging
from functools import lru_cache
from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .session import get_engine

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"
log = logging.getLogger("ppe.health")


@lru_cache(maxsize=1)
def expected_heads() -> frozenset[str]:
    return frozenset(ScriptDirectory.from_config(Config(str(ALEMBIC_INI))).get_heads())


def check_ready() -> tuple[bool, dict]:
    expected = sorted(expected_heads())
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
            current = sorted(MigrationContext.configure(conn).get_current_heads())
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
