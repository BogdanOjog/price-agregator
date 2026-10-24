"""Read queries for health reports."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pisaprices.domain.enums import ReviewStatus
from pisaprices.storage.orm import ReviewItem, ScrapeRun


def last_run(session: Session, source: str) -> dict | None:
    run = session.scalar(select(ScrapeRun).where(ScrapeRun.source == source).order_by(ScrapeRun.id.desc()).limit(1))
    if run is None:
        return None
    return {c: getattr(run, c) for c in ("id", "started_at", "finished_at", "status", "items", "requests", "error")}


def open_review_counts(session: Session) -> dict[str, int]:
    rows = session.execute(
        select(ReviewItem.kind, func.count()).where(ReviewItem.status == ReviewStatus.OPEN.value).group_by(ReviewItem.kind)
    )
    return dict(rows.all())
