"""Tarayıcı oturumu (/api/auth/*) ve yönetim (/api/admin/*) uçları + rol bağımlılıkları.

Durum değiştiren her oturumlu istek: geçerli oturum + X-CSRF-Token + aynı köken (Origin). Giriş: aynı köken +
JSON gövde (form ile gönderilemez) + hesap kilidi + IP başına sınır. Hata mesajları kullanıcı varlığını açığa
vurmaz. Parola/token/çerez hiçbir log veya denetim kaydına yazılmaz."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError

from db.session import get_session

from . import passwords, security, store
from .security import AuthCtx

router = APIRouter()
GENERIC_FAIL = "Kullanıcı adı veya parola hatalı ya da hesap geçici olarak kilitli."


# ---------------------------------------------------------------- bağımlılıklar
def require(role: str):
    def dep(request: Request) -> AuthCtx:
        ctx = security.authenticate(request)
        if ctx is None:
            security.deny(request, 401, "auth_required", required=role)
        if not ctx.at_least(role):
            security.deny(request, 403, "insufficient_role", ctx, required=role)
        request.state.auth = ctx
        return ctx
    return dep


def require_csrf(role: str):
    base = require(role)

    def dep(request: Request) -> AuthCtx:
        ctx = base(request)
        if not security.csrf_ok(request, ctx):
            security.audit("csrf_failure", "denied", conn=request, user_id=ctx.user_id,
                           detail={"path": request.url.path[:120], "method": request.method})
            raise HTTPException(status_code=403, detail="CSRF doğrulaması başarısız")
        return ctx
    return dep


def _db(fn, *args, **kwargs):
    try:
        with get_session() as s, s.begin():
            return fn(s, *args, **kwargs)
    except (store.StoreError, passwords.PolicyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except LookupError:
        raise HTTPException(status_code=404, detail="kullanıcı bulunamadı") from None
    except (SQLAlchemyError, RuntimeError):
        raise HTTPException(status_code=503, detail="veritabanı şu an kullanılamıyor; tekrar deneyin") from None


def _user_out(ctx_or_row) -> dict:
    if isinstance(ctx_or_row, AuthCtx):
        return {"username": ctx_or_row.username, "role": ctx_or_row.role,
                "cameras": list(ctx_or_row.scope) if ctx_or_row.scope is not None else None}
    r = ctx_or_row
    return {"id": r["id"], "username": r["username"], "role": r["role"], "cameras": r["camera_scope"],
            "disabled": r["disabled"], "locked": r["locked_until"] is not None and r["locked_until"] > store.now(),
            "password_changed_at": r["password_changed_at"].isoformat(), "created_at": r["created_at"].isoformat()}


def _set_cookie(resp: Response, token: str) -> None:
    resp.set_cookie(security.COOKIE, token, max_age=int(security.SESSION_ABSOLUTE), path="/", httponly=True,
                    samesite="strict", secure=security.PROFILE == "lan")


def _clear_cookie(resp: Response) -> None:
    resp.delete_cookie(security.COOKIE, path="/", httponly=True, samesite="strict", secure=security.PROFILE == "lan")


# ---------------------------------------------------------------- oturum
class LoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(max_length=64)
    password: str = Field(max_length=passwords.MAX_LEN)


@router.post("/api/auth/login")
def login(body: LoginIn, request: Request, response: Response):
    if not security.origin_ok(request):
        security.audit("login", "denied", conn=request, detail={"reason": "bad_origin"})
        raise HTTPException(status_code=403, detail="geçersiz köken")
    ip = security.client_ip(request)
    wait = security.IP_LIMITER.blocked(ip)
    if wait:
        security.audit("login", "denied", conn=request, detail={"reason": "ip_rate_limited"},
                       dedupe=f"ratelimit:{ip}")
        raise HTTPException(status_code=429, detail="Çok fazla başarısız deneme; daha sonra tekrar deneyin.",
                            headers={"Retry-After": str(int(wait) + 1)})

    def attempt(s):
        try:
            name = store.normalize_username(body.username)
        except store.StoreError:
            name = None
        user = store.get_user(s, username=name) if name else None
        reason = None
        if user is None:
            passwords.verify(None, body.password)                  # zamanlama eşitleme
            reason = "unknown_user"
        elif user["disabled"]:
            passwords.verify(None, body.password)
            reason = "disabled"
        elif user["locked_until"] is not None and user["locked_until"] > store.now():
            passwords.verify(None, body.password)
            reason = "locked"
        elif not passwords.verify(user["password_hash"], body.password):
            reason = "locked_now" if store.record_failure(s, user["id"], security.LOGIN_MAX_FAILURES,
                                                          security.LOGIN_LOCK_MINUTES) else "bad_password"
        if reason:
            store.audit(s, "login", "failure", user_id=user["id"] if user else None, ip=ip, detail={"reason": reason})
            return None
        store.reset_failures(s, user["id"])
        if passwords.needs_rehash(user["password_hash"]):
            store.rehash(s, user["id"], body.password)
        old = request.cookies.get(security.COOKIE, "")
        if security.TOKEN_RE.match(old):                          # oturum sabitleme: eski oturum iptal
            store.revoke_session(s, store.token_hash(old), "relogin")
        token, csrf = store.create_session(s, user["id"], security.SESSION_ABSOLUTE)
        store.audit(s, "login", "success", user_id=user["id"], ip=ip, detail={"role": user["role"]})
        return user, token, csrf

    result = _db(attempt)
    if result is None:
        security.IP_LIMITER.fail(ip)
        raise HTTPException(status_code=401, detail=GENERIC_FAIL)
    user, token, csrf = result
    _set_cookie(response, token)
    return {"user": {"username": user["username"], "role": user["role"], "cameras": user["camera_scope"]},
            "csrf_token": csrf}


@router.post("/api/auth/logout")
def logout(request: Request, response: Response, ctx: AuthCtx = Depends(require_csrf("viewer"))):
    def run(s):
        store.revoke_session(s, ctx.token_hash, "logout")
        store.audit(s, "logout", "success", user_id=ctx.user_id, ip=security.client_ip(request))
    _db(run)
    security.invalidate(token_hash=ctx.token_hash)
    _clear_cookie(response)
    return {"ok": True}


@router.get("/api/auth/me")
def me(ctx: AuthCtx = Depends(require("viewer"))):
    return {"user": _user_out(ctx), "csrf_token": ctx.csrf, "profile": security.PROFILE,
            "permissions": {"live": ctx.at_least("supervisor"), "snapshots": ctx.at_least("supervisor"),
                            "admin": ctx.at_least("admin")}}


class PasswordChangeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: str = Field(max_length=passwords.MAX_LEN)
    new_password: str = Field(max_length=passwords.MAX_LEN)


@router.post("/api/auth/password")
def change_password(body: PasswordChangeIn, request: Request, response: Response,
                    ctx: AuthCtx = Depends(require_csrf("viewer"))):
    def run(s):
        user = store.get_user(s, user_id=ctx.user_id)
        if not passwords.verify(user["password_hash"], body.current_password):
            store.audit(s, "password_change", "failure", user_id=ctx.user_id, ip=security.client_ip(request),
                        detail={"reason": "bad_current_password"})
            return False
        store.set_password(s, ctx.user_id, body.new_password)        # tüm oturumları iptal eder
        store.audit(s, "password_change", "success", user_id=ctx.user_id, ip=security.client_ip(request))
        return True
    if not _db(run):
        raise HTTPException(status_code=403, detail="mevcut parola hatalı")
    security.invalidate(user_id=ctx.user_id)
    _clear_cookie(response)
    return {"ok": True, "relogin": True}


# ---------------------------------------------------------------- yönetim (yalnız admin)
class UserCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(max_length=64)
    password: str = Field(max_length=passwords.MAX_LEN)
    role: str
    cameras: Optional[list[str]] = None


class UserUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Optional[str] = None
    disabled: Optional[bool] = None
    cameras: Optional[list[str]] = None
    all_cameras: bool = False


class PasswordSetIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(max_length=passwords.MAX_LEN)


@router.get("/api/admin/users")
def admin_users(ctx: AuthCtx = Depends(require("admin"))):
    return [_user_out(r) for r in _db(store.list_users)]


@router.post("/api/admin/users", status_code=201)
def admin_create_user(body: UserCreateIn, request: Request, ctx: AuthCtx = Depends(require_csrf("admin"))):
    def run(s):
        uid = store.create_user(s, body.username, body.password, body.role, body.cameras)
        store.audit(s, "user_create", "success", user_id=ctx.user_id, ip=security.client_ip(request),
                    detail={"target_user_id": uid, "role": body.role})
        return uid
    return {"id": _db(run)}


@router.patch("/api/admin/users/{user_id}")
def admin_update_user(user_id: int, body: UserUpdateIn, request: Request,
                      ctx: AuthCtx = Depends(require_csrf("admin"))):
    def run(s):
        target = store.get_user(s, user_id=user_id)
        if target is None:
            raise LookupError
        demote = target["role"] == "admin" and (body.role not in (None, "admin") or body.disabled is True)
        if demote and not target["disabled"] and store.active_admins(s) <= 1:
            raise store.StoreError("son etkin yönetici devre dışı bırakılamaz veya rolü düşürülemez")
        scope = None if body.all_cameras else (body.cameras if body.cameras is not None else ...)
        store.update_user(s, user_id, role=body.role, disabled=body.disabled, scope=scope)
        changes = [k for k in ("role", "disabled", "cameras") if getattr(body, k) is not None] + (
            ["all_cameras"] if body.all_cameras else [])
        if body.role is not None or body.disabled or scope is not ...:
            store.revoke_user_sessions(s, user_id, "policy_changed")   # yeni yetki yeni oturumla geçerli olur
        store.audit(s, "user_update", "success", user_id=ctx.user_id, ip=security.client_ip(request),
                    detail={"target_user_id": user_id, "changes": changes, "role": body.role})
    _db(run)
    security.invalidate(user_id=user_id)
    return {"ok": True}


@router.post("/api/admin/users/{user_id}/password")
def admin_set_password(user_id: int, body: PasswordSetIn, request: Request,
                       ctx: AuthCtx = Depends(require_csrf("admin"))):
    def run(s):
        store.set_password(s, user_id, body.password)
        store.audit(s, "password_reset", "success", user_id=ctx.user_id, ip=security.client_ip(request),
                    detail={"target_user_id": user_id})
    _db(run)
    security.invalidate(user_id=user_id)
    return {"ok": True}


@router.post("/api/admin/users/{user_id}/revoke-sessions")
def admin_revoke(user_id: int, request: Request, ctx: AuthCtx = Depends(require_csrf("admin"))):
    def run(s):
        if store.get_user(s, user_id=user_id) is None:
            raise LookupError
        n = store.revoke_user_sessions(s, user_id, "admin_revoked")
        store.audit(s, "sessions_revoke", "success", user_id=ctx.user_id, ip=security.client_ip(request),
                    detail={"target_user_id": user_id, "count": n})
        return n
    n = _db(run)
    security.invalidate(user_id=user_id)
    return {"revoked": n}


@router.get("/api/admin/audit")
def admin_audit(limit: int = Query(100, ge=1, le=500), before_id: Optional[int] = Query(None, ge=1),
                ctx: AuthCtx = Depends(require("admin"))):
    rows = _db(store.list_audit, limit, before_id)
    return [{**r, "at": r["at"].isoformat()} for r in rows]
