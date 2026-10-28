"""Kalıcı kayıt tabloları. Canlı video kareleri ve JPEG içerikleri burada TUTULMAZ.

pid yalnızca bir run içindeki ByteTrack iz kimliğidir; gerçek kişi kimliği değildir.
video_time_sec (video/akış içindeki saniye) ile occurred_at (gerçek UTC zaman) ayrı alanlardır."""
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (BigInteger, Boolean, CheckConstraint, ForeignKey, ForeignKeyConstraint, Identity, Index,
                        Integer, Numeric, SmallInteger, String, Text, UniqueConstraint, func, text)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import DateTime

from .base import Base

TZ = DateTime(timezone=True)

CAMERA_ID_RE = "^[A-Za-z0-9_-]{1,40}$"            # dashboard get_cam() temizliğiyle aynı
SNAPSHOT_PATH_RE = r"^snapshots/[A-Za-z0-9_.-]+\.jpg$"
RUN_STATUSES = ("running", "finished", "error", "interrupted")
SOURCE_KINDS = ("file", "webcam", "rtsp", "youtube", "other")
EVENT_TYPES = ("start", "ongoing", "end")


def _in(col, values):
    return f"{col} IN ({', '.join(repr(v) for v in values)})"


# box: [x1, y1, x2, y2] sayı dizisi. CASE, tür kontrolü yapılmadan uzunluk/cast denenmesini engeller.
BOX_CHECK = (
    "box IS NULL OR CASE WHEN jsonb_typeof(box) = 'array' AND jsonb_array_length(box) = 4"
    " AND jsonb_typeof(box->0) = 'number' AND jsonb_typeof(box->1) = 'number'"
    " AND jsonb_typeof(box->2) = 'number' AND jsonb_typeof(box->3) = 'number'"
    " THEN (box->>0)::numeric >= 0 AND (box->>1)::numeric >= 0"
    " AND (box->>2)::numeric >= (box->>0)::numeric AND (box->>3)::numeric >= (box->>1)::numeric"
    " ELSE false END"
)

# ppe_core değişmezi: start/ongoing en az bir eksik ekipman taşır, end hiç taşımaz.
EQUIPMENT_CHECK = (
    "(event_type = 'end' AND NOT missing_helmet AND NOT missing_vest)"
    " OR (event_type IN ('start', 'ongoing') AND (missing_helmet OR missing_vest))"
)


