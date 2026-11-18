"""Dedektör payload'larının tipli doğrulaması (Pydantic v2).

Sözleşme: detector/protocol.py (FAZ 2). Kimliği (event_id/run_id/incident_id/camera_id/occurred_at) olmayan v1
olayları REDDEDİLİR (422); sunucu rastgele kimlik uydurmaz. Olaysız canlı kareler eskisi gibi kabul edilir.

Sayılar: oranlar/pid/gözlem sayısı StrictInt (12.5 -> 422, sessiz kırpma yok). Saniye alanları (t, first,
last) en fazla 1 ondalık basamak olmalı ve Decimal(repr(float)) ile birebir NUMERIC(10,1)'e çevrilir; daha
hassas değer yuvarlanmaz, reddedilir. Zaman damgaları UTC (Z/+00:00) olmalıdır."""
import json
import math
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import (AwareDatetime, BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr,
                      ValidationError, field_validator, model_validator)

CAMERA_ID = r"^[A-Za-z0-9_-]{1,40}$"
HELMET_TEXT, VEST_TEXT = "Baretsiz", "Yeleksiz"
SOURCE_KINDS = ("file", "webcam", "rtsp", "youtube", "other")
UNCLOSED_REASONS = ("stale", "run_end", "superseded")
# FAZ 10-A: run bitiş nedeni (dedektör sınıflandırması; detector.classify_end)
END_REASONS = ("eof", "user_stop", "source_lost", "source_lost_after_stall", "exception")
RUN_END_STATUS = {"eof": "finished", "user_stop": "interrupted", "source_lost": "disconnected",
                  "source_lost_after_stall": "disconnected", "exception": "error"}
MAX_SECONDS = Decimal("999999999.9")

Rate = Annotated[StrictInt, Field(ge=0, le=100)]
Pid = Annotated[StrictInt, Field(ge=0, le=2_147_483_647)]
Num = StrictInt | StrictFloat
CameraId = Annotated[StrictStr, Field(pattern=CAMERA_ID)]


class PayloadError(ValueError):
    """Doğrulama hatası. errors: girdi değerlerini İÇERMEYEN güvenli hata listesi (snapshot/parola sızmaz)."""
    def __init__(self, errors):
        super().__init__("payload geçersiz")
        self.errors = errors


def tenth(v, field: str) -> Decimal:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ValueError(f"{field} sonlu bir sayı olmalı")
    d = Decimal(repr(float(v)))
    if d != d.quantize(Decimal("0.1")):
        raise ValueError(f"{field} en fazla 1 ondalık basamak olmalı (gelen {v!r})")
    if d < 0 or d > MAX_SECONDS:
        raise ValueError(f"{field} 0..{MAX_SECONDS} aralığında olmalı")
    return d.quantize(Decimal("0.1"))


def _utc(dt: datetime, field: str) -> datetime:
    if dt.utcoffset() != timedelta(0):
        raise ValueError(f"{field} UTC olmalı (Z)")
    return dt.astimezone(timezone.utc)


class EventIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: Literal["start", "ongoing", "end"]
    pid: Pid
    t: Num
    missing: Annotated[StrictStr, Field(max_length=64)]
    helmet_rate: Rate
    vest_rate: Rate
    box: Annotated[list[Num], Field(min_length=4, max_length=4)]
    event_id: uuid.UUID
    run_id: uuid.UUID
    incident_id: uuid.UUID
    camera_id: CameraId
    occurred_at: AwareDatetime
    missing_helmet: StrictBool
    missing_vest: StrictBool
    snapshot_b64: StrictStr | None = None
    orphan: StrictBool = False

    @field_validator("box")
    @classmethod
    def _box(cls, b):
        if not all(math.isfinite(x) and 0 <= x <= 100_000 for x in b):
            raise ValueError("box koordinatları 0..100000 aralığında sonlu sayılar olmalı")
        if b[2] < b[0] or b[3] < b[1]:
            raise ValueError("box [x1, y1, x2, y2] olmalı (x2>=x1, y2>=y1)")
        return b

    @model_validator(mode="after")
    def _consistency(self):
        self.occurred_at = _utc(self.occurred_at, "occurred_at")
        tenth(self.t, "t")
        if self.type == "end":
            if self.missing_helmet or self.missing_vest or self.missing != "":
                raise ValueError("end olayında eksik ekipman olamaz (missing boş, iki boolean false)")
        else:
            if not (self.missing_helmet or self.missing_vest):
                raise ValueError(f"{self.type} olayında en az bir eksik ekipman olmalı")
            parts = self.missing.split(" + ")
            expected = {HELMET_TEXT} if self.missing_helmet else set()
            expected |= {VEST_TEXT} if self.missing_vest else set()
            if len(parts) != len(set(parts)) or set(parts) != expected:
                raise ValueError("missing metni missing_helmet/missing_vest ile uyuşmuyor")
        if self.snapshot_b64 is not None and self.type != "start":
            raise ValueError("snapshot yalnızca start olayına eklenebilir")
        return self

    @property
    def video_time_sec(self) -> Decimal:
        return tenth(self.t, "t")

    def identity(self) -> dict:
        """Tekrar karşılaştırması için kanonik içerik (snapshot içeriği hariç; yalnızca var/yok)."""
        return {"incident_id": self.incident_id, "run_id": self.run_id, "camera_id": self.camera_id,
                "pid": self.pid, "event_type": self.type, "missing_helmet": self.missing_helmet,
                "missing_vest": self.missing_vest, "missing_text": self.missing, "helmet_rate": self.helmet_rate,
                "vest_rate": self.vest_rate, "video_time_sec": self.video_time_sec,
                "occurred_at": self.occurred_at, "box": [float(x) for x in self.box],
                "has_snapshot": self.snapshot_b64 is not None}


class FramePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    cam: StrictStr | None = Field(default=None, max_length=200)
    name: StrictStr | None = Field(default=None, max_length=200)
    meta: dict = Field(default_factory=dict)
    summary: dict | None = None
    people: list = Field(default_factory=list)
    events: list[dict] = Field(default_factory=list)
    run_id: uuid.UUID | None = None


class UnclosedIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    incident_id: uuid.UUID
    pid: Pid
    reason: Literal[UNCLOSED_REASONS]
    t: Num | None = None

    @field_validator("t")
    @classmethod
    def _t(cls, v):
        if v is not None:
            tenth(v, "t")
        return v


class ReportRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    pid: Pid
    first: Num
    last: Num
    n: Annotated[StrictInt, Field(gt=0, le=2_147_483_647)]
    helmet: Rate
    vest: Rate
    violation: StrictBool

    @model_validator(mode="after")
    def _range(self):
        if tenth(self.last, "last") < tenth(self.first, "first"):
            raise ValueError("last >= first olmalı")
        return self


class ReportIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    cam: StrictStr | None = Field(default=None, max_length=200)
    name: StrictStr | None = Field(default=None, max_length=200)
    status: StrictStr | None = Field(default=None, max_length=40)          # eski: dashboard kamera durumu
    report: list[ReportRow]
    run_id: uuid.UUID
    camera_id: CameraId
    run_status: Literal["finished", "error", "interrupted", "disconnected"]
    end_reason: Literal[END_REASONS] | None = None              # eski dedektörler göndermez
    loop: Annotated[StrictInt, Field(ge=1)] | None = None
    source_kind: Literal[SOURCE_KINDS] | None = None
    started_at: AwareDatetime
    finished_at: AwareDatetime
    video_time_sec: Num | None = None
    error: StrictStr | None = Field(default=None, max_length=300)
    unclosed_incidents: Annotated[list[UnclosedIn], Field(max_length=500)] = Field(default_factory=list)
    unclosed_overflow: Annotated[StrictInt, Field(ge=0)] = 0
    orphan_events: Annotated[StrictInt, Field(ge=0)] = 0

    @model_validator(mode="after")
    def _times(self):
        self.started_at = _utc(self.started_at, "started_at")
        self.finished_at = _utc(self.finished_at, "finished_at")
        if self.finished_at < self.started_at:
            raise ValueError("finished_at >= started_at olmalı")
        if self.video_time_sec is not None:
            tenth(self.video_time_sec, "video_time_sec")
        if len({r.pid for r in self.report}) != len(self.report):
            raise ValueError("report içinde aynı pid birden fazla kez var")
        if self.end_reason is not None and RUN_END_STATUS[self.end_reason] != self.run_status:
            raise ValueError(f"end_reason '{self.end_reason}' ile run_status '{self.run_status}' çelişiyor")
        return self


# ---------------------------------------------------------------- ayrıştırma yardımcıları
def _reject_constant(c):
    raise ValueError(f"geçersiz JSON sabiti: {c}")


def load_json(raw, what: str):
    try:
        return json.loads(raw, parse_constant=_reject_constant)
    except (ValueError, TypeError, RecursionError):
        raise PayloadError([{"loc": [what], "msg": "geçerli JSON değil", "type": "json_invalid"}]) from None


def _safe(errors, prefix=()) -> list:
    """Pydantic hatalarından girdi değerlerini (input/ctx/url) çıkarır."""
    out = []
    for e in errors:
        msg = e.get("msg", "geçersiz")
        if "ctx" in e and isinstance(e["ctx"].get("error"), ValueError):
            msg = str(e["ctx"]["error"])
        out.append({"loc": [*prefix, *e.get("loc", ())], "msg": msg, "type": e.get("type", "value_error")})
    return out


def validate(model, data, prefix=()):
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise PayloadError(_safe(exc.errors(include_input=False, include_url=False), prefix)) from None
