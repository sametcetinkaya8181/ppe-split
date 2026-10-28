"""Geçmiş sorguları (FAZ 4). Yalnızca OKUR; şema değişikliği yoktur.

Temel birim incident'tır: aynı incident_id'yi taşıyan start -> ongoing... -> end olayları TEK ihlaldir.
Olay (violation_events satırı) sayısı ile ihlal (incident) sayısı ayrı raporlanır; ongoing tekrarları ihlal
sayısını artırmaz.

Incident alanları olaylardan türetilir (violation_events GROUP BY incident_id):
  source_label: run'ın kendi kaynak etiketi (0003; run başında donar). NULL = eski kayıt, kaynak doğrulanamadı;
                cameras.name GÜNCEL addır ve geçmişe ait gibi kullanılmaz.
  started_at  : incident'ın ilk olayının occurred_at'i (UTC). Sıralama, tarih filtresi ve sayfalama anahtarı.
                Bir incident başladığı döneme aittir: tarih aralığı başlangıca göre uygulanır.
  ended_at    : end olayının occurred_at'i (yoksa NULL).
  missing_*   : incident boyunca (start + ongoing) en az bir kez eksik görülen ekipman (bool_or). ppe_core
                bir ihlal sürerken eksik ekipman kümesini değiştirebilir; başlangıçtaki küme ayrıca verilir.

Durum (status) — öncelik sırasıyla:
  closed  : start ve end olayı var.
  orphan  : start olayı yok (dedektör aktif incident yokken ongoing/end gördü).
  active  : end yok, run 'running', run'ın son canlılık kaydı VE incident'ın son olayı ACTIVE_WINDOW içinde.
  stale   : end yok, run tamamlanmış ve raporun unclosed_incidents listesinde reason='stale'.
  run_end : end yok, run tamamlanmış ve raporda reason='run_end'.
  unknown : diğer her şey; status_reason nedenini söyler:
            heartbeat_lost (run running ama canlılık eski), no_recent_event (run canlı, incident sessiz:
            PPEMonitor kişiyi unutmuş olabilir; neden ancak run raporunda kesinleşir), superseded,
            missing_metadata (run tamamlanmış, rapor metadata'sı yok), metadata_overflow (incident raporda
            yok ve rapor listesi taşmış), not_reported (incident raporda yok, taşma da yok).
Tüm durumlar SQL'de hesaplanır; böylece filtre, sayfalama ve özet aynı tanımı kullanır."""
import base64
import binascii
import json
import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import Numeric, String, and_, case, cast, false, func, literal, not_, or_, select, text, tuple_
from sqlalchemy.orm import Session

from .models import Camera, DetectorRun, Incident, PersonRunReport, ViolationEvent

STATUSES = ("closed", "active", "stale", "run_end", "orphan", "unknown")
EQUIPMENT = ("helmet", "vest", "both", "helmet_only", "vest_only")
MAX_TIMELINE = 2000
MAX_DAYS = 400

E = ViolationEvent.__table__
INC = Incident.__table__
RUN = DetectorRun.__table__
CAM = Camera.__table__
PRR = PersonRunReport.__table__


class BadCursor(ValueError):
    """Çözülemeyen/kurcalanmış sayfalama imleci (HTTP 422)."""


