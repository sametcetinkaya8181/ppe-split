"""detector_runs: 'abandoned' durumu + uzlaştırma izi (eklemeli; 0001-0004 değişmez)

Rapor göndermeden kapanan (çöken/zorla öldürülen) dedektörün run'ı sonsuza dek 'running' kalıyordu. 'interrupted'
kullanıcı durdurmasını, 'finished' normal bitişi anlatır; ikisi de çökmüş run için yanlış olur. 'abandoned':
canlılık sinyali kesildi, rapor gelmedi, sonucu bilinmiyor. Yalnız maintenance.runs ile, açık onayla ve denetim
kaydıyla atanır; sonradan rapor veya canlılık gelirse geri alınır (repository).

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-01 10:00:00+00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD = "status IN ('running', 'finished', 'error', 'interrupted')"
NEW = "status IN ('running', 'finished', 'error', 'interrupted', 'abandoned')"


def upgrade() -> None:
    op.drop_constraint(op.f("ck_detector_runs_status"), "detector_runs", type_="check")
    op.create_check_constraint(op.f("ck_detector_runs_status"), "detector_runs", NEW)
    op.add_column("detector_runs", sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("detector_runs", sa.Column("reconcile_reason", sa.String(length=64), nullable=True))


def downgrade() -> None:
    if op.get_bind().execute(sa.text("SELECT EXISTS (SELECT 1 FROM detector_runs WHERE status = 'abandoned' "
                                     "OR reconciled_at IS NOT NULL)")).scalar():
        raise RuntimeError("Downgrade durduruldu: uzlaştırılmış run'lar var (durum bilgisi kaybolur).")
    op.drop_column("detector_runs", "reconcile_reason")
    op.drop_column("detector_runs", "reconciled_at")
    op.drop_constraint(op.f("ck_detector_runs_status"), "detector_runs", type_="check")
    op.create_check_constraint(op.f("ck_detector_runs_status"), "detector_runs", OLD)
