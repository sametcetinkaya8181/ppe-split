"""Kalıcı yazım katmanı (SQLAlchemy 2 + PostgreSQL INSERT ... ON CONFLICT).

Fonksiyonlar çağıranın açtığı Session/transaction içinde çalışır; commit/rollback çağırana aittir
(her HTTP isteği kendi Session'ını açar, Session thread'ler arasında paylaşılmaz).
Çelişkili tekrar (aynı kimlik, farklı içerik) -> Conflict; çağıran transaction'ı geri alır ve 409 döner.
Kilit sırası: kameralar, run'lar, incident'lar ve olaylar kimliğe göre sıralı yazılır (deadlock riskini azaltır;
yine de oluşursa çağıran 503 ile retry ettirir)."""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .models import Camera, DetectorRun, Incident, PersonRunReport, ViolationEvent

TERMINAL = ("finished", "error", "interrupted")


class Conflict(Exception):
    """Aynı kimlikle çelişkili içerik (HTTP 409)."""


# ---------------------------------------------------------------- kamera / run
def upsert_camera(s: Session, cid: str, name: str, source_kind: str | None = None) -> None:
    stmt = insert(Camera).values(id=cid, name=name or cid, source_kind=source_kind, last_seen_at=func.now())
    ex = stmt.excluded
    s.execute(stmt.on_conflict_do_update(index_elements=[Camera.id], set_={
        "name": ex.name, "source_kind": func.coalesce(ex.source_kind, Camera.source_kind),
        "last_seen_at": ex.last_seen_at}))


def upsert_run(s: Session, run_id, camera_id: str, *, started_at: datetime | None = None, loop_no=None, fps=None,
               width=None, height=None, source_label=None, source_kind=None, source_ref=None) -> None:
    """Eksik metadata ile oluşturulabilir; sonradan gelen değerler yalnızca boşları/güncelleri doldurur
    (NULL hiçbir zaman dolu değeri ezmez). started_at yalnızca geriye çekilir. Durum (status) DEĞİŞMEZ:
    tamamlanmış run running'e dönmez. Kaynak bilgisi (source_*) ilk dolu değerde DONAR; kamera adı sonradan
    değişse de run'ın etiketi değişmez. Run başka kameraya aitse Conflict."""
    values = dict(id=run_id, camera_id=camera_id, status="running", loop_no=loop_no, fps=fps, width=width,
                  height=height, last_seen_at=func.now(), source_label=source_label or None,
                  source_kind=source_kind, source_ref=source_ref or None)
    if started_at is not None:
        values["started_at"] = started_at
    stmt = insert(DetectorRun).values(**values)
    ex = stmt.excluded
    stmt = stmt.on_conflict_do_update(
        index_elements=[DetectorRun.id],
        set_={"loop_no": func.coalesce(DetectorRun.loop_no, ex.loop_no),
              "fps": func.coalesce(ex.fps, DetectorRun.fps),
              "width": func.coalesce(ex.width, DetectorRun.width),
              "height": func.coalesce(ex.height, DetectorRun.height),
              "started_at": func.least(DetectorRun.started_at, ex.started_at),
              "last_seen_at": func.greatest(DetectorRun.last_seen_at, ex.last_seen_at),
              "source_label": func.coalesce(DetectorRun.source_label, ex.source_label),
              "source_kind": func.coalesce(DetectorRun.source_kind, ex.source_kind),
              "source_ref": func.coalesce(DetectorRun.source_ref, ex.source_ref)},
        where=DetectorRun.camera_id == ex.camera_id,
    ).returning(DetectorRun.id)
    if s.execute(stmt).first() is None:
        raise Conflict(f"run {run_id} başka bir kameraya ait")


# ---------------------------------------------------------------- incident / olay
def upsert_incidents(s: Session, rows: list[dict]) -> None:
    """rows: {id, run_id, camera_id, pid}. Var olan incident farklı üçlüye aitse Conflict."""
    if not rows:
        return
    rows = sorted(rows, key=lambda r: str(r["id"]))
    s.execute(insert(Incident).values(rows).on_conflict_do_nothing(index_elements=[Incident.id]))
    have = {r.id: (r.run_id, r.camera_id, r.pid) for r in s.execute(
        select(Incident.id, Incident.run_id, Incident.camera_id, Incident.pid)
        .where(Incident.id.in_([r["id"] for r in rows])))}
    for r in rows:
        if have.get(r["id"]) != (r["run_id"], r["camera_id"], r["pid"]):
            raise Conflict(f"incident {r['id']} başka bir run/kamera/kişiye ait")


