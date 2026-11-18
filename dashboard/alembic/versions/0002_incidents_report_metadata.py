"""incidents tablosu + detector_runs.report_metadata (eklemeli; 0001 değişmez)

- incidents: incident_id -> tek (run_id, camera_id, pid). violation_events bileşik FK ile bağlanır.
  Var olan olaylardan geri doldurulur; aynı incident_id farklı üçlülerle kayıtlıysa PK ihlaliyle migration
  DURUR (çelişkili veri sessizce birleştirilmez).
- detector_runs.report_metadata: rapor özeti için sınırlı JSONB (nesne, <= 256 KiB).

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-28 14:00:00+00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("detector_runs", sa.Column("report_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.create_check_constraint(
        op.f("ck_detector_runs_report_metadata_object"), "detector_runs",
        "report_metadata IS NULL OR (jsonb_typeof(report_metadata) = 'object'"
        " AND octet_length(report_metadata::text) <= 262144)")

    op.create_table(
        "incidents",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("camera_id", sa.String(length=40), nullable=False),
        sa.Column("pid", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("pid >= 0", name=op.f("ck_incidents_pid_non_negative")),
        sa.ForeignKeyConstraint(["run_id", "camera_id"], ["detector_runs.id", "detector_runs.camera_id"],
                                name="fk_incidents_run_id_camera_id_detector_runs", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_incidents")),
        sa.UniqueConstraint("id", "run_id", "camera_id", "pid", name=op.f("uq_incidents_id_run_id_camera_id_pid")),
    )
    op.create_index("ix_incidents_run_pid", "incidents", ["run_id", "pid"], unique=False)

    # geri doldurma: DISTINCT ON kullanılmaz; çelişki varsa PK hatası verip durur
    op.execute("INSERT INTO incidents (id, run_id, camera_id, pid) "
               "SELECT DISTINCT incident_id, run_id, camera_id, pid FROM violation_events")
    op.create_foreign_key("fk_violation_events_incident_incidents", "violation_events", "incidents",
                          ["incident_id", "run_id", "camera_id", "pid"], ["id", "run_id", "camera_id", "pid"],
                          ondelete="RESTRICT")


def downgrade() -> None:
    # incidents violation_events'ten yeniden türetilebilir; report_metadata ise kaybolur -> onay gerekir
    if context.get_x_argument(as_dictionary=True).get("allow_data_loss") != "1":
        dolu = op.get_bind().execute(sa.text(
            "SELECT EXISTS (SELECT 1 FROM detector_runs WHERE report_metadata IS NOT NULL)")).scalar()
        if dolu:
            raise RuntimeError("Downgrade durduruldu: detector_runs.report_metadata dolu. "
                               "Bilerek silmek için: alembic -x allow_data_loss=1 downgrade 0001")
    op.drop_constraint("fk_violation_events_incident_incidents", "violation_events", type_="foreignkey")
    op.drop_index("ix_incidents_run_pid", table_name="incidents")
    op.drop_table("incidents")
    op.drop_constraint(op.f("ck_detector_runs_report_metadata_object"), "detector_runs", type_="check")
    op.drop_column("detector_runs", "report_metadata")
