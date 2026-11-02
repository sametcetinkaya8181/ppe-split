"""detector_runs: 'disconnected' durumu (eklemeli; 0001-0005 değişmez)

Canlı kaynağın (RTSP/ağ akışı/kamera) beklenmedik kopması dedektörde hata fırlatmadan akışın bitmesi olarak görülür
ve FAZ 9'a kadar 'finished' (normal bitiş) olarak kaydediliyordu (FAZ 9 raporu §9). 'disconnected': kaynak bağlantısı
koptu / okuma zaman aşımına uğradı; ayrıntı rapor metadata'sında end_reason (source_lost, source_lost_after_stall).
Yalnız dedektör raporu atar; eski kayıtlar tahminle değiştirilmez.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-02 00:00:00+00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD = "status IN ('running', 'finished', 'error', 'interrupted', 'abandoned')"
NEW = "status IN ('running', 'finished', 'error', 'interrupted', 'abandoned', 'disconnected')"


def upgrade() -> None:
    op.drop_constraint(op.f("ck_detector_runs_status"), "detector_runs", type_="check")
    op.create_check_constraint(op.f("ck_detector_runs_status"), "detector_runs", NEW)


def downgrade() -> None:
    if op.get_bind().execute(sa.text("SELECT EXISTS (SELECT 1 FROM detector_runs WHERE status = 'disconnected')")
                             ).scalar():
        raise RuntimeError("Downgrade durduruldu: 'disconnected' durumlu run'lar var (durum bilgisi kaybolur).")
    op.drop_constraint(op.f("ck_detector_runs_status"), "detector_runs", type_="check")
    op.create_check_constraint(op.f("ck_detector_runs_status"), "detector_runs", OLD)