def _canonical_row(r) -> dict:
    return {"incident_id": r.incident_id, "run_id": r.run_id, "camera_id": r.camera_id, "pid": r.pid,
            "event_type": r.event_type, "missing_helmet": r.missing_helmet, "missing_vest": r.missing_vest,
            "missing_text": r.missing_text, "helmet_rate": r.helmet_rate, "vest_rate": r.vest_rate,
            "video_time_sec": r.video_time_sec, "occurred_at": r.occurred_at,
            "box": None if r.box is None else [float(x) for x in r.box], "has_snapshot": r.snapshot_path is not None}


def insert_events(s: Session, rows: list[dict], identities: dict) -> set:
    """rows: violation_events sütunları. identities: event_id -> kanonik içerik (EventIn.identity()).
    Döner: GERÇEKTEN yeni eklenen event_id'ler. Var olan event_id içerik olarak farklıysa Conflict."""
    if not rows:
        return set()
    rows = sorted(rows, key=lambda r: str(r["event_id"]))
    stmt = (insert(ViolationEvent).values(rows)
            .on_conflict_do_nothing(index_elements=[ViolationEvent.event_id])
            .returning(ViolationEvent.event_id))
    inserted = set(s.execute(stmt).scalars())
    dups = [r["event_id"] for r in rows if r["event_id"] not in inserted]
    if dups:
        for r in s.execute(select(ViolationEvent).where(ViolationEvent.event_id.in_(dups))).scalars():
            if _canonical_row(r) != identities[r.event_id]:
                raise Conflict(f"event_id {r.event_id} farklı içerikle zaten kayıtlı")
    return inserted


# ---------------------------------------------------------------- rapor
def complete_run(s: Session, *, run_id, camera_id: str, run_status: str, started_at: datetime,
                 finished_at: datetime, loop_no, metadata: dict, rows: list[dict], source_label=None,
                 source_kind=None) -> bool:
    """Run'ı tamamlar ve kişi satırlarını (run_id, pid) üzerinden yazar.
    Döner: True = ilk kez tamamlandı, False = birebir aynı raporun tekrarı (değişiklik yok).
    Run zaten tamamlanmışsa ve gelen rapor (durum, bitiş, metadata, satırlar) farklıysa Conflict."""
    upsert_run(s, run_id, camera_id, started_at=started_at, loop_no=loop_no, source_label=source_label,
               source_kind=source_kind)
    run = s.execute(select(DetectorRun).where(DetectorRun.id == run_id).with_for_update()).scalar_one()
    if run.status in TERMINAL:
        existing = {r.pid: _report_tuple(r) for r in s.execute(
            select(PersonRunReport).where(PersonRunReport.run_id == run_id)).scalars()}
        same = (run.status == run_status and run.finished_at == finished_at
                and (run.report_metadata or {}) == metadata
                and existing == {r["pid"]: _report_tuple_dict(r) for r in rows})
        if not same:
            raise Conflict(f"run {run_id} zaten '{run.status}' olarak farklı bir raporla tamamlanmış")
        return False
    s.execute(update(DetectorRun).where(DetectorRun.id == run_id).values(
        status=run_status, finished_at=finished_at, report_metadata=metadata,
        started_at=func.least(DetectorRun.started_at, started_at, finished_at)))
    if rows:
        stmt = insert(PersonRunReport).values(sorted(rows, key=lambda r: r["pid"]))
        ex = stmt.excluded
        s.execute(stmt.on_conflict_do_update(
            index_elements=[PersonRunReport.run_id, PersonRunReport.pid],
            set_={c: getattr(ex, c) for c in ("first_sec", "last_sec", "observations", "helmet_rate", "vest_rate",
                                              "ever_violation")} | {"updated_at": func.now()}))
    return True


def _report_tuple(r) -> tuple:
    return (Decimal(r.first_sec), Decimal(r.last_sec), r.observations, r.helmet_rate, r.vest_rate, r.ever_violation)


def _report_tuple_dict(r: dict) -> tuple:
    return (r["first_sec"], r["last_sec"], r["observations"], r["helmet_rate"], r["vest_rate"], r["ever_violation"])


# ---------------------------------------------------------------- canlılık (seyrek)
def heartbeat(s: Session, *, camera_id: str, name: str, source_kind=None, run_id=None, loop_no=None, fps=None,
              width=None, height=None, source_label=None, source_ref=None) -> None:
    upsert_camera(s, camera_id, name, source_kind)
    if run_id is not None:
        upsert_run(s, run_id, camera_id, loop_no=loop_no, fps=fps, width=width, height=height,
                   source_label=source_label, source_kind=source_kind, source_ref=source_ref)