class Camera(Base):
    __tablename__ = "cameras"
    __table_args__ = (
        CheckConstraint(f"id ~ '{CAMERA_ID_RE}'", name="id_format"),
        CheckConstraint("name <> ''", name="name_not_empty"),
        CheckConstraint(f"source_kind IS NULL OR {_in('source_kind', SOURCE_KINDS)}", name="source_kind"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    source_kind: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())
    last_seen_at: Mapped[datetime | None] = mapped_column(TZ)


class DetectorRun(Base):
    __tablename__ = "detector_runs"
    __table_args__ = (
        UniqueConstraint("id", "camera_id"),       # violation_events bileşik FK hedefi
        CheckConstraint(_in("status", RUN_STATUSES), name="status"),
        CheckConstraint("(status = 'running') = (finished_at IS NULL)", name="finished_matches_status"),
        CheckConstraint("finished_at IS NULL OR finished_at >= started_at", name="finished_after_start"),
        CheckConstraint("loop_no IS NULL OR loop_no >= 1", name="loop_no_positive"),
        CheckConstraint("fps IS NULL OR fps > 0", name="fps_positive"),
        CheckConstraint("(width IS NULL OR width > 0) AND (height IS NULL OR height > 0)", name="size_positive"),
        Index("ix_detector_runs_camera_started", "camera_id", text("started_at DESC")),
        CheckConstraint("report_metadata IS NULL OR (jsonb_typeof(report_metadata) = 'object'"
                        " AND octet_length(report_metadata::text) <= 262144)", name="report_metadata_object"),
        CheckConstraint("source_label IS NULL OR source_label <> ''", name="source_label_not_empty"),
        CheckConstraint(f"source_kind IS NULL OR {_in('source_kind', SOURCE_KINDS)}", name="run_source_kind"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    camera_id: Mapped[str] = mapped_column(String(40), ForeignKey("cameras.id", ondelete="RESTRICT"))
    loop_no: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), server_default="running")
    started_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(TZ)
    last_seen_at: Mapped[datetime | None] = mapped_column(TZ)
    fps: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    # Rapor özeti (allowlist): unclosed_incidents, unclosed_overflow, orphan_events, error, video_time_sec
    report_metadata: Mapped[dict | None] = mapped_column(JSONB)
    # 0003: run'ın kendi kaynak bilgisi; ilk kayıtta yazılır, sonra değişmez. NULL = eski kayıt (doğrulanamadı).
    source_label: Mapped[str | None] = mapped_column(String(120))
    source_kind: Mapped[str | None] = mapped_column(String(20))
    source_ref: Mapped[str | None] = mapped_column(String(300))


class Incident(Base):
    """Bir ihlal yaşam döngüsü kimliği tek bir (run, kamera, pid) üçlüsüne aittir. violation_events bu satıra
    bileşik FK ile bağlanır; böylece aynı incident_id'nin başka run/kamera/pid ile kullanılması (eşzamanlı
    istekler dahil) veritabanı düzeyinde imkânsızdır. Orphan olaylar da kendi incident satırını alır."""
    __tablename__ = "incidents"
    __table_args__ = (
        UniqueConstraint("id", "run_id", "camera_id", "pid"),
        ForeignKeyConstraint(["run_id", "camera_id"], ["detector_runs.id", "detector_runs.camera_id"],
                             ondelete="RESTRICT", name="fk_incidents_run_id_camera_id_detector_runs"),
        CheckConstraint("pid >= 0", name="pid_non_negative"),
        Index("ix_incidents_run_pid", "run_id", "pid"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    camera_id: Mapped[str] = mapped_column(String(40))
    pid: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())


class ViolationEvent(Base):
    """event_id: tekil olay (retry idempotency). incident_id: aynı start→ongoing→end yaşam döngüsü."""
    __tablename__ = "violation_events"
    __table_args__ = (
        ForeignKeyConstraint(["run_id", "camera_id"], ["detector_runs.id", "detector_runs.camera_id"],
                             ondelete="RESTRICT", name="fk_violation_events_run_id_camera_id_detector_runs"),
        ForeignKeyConstraint(["incident_id", "run_id", "camera_id", "pid"],
                             ["incidents.id", "incidents.run_id", "incidents.camera_id", "incidents.pid"],
                             ondelete="RESTRICT", name="fk_violation_events_incident_incidents"),
        CheckConstraint(_in("event_type", EVENT_TYPES), name="event_type"),
        CheckConstraint(EQUIPMENT_CHECK, name="equipment_matches_type"),
        CheckConstraint("pid >= 0", name="pid_non_negative"),
        CheckConstraint("helmet_rate IS NULL OR helmet_rate BETWEEN 0 AND 100", name="helmet_rate_range"),
        CheckConstraint("vest_rate IS NULL OR vest_rate BETWEEN 0 AND 100", name="vest_rate_range"),
        CheckConstraint("video_time_sec IS NULL OR video_time_sec >= 0", name="video_time_non_negative"),
        CheckConstraint(BOX_CHECK, name="box_shape"),
        CheckConstraint(f"snapshot_path IS NULL OR (event_type = 'start' AND snapshot_path ~ '{SNAPSHOT_PATH_RE}')",
                        name="snapshot_path"),
        Index("ix_violation_events_camera_occurred", "camera_id", text("occurred_at DESC"), text("id DESC")),
        Index("ix_violation_events_run_pid", "run_id", "pid", "id"),
        Index("ix_violation_events_type_occurred", "event_type", text("occurred_at DESC")),
        Index("ix_violation_events_incident", "incident_id", "occurred_at", "id"),
        # bir incident'te en fazla bir start ve en fazla bir end olabilir
        Index("uq_violation_events_incident_start", "incident_id", unique=True,
              postgresql_where=text("event_type = 'start'")),
        Index("uq_violation_events_incident_end", "incident_id", unique=True,
              postgresql_where=text("event_type = 'end'")),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), unique=True)
    incident_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    camera_id: Mapped[str] = mapped_column(String(40))
    pid: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(10))
    missing_helmet: Mapped[bool] = mapped_column(Boolean)
    missing_vest: Mapped[bool] = mapped_column(Boolean)
    missing_text: Mapped[str] = mapped_column(Text, server_default="")
    helmet_rate: Mapped[int | None] = mapped_column(SmallInteger)
    vest_rate: Mapped[int | None] = mapped_column(SmallInteger)
    video_time_sec: Mapped[Decimal | None] = mapped_column(Numeric(10, 1))
    occurred_at: Mapped[datetime] = mapped_column(TZ)
    ingested_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())
    box: Mapped[list | None] = mapped_column(JSONB)
    snapshot_path: Mapped[str | None] = mapped_column(Text, unique=True)


class PersonRunReport(Base):
    __tablename__ = "person_run_reports"
    __table_args__ = (
        CheckConstraint("first_sec >= 0 AND last_sec >= first_sec", name="time_range"),
        CheckConstraint("observations > 0", name="observations_positive"),
        CheckConstraint("helmet_rate BETWEEN 0 AND 100", name="helmet_rate_range"),
        CheckConstraint("vest_rate BETWEEN 0 AND 100", name="vest_rate_range"),
        CheckConstraint("pid >= 0", name="pid_non_negative"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("detector_runs.id", ondelete="RESTRICT"),
                                              primary_key=True)
    pid: Mapped[int] = mapped_column(Integer, primary_key=True)
    first_sec: Mapped[Decimal] = mapped_column(Numeric(10, 1))
    last_sec: Mapped[Decimal] = mapped_column(Numeric(10, 1))
    observations: Mapped[int] = mapped_column(Integer)
    helmet_rate: Mapped[int] = mapped_column(SmallInteger)
    vest_rate: Mapped[int] = mapped_column(SmallInteger)
    ever_violation: Mapped[bool] = mapped_column(Boolean)
    updated_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())
