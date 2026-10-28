"""detector_runs: run'a ait kaynak bilgisi (eklemeli; 0001/0002 değişmez)

cameras.name kameranın GÜNCEL adıdır ve her kare/raporla değişebilir. Eski kamera kimlikleri (cam<sıra>) kaynak
sırasına bağlı olduğundan aynı kimlik farklı çalışmalarda farklı videolara ait olabilir. Bu yüzden her run kendi
kaynak bilgisini taşır; değerler run'ın ilk kaydında yazılır ve sonra DEĞİŞMEZ (repository: COALESCE(mevcut, yeni)).

- source_label: o run sırasında kameranın görünen adı (maskelenmiş).
- source_kind : file / webcam / rtsp / youtube / other.
- source_ref  : kimlik bilgisi içermeyen kaynak referansı (dosya için yalnızca dosya adı, URL için maskelenmiş adres).

Var olan run'lar için geri doldurma YAPILMAZ: güvenilir kanıt yok (cameras.name üzerine yazılmış olabilir).
NULL = "eski kayıt, kaynak doğrulanamadı".

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-28 18:00:00+00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import context, op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SOURCE_KINDS = "'file', 'webcam', 'rtsp', 'youtube', 'other'"


def upgrade() -> None:
    op.add_column("detector_runs", sa.Column("source_label", sa.String(length=120), nullable=True))
    op.add_column("detector_runs", sa.Column("source_kind", sa.String(length=20), nullable=True))
    op.add_column("detector_runs", sa.Column("source_ref", sa.String(length=300), nullable=True))
    op.create_check_constraint(op.f("ck_detector_runs_source_label_not_empty"), "detector_runs",
                               "source_label IS NULL OR source_label <> ''")
    op.create_check_constraint(op.f("ck_detector_runs_run_source_kind"), "detector_runs",
                               f"source_kind IS NULL OR source_kind IN ({SOURCE_KINDS})")


def downgrade() -> None:
    if context.get_x_argument(as_dictionary=True).get("allow_data_loss") != "1":
        dolu = op.get_bind().execute(sa.text(
            "SELECT EXISTS (SELECT 1 FROM detector_runs WHERE source_label IS NOT NULL OR source_kind IS NOT NULL"
            " OR source_ref IS NOT NULL)")).scalar()
        if dolu:
            raise RuntimeError("Downgrade durduruldu: detector_runs kaynak bilgileri dolu. "
                               "Bilerek silmek için: alembic -x allow_data_loss=1 downgrade 0002")
    op.drop_constraint(op.f("ck_detector_runs_run_source_kind"), "detector_runs", type_="check")
    op.drop_constraint(op.f("ck_detector_runs_source_label_not_empty"), "detector_runs", type_="check")
    op.drop_column("detector_runs", "source_ref")
    op.drop_column("detector_runs", "source_kind")
    op.drop_column("detector_runs", "source_label")
