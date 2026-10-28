"""Kullanıcı, oturum ve denetim kaydı veri erişimi (SQLAlchemy Core, parametre bağlamalı).

Fonksiyonlar çağıranın Session/transaction'ında çalışır. Silme YOKTUR: çıkış/iptal = revoked_at güncellemesi,
kullanıcı kaldırma = disabled. Oturum token'ı yalnız SHA-256 özetiyle saklanır."""
import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, null, select, update
from sqlalchemy.orm import Session

from db.models import AppUser, AuditLog, UserSession

from . import passwords

ROLES = ("viewer", "supervisor", "admin")
RANK = {r: i for i, r in enumerate(ROLES)}
USERNAME_RE = re.compile(r"^[a-z0-9_.-]{3,64}$")
CAMERA_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
U = AppUser.__table__
S = UserSession.__table__
A = AuditLog.__table__


class StoreError(ValueError):
    """Geçersiz kullanıcı verisi (HTTP 422/409); mesaj gizli değer içermez."""


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def normalize_username(name) -> str:
    name = (name or "").strip().lower() if isinstance(name, str) else ""
    if not USERNAME_RE.match(name):
        raise StoreError("kullanıcı adı 3-64 karakter: a-z, 0-9, _ . -")
    return name


def normalize_scope(scope):
    if scope is None:
        return None
    if not isinstance(scope, (list, tuple)) or len(scope) > 255 or not all(
            isinstance(c, str) and CAMERA_RE.match(c) for c in scope):
        raise StoreError("kamera kapsamı geçerli kamera kimliklerinden oluşan bir liste olmalı")
    return sorted(set(scope))


def _role(role) -> str:
    if role not in ROLES:
        raise StoreError(f"rol şunlardan biri olmalı: {', '.join(ROLES)}")
    return role


# ---------------------------------------------------------------- kullanıcılar
def get_user(s: Session, *, username=None, user_id=None):
    q = select(U)
    q = q.where(U.c.username == username) if username is not None else q.where(U.c.id == user_id)
    return s.execute(q).mappings().first()


def create_user(s: Session, username: str, password: str, role: str, scope=None) -> int:
    username = normalize_username(username)
    passwords.validate_new(password, username)
    if get_user(s, username=username):
        raise StoreError("bu kullanıcı adı zaten var")
    return s.execute(U.insert().values(username=username, password_hash=passwords.hash_password(password),
                                       role=_role(role), camera_scope=normalize_scope(scope))
                     .returning(U.c.id)).scalar_one()


def list_users(s: Session) -> list:
    return [dict(r) for r in s.execute(
        select(U.c.id, U.c.username, U.c.role, U.c.camera_scope, U.c.disabled, U.c.locked_until,
               U.c.password_changed_at, U.c.created_at).order_by(U.c.username)).mappings()]


def update_user(s: Session, user_id: int, *, role=None, disabled=None, scope=...) -> None:
    values = {"updated_at": func.now()}
    if role is not None:
        values["role"] = _role(role)
    if disabled is not None:
        values["disabled"] = bool(disabled)
    if scope is not ...:
        values["camera_scope"] = normalize_scope(scope)
    if s.execute(update(U).where(U.c.id == user_id).values(**values)).rowcount != 1:
        raise LookupError("kullanıcı yok")


def active_admins(s: Session) -> int:
    return s.execute(select(func.count()).where(U.c.role == "admin", U.c.disabled.is_(False))).scalar_one()


def set_password(s: Session, user_id: int, password: str) -> None:
    user = get_user(s, user_id=user_id)
    if user is None:
        raise LookupError("kullanıcı yok")
    passwords.validate_new(password, user["username"])
    s.execute(update(U).where(U.c.id == user_id).values(
        password_hash=passwords.hash_password(password), password_changed_at=func.now(), failed_attempts=0,
        locked_until=None, updated_at=func.now()))
    revoke_user_sessions(s, user_id, "password_changed")


def rehash(s: Session, user_id: int, password: str) -> None:
    s.execute(update(U).where(U.c.id == user_id).values(password_hash=passwords.hash_password(password)))


