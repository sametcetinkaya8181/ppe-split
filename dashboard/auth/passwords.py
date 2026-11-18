"""Parola özeti (Argon2id, argon2-cffi) ve parola politikası. Düz parola hiçbir yere yazılmaz/loglanmaz."""
import os
import secrets

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

MIN_LEN, MAX_LEN = 12, 256          # üst sınır: çok uzun girdiyle özetleme maliyeti saldırısını önler
# OWASP önerisinin üzerinde: t=3, m=64 MiB, p=4. 'test' profili yalnız testlerde ve 'local' güvenlik profilinde.
PARAMS = {"default": dict(time_cost=3, memory_cost=65536, parallelism=4),
          "test": dict(time_cost=1, memory_cost=8192, parallelism=1)}


class PolicyError(ValueError):
    """Parola politikasına uymayan yeni parola (mesaj parolayı içermez)."""


def _profile() -> str:
    p = os.getenv("PASSWORD_HASH_PROFILE", "default")
    if p not in PARAMS:
        raise RuntimeError("PASSWORD_HASH_PROFILE geçersiz")
    if p == "test" and os.getenv("SECURITY_PROFILE", "local").strip().lower() != "local":
        raise RuntimeError("PASSWORD_HASH_PROFILE=test yalnız 'local' güvenlik profilinde kullanılabilir")
    return p


_PH = PasswordHasher(type=Type.ID, hash_len=32, salt_len=16, **PARAMS[_profile()])
_DUMMY = _PH.hash(secrets.token_urlsafe(24))     # bilinmeyen kullanıcıda da aynı maliyet (zamanlama farkı yok)


def validate_new(password: str, username: str) -> None:
    if not isinstance(password, str) or not (MIN_LEN <= len(password) <= MAX_LEN):
        raise PolicyError(f"parola {MIN_LEN}-{MAX_LEN} karakter olmalı")
    if password.strip() != password:
        raise PolicyError("parola boşlukla başlayıp bitemez")
    if username and username.lower() in password.lower():
        raise PolicyError("parola kullanıcı adını içeremez")
    if len(set(password)) < 6:
        raise PolicyError("parola en az 6 farklı karakter içermeli")


def hash_password(password: str) -> str:
    return _PH.hash(password)


def verify(stored_hash: str | None, password: str) -> bool:
    """Sabit maliyetli doğrulama; stored_hash None ise sahte özetle karşılaştırır ve False döner."""
    if not isinstance(password, str) or len(password) > MAX_LEN:
        password = ""
    try:
        return _PH.verify(stored_hash or _DUMMY, password) and stored_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(stored_hash: str) -> bool:
    try:
        return _PH.check_needs_rehash(stored_hash)
    except InvalidHashError:
        return True
