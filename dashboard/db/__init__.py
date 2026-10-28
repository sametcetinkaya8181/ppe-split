"""PostgreSQL kalıcı veri katmanı (SQLAlchemy 2 + psycopg 3). Şemayı yalnızca Alembic yönetir; create_all() kullanılmaz."""
from .base import Base
from .models import Camera, DetectorRun, PersonRunReport, ViolationEvent

__all__ = ["Base", "Camera", "DetectorRun", "ViolationEvent", "PersonRunReport"]