def record_failure(s: Session, user_id: int, max_failures: int, lock_minutes: float) -> bool:
    """Başarısız denemeyi sayar; eşik aşılırsa hesabı kilitler. Döner: kilitlendi mi."""
    n = s.execute(update(U).where(U.c.id == user_id).values(failed_attempts=U.c.failed_attempts + 1)
                  .returning(U.c.failed_attempts)).scalar_one()
    if n >= max_failures:
        s.execute(update(U).where(U.c.id == user_id).values(
            locked_until=func.now() + timedelta(minutes=lock_minutes), failed_attempts=0))
        return True
    return False


def reset_failures(s: Session, user_id: int) -> None:
    s.execute(update(U).where(U.c.id == user_id).values(failed_attempts=0, locked_until=None))


# ---------------------------------------------------------------- oturumlar
def create_session(s: Session, user_id: int, absolute_seconds: float) -> tuple[str, str]:
    """Döner: (çerez token'ı, CSRF token'ı). Token'ın yalnız özeti DB'ye yazılır."""
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    s.execute(S.insert().values(token_hash=token_hash(token), user_id=user_id, csrf_token=csrf,
                                expires_at=func.now() + timedelta(seconds=absolute_seconds)))
    return token, csrf


def load_session(s: Session, th: str, idle_seconds: float):
    """Geçerli oturum + kullanıcı veya None (iptal, süresi dolmuş, boşta kalmış, kullanıcı devre dışı)."""
    return s.execute(
        select(S.c.token_hash, S.c.csrf_token, S.c.last_seen_at, U.c.id.label("user_id"), U.c.username, U.c.role,
               U.c.camera_scope)
        .select_from(S.join(U, U.c.id == S.c.user_id))
        .where(S.c.token_hash == th, S.c.revoked_at.is_(None), S.c.expires_at > func.now(),
               S.c.last_seen_at > func.now() - timedelta(seconds=idle_seconds), U.c.disabled.is_(False))
    ).mappings().first()


def touch_session(s: Session, th: str) -> None:
    s.execute(update(S).where(S.c.token_hash == th, S.c.revoked_at.is_(None)).values(last_seen_at=func.now()))


def revoke_session(s: Session, th: str, reason: str) -> None:
    s.execute(update(S).where(S.c.token_hash == th, S.c.revoked_at.is_(None))
              .values(revoked_at=func.now(), revoke_reason=reason))


def revoke_user_sessions(s: Session, user_id: int, reason: str) -> int:
    return s.execute(update(S).where(S.c.user_id == user_id, S.c.revoked_at.is_(None))
                     .values(revoked_at=func.now(), revoke_reason=reason)).rowcount


# ---------------------------------------------------------------- denetim kaydı
AUDIT_DETAIL_KEYS = {"reason", "path", "method", "role", "target_user_id", "changes", "camera_id", "snapshot",
                     "required_role", "profile", "count"}


def audit(s: Session, event: str, outcome: str, *, user_id=None, ip=None, detail=None) -> None:
    """Yalnız izinli detay anahtarları yazılır (parola/token/çerez/görüntü asla)."""
    d = {k: v for k, v in (detail or {}).items() if k in AUDIT_DETAIL_KEYS and v is not None} or None
    s.execute(A.insert().values(event=event[:40], outcome=outcome, user_id=user_id,
                                client_ip=(ip or None) and str(ip)[:45],
                                detail=d if d is not None else null()))   # JSON 'null' değil SQL NULL


def list_audit(s: Session, limit: int = 100, before_id: int | None = None) -> list:
    q = (select(A.c.id, A.c.at, A.c.event, A.c.outcome, A.c.user_id, U.c.username, A.c.client_ip, A.c.detail)
         .select_from(A.outerjoin(U, U.c.id == A.c.user_id)).order_by(A.c.id.desc()).limit(limit))
    if before_id is not None:
        q = q.where(A.c.id < before_id)
    return [dict(r) for r in s.execute(q).mappings()]


def now() -> datetime:
    return datetime.now(timezone.utc)
