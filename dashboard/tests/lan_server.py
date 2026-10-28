"""LAN smoke testi için İZOLE dashboard (deploy/lan/lan_smoke.py başlatır). Yalnız 'ppe_lan_test' veritabanı.
Ortam: SECURITY_PROFILE=lan, ALLOWED_HOSTS, TRUSTED_PROXIES, INGEST_TOKEN, LAN_SMOKE_PASS (rastgele, yalnız test)."""
import os

from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.conftest import alembic_cfg

base = make_url(os.environ["TEST_DATABASE_URL"])
admin = create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
with admin.connect() as c:
    c.execute(text('DROP DATABASE IF EXISTS "ppe_lan_test" WITH (FORCE)'))
    c.execute(text('CREATE DATABASE "ppe_lan_test"'))
admin.dispose()
url = base.set(database="ppe_lan_test").render_as_string(hide_password=False)
command.upgrade(alembic_cfg(url), "head")
os.environ["DATABASE_URL"] = url
from auth import store  # noqa: E402  (DATABASE_URL ayarlandıktan sonra)
from db.session import get_session  # noqa: E402

with get_session() as s, s.begin():
    store.create_user(s, "lan_sup", os.environ["LAN_SMOKE_PASS"], "supervisor")
os.makedirs(os.environ.get("DATA_DIR", "/tmp/lan") + "/snapshots", exist_ok=True)
os.execvp("python", ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000",
                     "--log-level", "warning", "--no-server-header"])