# ---------------------------------------------------------------- imleç (keyset pagination)
def encode_cursor(at: datetime, key) -> str:
    raw = json.dumps({"t": at.astimezone(timezone.utc).isoformat(), "k": str(key)}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        if len(cursor) > 200:
            raise ValueError
        raw = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        at = datetime.fromisoformat(raw["t"])
        if at.utcoffset() is None:
            raise ValueError
        return at, uuid.UUID(raw["k"])
    except (ValueError, KeyError, TypeError, binascii.Error, json.JSONDecodeError):
        raise BadCursor("geçersiz cursor") from None


# ---------------------------------------------------------------- incident türetme
def _incident_rows(*, camera_id=None, run_id=None, date_from=None, date_to=None, incident_id=None,
                   active_window: float, camera_scope=None):
    """Her incident için türetilmiş alanlar + durum. Döner: alias'lı subquery."""
    start, end = E.c.event_type == "start", E.c.event_type == "end"
    agg = select(
        E.c.incident_id,
        func.min(E.c.occurred_at).label("started_at"),
        func.max(E.c.occurred_at).label("last_event_at"),
        func.max(E.c.ingested_at).label("last_ingested_at"),
        func.min(E.c.occurred_at).filter(end).label("ended_at"),
        func.count().label("event_count"),
        func.count().filter(E.c.event_type == "ongoing").label("ongoing_count"),
        func.count().filter(start).label("start_count"),
        func.count().filter(end).label("end_count"),
        func.bool_or(E.c.missing_helmet).label("missing_helmet"),
        func.bool_or(E.c.missing_vest).label("missing_vest"),
        func.coalesce(func.bool_or(E.c.missing_helmet).filter(start), false()).label("start_missing_helmet"),
        func.coalesce(func.bool_or(E.c.missing_vest).filter(start), false()).label("start_missing_vest"),
        func.max(E.c.snapshot_path).label("snapshot_path"),
        func.min(E.c.video_time_sec).label("video_first_sec"),
        func.max(E.c.video_time_sec).label("video_last_sec"),
    ).group_by(E.c.incident_id)
    if camera_id is not None:
        agg = agg.where(E.c.camera_id == camera_id)
    if run_id is not None:
        agg = agg.where(E.c.run_id == run_id)
    if incident_id is not None:
        agg = agg.where(E.c.incident_id == incident_id)
    if camera_scope is not None:                  # nesne düzeyi yetki: kapsam dışı kamera hiç görünmez
        agg = agg.where(E.c.camera_id.in_(list(camera_scope)))
    if date_from is not None or date_to is not None:
        # aday incident'lar: aralıkta en az bir olayı olanlar (indeksli dar tarama); toplama yine TÜM olaylarıyla
        cand = select(E.c.incident_id)
        if date_from is not None:
            cand = cand.where(E.c.occurred_at >= date_from)
        if date_to is not None:
            cand = cand.where(E.c.occurred_at < date_to)
        if camera_id is not None:
            cand = cand.where(E.c.camera_id == camera_id)
        agg = agg.where(E.c.incident_id.in_(cand))
    agg = agg.subquery("agg")

    md = RUN.c.report_metadata
    unclosed = md["unclosed_incidents"]

    def listed(reason):
        return unclosed.op("@>", is_comparison=True)(func.jsonb_build_array(func.jsonb_build_object(
            literal("incident_id", String), cast(INC.c.id, String), literal("reason", String), literal(reason, String))))

    overflow = case((func.jsonb_typeof(md["unclosed_overflow"]) == "number",
                     cast(md["unclosed_overflow"].astext, Numeric)), else_=0)
    cutoff = func.now() - timedelta(seconds=active_window)
    has_start, has_end = agg.c.start_count > 0, agg.c.end_count > 0
    run_fresh = RUN.c.last_seen_at >= cutoff
    inc_fresh = agg.c.last_ingested_at >= cutoff
    running = RUN.c.status == "running"

    status = case(
        (and_(has_start, has_end), "closed"),
        (not_(has_start), "orphan"),
        (running, case((and_(run_fresh, inc_fresh), "active"), else_="unknown")),
        (listed("stale"), "stale"),
        (listed("run_end"), "run_end"),
        else_="unknown")
    reason = case(
        (and_(has_start, has_end), "end_event"),
        (not_(has_start), "no_start_event"),
        (and_(running, run_fresh, inc_fresh), "recent_activity"),
        (and_(running, or_(RUN.c.last_seen_at.is_(None), not_(run_fresh))), "heartbeat_lost"),
        (running, "no_recent_event"),
        (listed("stale"), "stale"),
        (listed("run_end"), "run_end"),
        (listed("superseded"), "superseded"),
        (md.is_(None), "missing_metadata"),
        (overflow > 0, "metadata_overflow"),
        else_="not_reported")

    missing_h, missing_v = agg.c.missing_helmet, agg.c.missing_vest
    return (select(
        INC.c.id.label("incident_id"), INC.c.run_id, INC.c.camera_id, INC.c.pid,
        CAM.c.name.label("camera_current_name"), RUN.c.source_label, RUN.c.source_kind, RUN.c.source_ref,
        RUN.c.status.label("run_status"), RUN.c.loop_no, RUN.c.started_at.label("run_started_at"),
        RUN.c.finished_at.label("run_finished_at"), RUN.c.last_seen_at.label("run_last_seen_at"),
        agg.c.started_at, agg.c.ended_at, agg.c.last_event_at, agg.c.event_count, agg.c.ongoing_count,
        agg.c.start_count, agg.c.end_count, missing_h, missing_v, agg.c.start_missing_helmet,
        agg.c.start_missing_vest, agg.c.snapshot_path, agg.c.video_first_sec, agg.c.video_last_sec,
        has_start.label("verified"), status.label("status"), reason.label("status_reason"))
        .select_from(agg.join(INC, INC.c.id == agg.c.incident_id)
                     .join(RUN, RUN.c.id == INC.c.run_id)
                     .join(CAM, CAM.c.id == INC.c.camera_id))
        .where(*([agg.c.started_at >= date_from] if date_from is not None else []),
               *([agg.c.started_at < date_to] if date_to is not None else []))
        .subquery("inc"))


def _equipment_clause(q, equipment):
    h, v = q.c.missing_helmet, q.c.missing_vest
    return {"helmet": h, "vest": v, "both": and_(h, v), "helmet_only": and_(h, not_(v)),
            "vest_only": and_(v, not_(h))}[equipment]


def list_incidents(s: Session, *, camera_id=None, run_id=None, date_from=None, date_to=None, statuses=(),
                   equipment=None, limit=50, cursor=None, active_window: float, camera_scope=None) -> dict:
    q = _incident_rows(camera_id=camera_id, run_id=run_id, date_from=date_from, date_to=date_to,
                       active_window=active_window, camera_scope=camera_scope)
    conds = []
    if statuses:
        conds.append(q.c.status.in_(list(statuses)))
    if equipment:
        conds.append(_equipment_clause(q, equipment))
    total = s.execute(select(func.count()).select_from(q).where(*conds)).scalar_one()
    if cursor:
        at, key = decode_cursor(cursor)
        conds.append(tuple_(q.c.started_at, q.c.incident_id) < tuple_(literal(at), literal(key)))
    got = s.execute(select(q).where(*conds).order_by(q.c.started_at.desc(), q.c.incident_id.desc())
                    .limit(limit + 1)).mappings().all()
    page = [dict(r) for r in got[:limit]]
    nxt = encode_cursor(page[-1]["started_at"], page[-1]["incident_id"]) if len(got) > limit else None
    return {"items": page, "total": total, "next_cursor": nxt}


def get_incident(s: Session, incident_id, *, active_window: float, camera_scope=None) -> dict | None:
    q = _incident_rows(incident_id=incident_id, active_window=active_window, camera_scope=camera_scope)
    row = s.execute(select(q)).mappings().first()
    if row is None:
        return None
    out = dict(row)
    events = s.execute(
        select(E.c.event_id, E.c.event_type, E.c.occurred_at, E.c.ingested_at, E.c.video_time_sec,
               E.c.missing_helmet, E.c.missing_vest, E.c.missing_text, E.c.helmet_rate, E.c.vest_rate, E.c.box,
               E.c.snapshot_path)
        .where(E.c.incident_id == incident_id).order_by(E.c.occurred_at, E.c.id).limit(MAX_TIMELINE + 1)
    ).mappings().all()
    out["timeline"] = [dict(e) for e in events[:MAX_TIMELINE]]
    out["timeline_truncated"] = len(events) > MAX_TIMELINE
    md = s.execute(select(RUN.c.report_metadata).where(RUN.c.id == row["run_id"])).scalar_one()
    out["report_metadata"] = md
    return out


# ---------------------------------------------------------------- özet / analitik
def summary(s: Session, *, camera_id=None, run_id=None, date_from=None, date_to=None, tz="UTC",
            active_window: float, camera_scope=None) -> dict:
    q = _incident_rows(camera_id=camera_id, run_id=run_id, date_from=date_from, date_to=date_to,
                       active_window=active_window, camera_scope=camera_scope)
    h, v, ver = q.c.missing_helmet, q.c.missing_vest, q.c.verified
    tot = s.execute(select(
        func.count().label("incidents"),
        func.count().filter(ver).label("verified"),
        func.count().filter(and_(ver, h)).label("helmet"),
        func.count().filter(and_(ver, v)).label("vest"),
        func.count().filter(and_(ver, h, v)).label("both"),
        func.count().filter(and_(ver, h, not_(v))).label("helmet_only"),
        func.count().filter(and_(ver, v, not_(h))).label("vest_only"),
    ).select_from(q)).mappings().one()
    by_status = dict.fromkeys(STATUSES, 0) | {r.status: r.n for r in s.execute(
        select(q.c.status, func.count().label("n")).group_by(q.c.status))}

    cams = s.execute(select(
        q.c.camera_id, func.min(q.c.camera_current_name).label("camera_name"),
        func.count().label("incidents"), func.count().filter(ver).label("verified"),
        func.count().filter(and_(ver, h)).label("helmet"), func.count().filter(and_(ver, v)).label("vest"),
        func.count().filter(q.c.status == "active").label("active"),
    ).group_by(q.c.camera_id).order_by(func.count().filter(ver).desc(), q.c.camera_id)).mappings().all()

    day = func.date(func.timezone(tz, q.c.started_at))
    daily = {r.day: r for r in s.execute(select(
        day.label("day"), func.count().filter(ver).label("verified"),
        func.count().filter(and_(ver, h)).label("helmet"), func.count().filter(and_(ver, v)).label("vest"),
    ).group_by(day).order_by(day)).mappings()}

    # olay sayıları: olayın kendi occurred_at'ine göre (incident sayısından bağımsız)
    ev_conds = [c for c in ((E.c.camera_id == camera_id) if camera_id is not None else None,
                            (E.c.run_id == run_id) if run_id is not None else None,
                            (E.c.occurred_at >= date_from) if date_from is not None else None,
                            (E.c.occurred_at < date_to) if date_to is not None else None,
                            E.c.camera_id.in_(list(camera_scope)) if camera_scope is not None else None)
                if c is not None]
    events = dict.fromkeys(("start", "ongoing", "end"), 0) | {r.event_type: r.n for r in s.execute(
        select(E.c.event_type, func.count().label("n")).where(*ev_conds).group_by(E.c.event_type))}
    cam_ev = {r.camera_id: r.n for r in s.execute(
        select(E.c.camera_id, func.count().label("n")).where(*ev_conds).group_by(E.c.camera_id))}

    return {"incidents": dict(tot), "by_status": by_status,
            "events": {"total": sum(events.values()), **events},
            "by_camera": [dict(c) | {"events": cam_ev.get(c["camera_id"], 0)} for c in cams],
            "daily": _fill_days(daily, date_from, date_to, tz, s)}


def _fill_days(daily: dict, date_from, date_to, tz, s) -> list:
    """Boş günleri 0 ile doldurur (grafik ekseni kesintisiz olsun). Aralık MAX_DAYS ile sınırlı."""
    def local_day(dt):
        return s.execute(select(func.date(func.timezone(tz, literal(dt))))).scalar_one()
    days = sorted(daily)
    first = local_day(date_from) if date_from is not None else (days[0] if days else None)
    last = local_day(date_to - timedelta(microseconds=1)) if date_to is not None else (days[-1] if days else None)
    if first is None or last is None or last < first or (last - first).days >= MAX_DAYS:
        return [_day(d, daily[d]) for d in days]
    out, d = [], first
    while d <= last:
        out.append(_day(d, daily.get(d)))
        d += timedelta(days=1)
    return out


def _day(d: date, r) -> dict:
    return {"day": d.isoformat(), "verified": r.verified if r else 0, "helmet": r.helmet if r else 0,
            "vest": r.vest if r else 0}


def valid_timezone(s: Session, tz: str) -> bool:
    return s.execute(text("SELECT EXISTS (SELECT 1 FROM pg_timezone_names WHERE name = :tz)"), {"tz": tz}).scalar()


# ---------------------------------------------------------------- kameralar / run'lar
def list_cameras(s: Session, *, active_window: float, camera_scope=None) -> list:
    runs = select(func.count()).where(RUN.c.camera_id == CAM.c.id).scalar_subquery()
    last_run = select(RUN.c.started_at).where(RUN.c.camera_id == CAM.c.id).order_by(
        RUN.c.started_at.desc()).limit(1).scalar_subquery()
    running = select(func.count()).where(RUN.c.camera_id == CAM.c.id, RUN.c.status == "running",
                                         RUN.c.last_seen_at >= func.now() - timedelta(seconds=active_window)
                                         ).scalar_subquery()
    incidents = select(func.count()).where(INC.c.camera_id == CAM.c.id).scalar_subquery()
    events = select(func.count()).where(E.c.camera_id == CAM.c.id).scalar_subquery()
    rows = s.execute(select(CAM.c.id, CAM.c.name, CAM.c.source_kind, CAM.c.created_at, CAM.c.last_seen_at,
                            runs.label("run_count"), last_run.label("last_run_started_at"),
                            running.label("live_runs"), incidents.label("incident_count"),
                            events.label("event_count"))
                     .where(*([CAM.c.id.in_(list(camera_scope))] if camera_scope is not None else []))
                     .order_by(CAM.c.id)).mappings().all()
    return [dict(r) for r in rows]


def _run_select(active_window: float):
    persons = select(func.count()).where(PRR.c.run_id == RUN.c.id).scalar_subquery()
    persons_viol = select(func.count()).where(PRR.c.run_id == RUN.c.id, PRR.c.ever_violation).scalar_subquery()
    events = select(func.count()).where(E.c.run_id == RUN.c.id).scalar_subquery()
    incidents = select(func.count()).where(INC.c.run_id == RUN.c.id).scalar_subquery()
    verified = select(func.count()).where(E.c.run_id == RUN.c.id, E.c.event_type == "start").scalar_subquery()
    liveness = case((RUN.c.status != "running", None),
                    (RUN.c.last_seen_at >= func.now() - timedelta(seconds=active_window), "live"),
                    else_="no_heartbeat")
    return select(RUN.c.id.label("run_id"), RUN.c.camera_id, CAM.c.name.label("camera_current_name"),
                  RUN.c.source_label, RUN.c.source_kind, RUN.c.source_ref, RUN.c.loop_no,
                  RUN.c.status, liveness.label("liveness"), RUN.c.started_at, RUN.c.finished_at,
                  RUN.c.last_seen_at, RUN.c.fps, RUN.c.width, RUN.c.height, RUN.c.report_metadata,
                  persons.label("person_count"), persons_viol.label("persons_with_violation"),
                  incidents.label("incident_count"), verified.label("verified_incidents"),
                  events.label("event_count")).select_from(RUN.join(CAM, CAM.c.id == RUN.c.camera_id))


def list_runs(s: Session, *, camera_id=None, date_from=None, date_to=None, statuses=(), limit=50, cursor=None,
              active_window: float, camera_scope=None) -> dict:
    conds = [RUN.c.camera_id.in_(list(camera_scope))] if camera_scope is not None else []
    if camera_id is not None:
        conds.append(RUN.c.camera_id == camera_id)
    if date_from is not None:
        conds.append(RUN.c.started_at >= date_from)
    if date_to is not None:
        conds.append(RUN.c.started_at < date_to)
    if statuses:
        conds.append(RUN.c.status.in_(list(statuses)))
    total = s.execute(select(func.count()).select_from(RUN).where(*conds)).scalar_one()
    if cursor:
        at, key = decode_cursor(cursor)
        conds.append(tuple_(RUN.c.started_at, RUN.c.id) < tuple_(literal(at), literal(key)))
    got = s.execute(_run_select(active_window).where(*conds)
                    .order_by(RUN.c.started_at.desc(), RUN.c.id.desc()).limit(limit + 1)).mappings().all()
    page = [dict(r) for r in got[:limit]]
    nxt = encode_cursor(page[-1]["started_at"], page[-1]["run_id"]) if len(got) > limit else None
    return {"items": page, "total": total, "next_cursor": nxt}


def get_run(s: Session, run_id, *, active_window: float, camera_scope=None) -> dict | None:
    row = s.execute(_run_select(active_window).where(
        RUN.c.id == run_id, *([RUN.c.camera_id.in_(list(camera_scope))] if camera_scope is not None else []))
    ).mappings().first()
    if row is None:
        return None
    out = dict(row)
    out["persons"] = [dict(r) for r in s.execute(
        select(PRR.c.pid, PRR.c.first_sec, PRR.c.last_sec, PRR.c.observations, PRR.c.helmet_rate,
               PRR.c.vest_rate, PRR.c.ever_violation, PRR.c.updated_at)
        .where(PRR.c.run_id == run_id).order_by(PRR.c.pid)).mappings()]
    q = _incident_rows(run_id=run_id, active_window=active_window, camera_scope=camera_scope)
    out["incidents_by_status"] = dict.fromkeys(STATUSES, 0) | {r.status: r.n for r in s.execute(
        select(q.c.status, func.count().label("n")).group_by(q.c.status))}
    return out


def snapshot_camera(s: Session, snapshot_path: str) -> str | None:
    """Snapshot'ın ait olduğu kamera (yetki denetimi için); DB'de referansı yoksa None."""
    return s.execute(select(E.c.camera_id).where(E.c.snapshot_path == snapshot_path).limit(1)).scalar()
