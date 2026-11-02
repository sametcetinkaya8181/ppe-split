"""İnsan inceleme kararları (eklemeli; 0001-0006 değişmez, mevcut tablolarda hiçbir satır değişmez)

- incident_reviews        : incident başına GÜNCEL karar (CONFIRMED_VIOLATION / FALSE_ALARM / INCONCLUSIVE),
                            iyimser kilit için version. Satırı olmayan incident UNREVIEWED'dır: eski kayıtlar için
                            geri doldurma YAPILMAZ (hiçbir kayda sessizce doğrulanmış etiket atanmaz).
- incident_review_history : her karar (yalnız ekleme; ppe_app'te UPDATE db.roles.grants ile geri alınır, DELETE
                            zaten yoktur). previous_status ile değişiklik zinciri, request_id ile tekrar gönderim
                            koruması.
Otomatik yaşam döngüsü (active/closed/stale...) olaylardan türetilmeye devam eder; bu tablolar onu değiştirmez.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-02 20:00:00+00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import context, op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DECISIONS = "('CONFIRMED_VIOLATION', 'FALSE_ALARM', 'INCONCLUSIVE')"
NOTE = "note IS NULL OR char_length(note) BETWEEN 1 AND 500"


def upgrade() -> None:
    op.create_table(
        "incident_reviews",
        sa.Column("incident_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("reviewed_by", sa.BigInteger(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("annotation_candidate", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.CheckConstraint(f"status IN {DECISIONS}", name=op.f("ck_incident_reviews_status")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_incident_reviews_version_positive")),
        sa.CheckConstraint(NOTE, name=op.f("ck_incident_reviews_note_length")),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"],
                                name=op.f("fk_incident_reviews_incident_id_incidents"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["reviewed_by"], ["app_users.id"],
                                name=op.f("fk_incident_reviews_reviewed_by_app_users"), ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("incident_id", name=op.f("pk_incident_reviews")),
    )
    op.create_index("ix_incident_reviews_status", "incident_reviews", ["status"], unique=False)
    op.create_table(
        "incident_review_history",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("incident_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("previous_status", sa.String(length=24), nullable=True),
        sa.Column("reviewer_id", sa.BigInteger(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("annotation_candidate", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("request_id", sa.UUID(), nullable=False),
        sa.CheckConstraint(f"status IN {DECISIONS}", name=op.f("ck_incident_review_history_status")),
        sa.CheckConstraint(f"previous_status IS NULL OR previous_status IN {DECISIONS}",
                           name=op.f("ck_incident_review_history_previous_status")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_incident_review_history_version_positive")),
        sa.CheckConstraint("(version = 1) = (previous_status IS NULL)",
                           name=op.f("ck_incident_review_history_first_has_no_previous")),
        sa.CheckConstraint(NOTE, name=op.f("ck_incident_review_history_note_length")),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"],
                                name=op.f("fk_incident_review_history_incident_id_incidents"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["reviewer_id"], ["app_users.id"],
                                name=op.f("fk_incident_review_history_reviewer_id_app_users"), ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_incident_review_history")),
        sa.UniqueConstraint("incident_id", "version", name=op.f("uq_incident_review_history_incident_id_version")),
        sa.UniqueConstraint("request_id", name=op.f("uq_incident_review_history_request_id")),
    )
    op.create_index("ix_incident_review_history_decided", "incident_review_history",
                    [sa.text("decided_at DESC"), sa.text("id DESC")], unique=False)


def downgrade() -> None:
    if context.get_x_argument(as_dictionary=True).get("allow_data_loss") != "1":
        if op.get_bind().execute(sa.text("SELECT EXISTS (SELECT 1 FROM incident_review_history) "
                                         "OR EXISTS (SELECT 1 FROM incident_reviews)")).scalar():
            raise RuntimeError("Downgrade durduruldu: insan inceleme kararları var. "
                               "Bilerek silmek için: alembic -x allow_data_loss=1 downgrade 0006")
    op.drop_index("ix_incident_review_history_decided", table_name="incident_review_history")
    op.drop_table("incident_review_history")
    op.drop_index("ix_incident_reviews_status", table_name="incident_reviews")
    op.drop_table("incident_reviews")
