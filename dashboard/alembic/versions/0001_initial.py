"""initial: cameras, detector_runs, violation_events, person_run_reports

Autogenerate taslağından üretildi ve elle gözden geçirildi (db/models.py ile birebir).
Tüm FK'ler ON DELETE RESTRICT: geçmiş kayıtlar zincirleme silinmez.

Revision ID: 0001
Revises:
Create Date: 2026-10-28 12:39:04.837961+00:00
"""
from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

TABLES = ("violation_events", "person_run_reports", "detector_runs", "cameras")

revision: str = '0001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('cameras',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('source_kind', sa.String(length=20), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("id ~ '^[A-Za-z0-9_-]{1,40}$'", name=op.f('ck_cameras_id_format')),
    sa.CheckConstraint("name <> ''", name=op.f('ck_cameras_name_not_empty')),
    sa.CheckConstraint("source_kind IS NULL OR source_kind IN ('file', 'webcam', 'rtsp', 'youtube', 'other')", name=op.f('ck_cameras_source_kind')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_cameras'))
    )
    op.create_table('detector_runs',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('camera_id', sa.String(length=40), nullable=False),
    sa.Column('loop_no', sa.Integer(), nullable=True),
    sa.Column('status', sa.String(length=20), server_default='running', nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('fps', sa.Numeric(precision=6, scale=2), nullable=True),
    sa.Column('width', sa.Integer(), nullable=True),
    sa.Column('height', sa.Integer(), nullable=True),
    sa.CheckConstraint("(status = 'running') = (finished_at IS NULL)", name=op.f('ck_detector_runs_finished_matches_status')),
    sa.CheckConstraint("status IN ('running', 'finished', 'error', 'interrupted')", name=op.f('ck_detector_runs_status')),
    sa.CheckConstraint('(width IS NULL OR width > 0) AND (height IS NULL OR height > 0)', name=op.f('ck_detector_runs_size_positive')),
    sa.CheckConstraint('finished_at IS NULL OR finished_at >= started_at', name=op.f('ck_detector_runs_finished_after_start')),
    sa.CheckConstraint('fps IS NULL OR fps > 0', name=op.f('ck_detector_runs_fps_positive')),
    sa.CheckConstraint('loop_no IS NULL OR loop_no >= 1', name=op.f('ck_detector_runs_loop_no_positive')),
    sa.ForeignKeyConstraint(['camera_id'], ['cameras.id'], name=op.f('fk_detector_runs_camera_id_cameras'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_detector_runs')),
    sa.UniqueConstraint('id', 'camera_id', name=op.f('uq_detector_runs_id_camera_id'))
    )
    op.create_index('ix_detector_runs_camera_started', 'detector_runs', ['camera_id', sa.literal_column('started_at DESC')], unique=False)
    op.create_table('person_run_reports',
    sa.Column('run_id', sa.UUID(), nullable=False),
    sa.Column('pid', sa.Integer(), nullable=False),
    sa.Column('first_sec', sa.Numeric(precision=10, scale=1), nullable=False),
    sa.Column('last_sec', sa.Numeric(precision=10, scale=1), nullable=False),
    sa.Column('observations', sa.Integer(), nullable=False),
    sa.Column('helmet_rate', sa.SmallInteger(), nullable=False),
    sa.Column('vest_rate', sa.SmallInteger(), nullable=False),
    sa.Column('ever_violation', sa.Boolean(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('first_sec >= 0 AND last_sec >= first_sec', name=op.f('ck_person_run_reports_time_range')),
    sa.CheckConstraint('helmet_rate BETWEEN 0 AND 100', name=op.f('ck_person_run_reports_helmet_rate_range')),
    sa.CheckConstraint('observations > 0', name=op.f('ck_person_run_reports_observations_positive')),
    sa.CheckConstraint('pid >= 0', name=op.f('ck_person_run_reports_pid_non_negative')),
    sa.CheckConstraint('vest_rate BETWEEN 0 AND 100', name=op.f('ck_person_run_reports_vest_rate_range')),
    sa.ForeignKeyConstraint(['run_id'], ['detector_runs.id'], name=op.f('fk_person_run_reports_run_id_detector_runs'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('run_id', 'pid', name=op.f('pk_person_run_reports'))
    )
    op.create_table('violation_events',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('event_id', sa.UUID(), nullable=False),
    sa.Column('incident_id', sa.UUID(), nullable=False),
    sa.Column('run_id', sa.UUID(), nullable=False),
    sa.Column('camera_id', sa.String(length=40), nullable=False),
    sa.Column('pid', sa.Integer(), nullable=False),
    sa.Column('event_type', sa.String(length=10), nullable=False),
    sa.Column('missing_helmet', sa.Boolean(), nullable=False),
    sa.Column('missing_vest', sa.Boolean(), nullable=False),
    sa.Column('missing_text', sa.Text(), server_default='', nullable=False),
    sa.Column('helmet_rate', sa.SmallInteger(), nullable=True),
    sa.Column('vest_rate', sa.SmallInteger(), nullable=True),
    sa.Column('video_time_sec', sa.Numeric(precision=10, scale=1), nullable=True),
    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('ingested_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('box', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('snapshot_path', sa.Text(), nullable=True),
    sa.CheckConstraint("(event_type = 'end' AND NOT missing_helmet AND NOT missing_vest) OR (event_type IN ('start', 'ongoing') AND (missing_helmet OR missing_vest))", name=op.f('ck_violation_events_equipment_matches_type')),
    sa.CheckConstraint("box IS NULL OR CASE WHEN jsonb_typeof(box) = 'array' AND jsonb_array_length(box) = 4 AND jsonb_typeof(box->0) = 'number' AND jsonb_typeof(box->1) = 'number' AND jsonb_typeof(box->2) = 'number' AND jsonb_typeof(box->3) = 'number' THEN (box->>0)::numeric >= 0 AND (box->>1)::numeric >= 0 AND (box->>2)::numeric >= (box->>0)::numeric AND (box->>3)::numeric >= (box->>1)::numeric ELSE false END", name=op.f('ck_violation_events_box_shape')),
    sa.CheckConstraint("event_type IN ('start', 'ongoing', 'end')", name=op.f('ck_violation_events_event_type')),
    sa.CheckConstraint("snapshot_path IS NULL OR (event_type = 'start' AND snapshot_path ~ '^snapshots/[A-Za-z0-9_.-]+\\.jpg$')", name=op.f('ck_violation_events_snapshot_path')),
    sa.CheckConstraint('helmet_rate IS NULL OR helmet_rate BETWEEN 0 AND 100', name=op.f('ck_violation_events_helmet_rate_range')),
    sa.CheckConstraint('pid >= 0', name=op.f('ck_violation_events_pid_non_negative')),
    sa.CheckConstraint('vest_rate IS NULL OR vest_rate BETWEEN 0 AND 100', name=op.f('ck_violation_events_vest_rate_range')),
    sa.CheckConstraint('video_time_sec IS NULL OR video_time_sec >= 0', name=op.f('ck_violation_events_video_time_non_negative')),
    sa.ForeignKeyConstraint(['run_id', 'camera_id'], ['detector_runs.id', 'detector_runs.camera_id'], name='fk_violation_events_run_id_camera_id_detector_runs', ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_violation_events')),
    sa.UniqueConstraint('event_id', name=op.f('uq_violation_events_event_id')),
    sa.UniqueConstraint('snapshot_path', name=op.f('uq_violation_events_snapshot_path'))
    )
    op.create_index('ix_violation_events_camera_occurred', 'violation_events', ['camera_id', sa.literal_column('occurred_at DESC'), sa.literal_column('id DESC')], unique=False)
    op.create_index('ix_violation_events_incident', 'violation_events', ['incident_id', 'occurred_at', 'id'], unique=False)
    op.create_index('ix_violation_events_run_pid', 'violation_events', ['run_id', 'pid', 'id'], unique=False)
    op.create_index('ix_violation_events_type_occurred', 'violation_events', ['event_type', sa.literal_column('occurred_at DESC')], unique=False)
    op.create_index('uq_violation_events_incident_end', 'violation_events', ['incident_id'], unique=True, postgresql_where=sa.text("event_type = 'end'"))
    op.create_index('uq_violation_events_incident_start', 'violation_events', ['incident_id'], unique=True, postgresql_where=sa.text("event_type = 'start'"))


def downgrade() -> None:
    # Veri koruma: tablolarda kayıt varsa açık onay olmadan silme (alembic -x allow_data_loss=1 downgrade ...).
    if context.get_x_argument(as_dictionary=True).get("allow_data_loss") != "1":
        conn = op.get_bind()
        dolu = [t for t in TABLES if conn.execute(sa.text(f"SELECT EXISTS (SELECT 1 FROM {t})")).scalar()]
        if dolu:
            raise RuntimeError(f"Downgrade durduruldu: {', '.join(dolu)} tablolarında veri var. "
                               "Bilerek silmek için: alembic -x allow_data_loss=1 downgrade <revision>")
    op.drop_index('uq_violation_events_incident_start', table_name='violation_events', postgresql_where=sa.text("event_type = 'start'"))
    op.drop_index('uq_violation_events_incident_end', table_name='violation_events', postgresql_where=sa.text("event_type = 'end'"))
    op.drop_index('ix_violation_events_type_occurred', table_name='violation_events')
    op.drop_index('ix_violation_events_run_pid', table_name='violation_events')
    op.drop_index('ix_violation_events_incident', table_name='violation_events')
    op.drop_index('ix_violation_events_camera_occurred', table_name='violation_events')
    op.drop_table('violation_events')
    op.drop_table('person_run_reports')
    op.drop_index('ix_detector_runs_camera_started', table_name='detector_runs')
    op.drop_table('detector_runs')
    op.drop_table('cameras')
