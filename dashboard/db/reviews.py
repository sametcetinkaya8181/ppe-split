"""İnsan inceleme kararları (FAZ 10-A.6). Otomatik ihlal kaydı (violation_events/incidents) ile insanın doğrulama
kararı AYRIDIR: bu modül olay tablolarına yazmaz, incident yaşam döngüsünü (history.py durumları) değiştirmez.

Durumlar: UNREVIEWED (incident_reviews satırı yok — eski kayıtların varsayılanı) | CONFIRMED_VIOLATION |
FALSE_ALARM | INCONCLUSIVE. Her karar incident_review_history'ye eklenir (yalnız ekleme) ve güncel durum
incident_reviews'da tutulur.

Eşzamanlılık: iyimser kilit. İstemci gördüğü version'ı (UNREVIEWED = 0) gönderir; güncelleme
`WHERE version = :beklenen` ile atomiktir, ilk karar PK çakışmasıyla korunur. Eşleşmezse ReviewConflict (HTTP 409)
güncel durumla döner. Tekrar gönderim: request_id tekildir; aynı istek yeniden gelirse yeni satır yazılmaz,
güncel durum replayed=true ile döner. Fonksiyonlar çağıranın transaction'ında çalışır."""
import uuid

from sqlalchemy import func, insert, literal, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .models import REVIEW_DECISIONS, AppUser, Incident, IncidentReview, IncidentReviewHistory
from .models import REVIEW_NOTE_MAX as NOTE_MAX

UNREVIEWED = "UNREVIEWED"
REVIEW_STATUSES = (UNREVIEWED, *REVIEW_DECISIONS)
R = IncidentReview.__table__
H = IncidentReviewHistory.__table__
U = AppUser.__table__
INC = Incident.__table__


class ReviewError(ValueError):
    """Geçersiz karar (HTTP 422). code: makine okunur neden."""

    def __init__(self, code: str, msg: str, field: str = "status"):
        super().__init__(msg)
        self.code, self.field = code, field


class ReviewConflict(RuntimeError):
    """Beklenen version güncel değil (HTTP 409). current: güncel inceleme durumu."""

    def __init__(self, current: dict):
        super().__init__("version_conflict")
        self.current = current


def incident_camera(s: Session, incident_id) -> str | None:
    return s.execute(select(INC.c.camera_id).where(INC.c.id == incident_id)).scalar()


def current(s: Session, incident_id, *, lock: bool = False) -> dict:
    q = (select(R.c.status, R.c.version, R.c.reviewed_at, R.c.note, R.c.annotation_candidate, R.c.reviewed_by,
                U.c.username.label("reviewed_by_username"))
         .select_from(R.join(U, U.c.id == R.c.reviewed_by)).where(R.c.incident_id == incident_id))
    if lock:
        q = q.with_for_update(of=R)
    row = s.execute(q).mappings().first()
    if row is None:
        return {"status": UNREVIEWED, "version": 0, "reviewed_at": None, "note": None,
                "annotation_candidate": False, "reviewed_by": None, "reviewed_by_username": None}
    return dict(row)


def replay(s: Session, incident_id, request_id, status: str) -> dict | None:
    """Aynı request_id daha önce işlendiyse güncel durum; farklı incident/karar için kullanılmışsa ReviewError."""
    h = s.execute(select(H.c.incident_id, H.c.status).where(H.c.request_id == request_id)).first()
    if h is None:
        return None
    if h.incident_id != incident_id or h.status != status:
        raise ReviewError("request_id_reused", "request_id başka bir karar için kullanılmış", "request_id")
    return current(s, incident_id)


def decide(s: Session, incident_id, *, status: str, expected_version: int, reviewer_id: int,
           request_id: uuid.UUID, note: str | None = None, annotation_candidate: bool = False) -> dict:
    """Döner: {"review": güncel durum, "previous_status": ..., "replayed": bool}."""
    if status not in REVIEW_DECISIONS:
        raise ReviewError("invalid_status", f"karar şunlardan biri olmalı: {', '.join(REVIEW_DECISIONS)} "
                                            "(UNREVIEWED insan kararı değildir)")
    done = replay(s, incident_id, request_id, status)
    if done is not None:
        return {"review": done, "previous_status": None, "replayed": True}
    cur = current(s, incident_id, lock=True)
    if expected_version != cur["version"]:
        raise ReviewConflict(cur)
    prev = None if cur["status"] == UNREVIEWED else cur["status"]
    if prev is not None:
        if status == prev and annotation_candidate == cur["annotation_candidate"]:
            raise ReviewError("no_change", "karar zaten bu durumda")
        if note is None:
            raise ReviewError("note_required", "mevcut bir kararı değiştirirken gerekçe (note) zorunludur", "note")
    new_version = cur["version"] + 1
    values = {"status": status, "version": new_version, "reviewed_by": reviewer_id, "reviewed_at": func.now(),
              "note": note, "annotation_candidate": annotation_candidate}
    if prev is None:
        done = s.execute(pg_insert(R).values(incident_id=incident_id, **values)
                         .on_conflict_do_nothing(index_elements=[R.c.incident_id]).returning(R.c.version)).first()
    else:
        done = s.execute(update(R).where(R.c.incident_id == incident_id, R.c.version == expected_version)
                         .values(**values).returning(R.c.version)).first()
    if done is None:                              # eşzamanlı başka karar araya girdi
        raise ReviewConflict(current(s, incident_id))
    s.execute(insert(H).values(incident_id=incident_id, version=new_version, status=status, previous_status=prev,
                               reviewer_id=reviewer_id, note=note, annotation_candidate=annotation_candidate,
                               request_id=request_id))
    return {"review": current(s, incident_id), "previous_status": prev, "replayed": False}


def history(s: Session, incident_id) -> list:
    rows = s.execute(select(H.c.version, H.c.status, H.c.previous_status, H.c.decided_at, H.c.note,
                            H.c.annotation_candidate, U.c.username.label("reviewer"))
                     .select_from(H.join(U, U.c.id == H.c.reviewer_id))
                     .where(H.c.incident_id == incident_id).order_by(H.c.version)).mappings().all()
    return [dict(r) for r in rows]


def review_columns(incident_id_col):
    """history._incident_rows için: incident başına güncel inceleme (LEFT JOIN; satır yoksa UNREVIEWED)."""
    ru = U.alias("review_user")
    sub = (select(R.c.incident_id, R.c.status, R.c.version, R.c.reviewed_at, R.c.note, R.c.annotation_candidate,
                  ru.c.username.label("reviewed_by_username"))
           .select_from(R.join(ru, ru.c.id == R.c.reviewed_by)).subquery("rev"))
    cols = [func.coalesce(sub.c.status, literal(UNREVIEWED)).label("review_status"),
            func.coalesce(sub.c.version, 0).label("review_version"), sub.c.reviewed_at,
            sub.c.reviewed_by_username, sub.c.note.label("review_note"),
            func.coalesce(sub.c.annotation_candidate, False).label("annotation_candidate")]
    return sub, sub.c.incident_id == incident_id_col, cols
