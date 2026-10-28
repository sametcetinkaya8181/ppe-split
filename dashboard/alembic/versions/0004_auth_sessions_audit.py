"""Tarayıcı kullanıcıları, sunucu tarafı oturumlar ve güvenlik denetim kaydı (eklemeli; 0001-0003 değişmez)

- app_users     : yerel kullanıcı (Argon2id özeti), rol (admin/supervisor/viewer), isteğe bağlı kamera kapsamı,
                  kaba kuvvet kilidi alanları. Parola düz metni hiçbir yerde saklanmaz.
- user_sessions : oturum token'ının SHA-256 özeti (token'ın kendisi yalnız tarayıcı çerezinde), CSRF token'ı,
                  boşta/mutlak süre, iptal. Çıkış = iptal (UPDATE); uygulama rolüne DELETE gerekmez.
- audit_log     : yalnız ekleme (ppe_app'te UPDATE de geri alınır). Parola, token, çerez içermez.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-28 19:00:00+00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "app_users",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("camera_scope", postgresql.ARRAY(sa.String(length=40)), nullable=True),
        sa.Column("disabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("failed_attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("password_changed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("role IN ('admin', 'supervisor', 'viewer')", name=op.f("ck_app_users_role")),
        sa.CheckConstraint("username ~ '^[a-z0-9_.-]{3,64}$'", name=op.f("ck_app_users_username_format")),
        sa.CheckConstraint("password_hash LIKE '$argon2id$%'", name=op.f("ck_app_users_password_hash_argon2id")),
        sa.CheckConstraint("failed_attempts >= 0", name=op.f("ck_app_users_failed_attempts_non_negative")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_app_users")),
        sa.UniqueConstraint("username", name=op.f("uq_app_users_username")),
    )
    op.create_table(
        "user_sessions",
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("csrf_token", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoke_reason", sa.String(length=32), nullable=True),
        sa.CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_user_sessions_token_hash_format")),
        sa.ForeignKeyConstraint(["user_id"], ["app_users.id"], name=op.f("fk_user_sessions_user_id_app_users"),
                                ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("token_hash", name=op.f("pk_user_sessions")),
    )
    op.create_index("ix_user_sessions_user", "user_sessions", ["user_id"], unique=False)
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("event", sa.String(length=40), nullable=False),
        sa.Column("outcome", sa.String(length=10), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("client_ip", sa.String(length=45), nullable=True),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.CheckConstraint("outcome IN ('success', 'failure', 'denied')", name=op.f("ck_audit_log_outcome")),
        sa.CheckConstraint("detail IS NULL OR (jsonb_typeof(detail) = 'object' AND octet_length(detail::text) <= 4096)",
                           name=op.f("ck_audit_log_detail_small_object")),
        sa.ForeignKeyConstraint(["user_id"], ["app_users.id"], name=op.f("fk_audit_log_user_id_app_users"),
                                ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
    )
    op.create_index("ix_audit_log_at", "audit_log", [sa.text("at DESC"), sa.text("id DESC")], unique=False)


def downgrade() -> None:
    if context.get_x_argument(as_dictionary=True).get("allow_data_loss") != "1":
        dolu = op.get_bind().execute(sa.text(
            "SELECT EXISTS (SELECT 1 FROM app_users) OR EXISTS (SELECT 1 FROM audit_log)")).scalar()
        if dolu:
            raise RuntimeError("Downgrade durduruldu: kullanıcı/denetim kayıtları var. "
                               "Bilerek silmek için: alembic -x allow_data_loss=1 downgrade 0003")
    op.drop_index("ix_audit_log_at", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_index("ix_user_sessions_user", table_name="user_sessions")
    op.drop_table("user_sessions")
    op.drop_table("app_users")
