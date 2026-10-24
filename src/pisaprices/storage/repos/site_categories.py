"""Categories as each site names them (refreshed on demand, read by the category setup screen)."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from pisaprices.storage.orm import SourceCategory


def upsert(session: Session, source: str, categories: dict[str, str], seen_at: str) -> int:
    existing = {row.source_key: row for row in session.scalars(select(SourceCategory).where(SourceCategory.source == source))}
    for key, name in categories.items():
        row = existing.get(key)
        if row is None:
            session.add(SourceCategory(source=source, source_key=key, name=name, last_fetched_at=seen_at))
        else:
            row.name, row.last_fetched_at = name, seen_at
    return len(categories)


def key_id(session: Session, source: str, key: str) -> int:
    """The row of a site category key, added (without a name) the first time a product carries it."""
    row = session.scalar(select(SourceCategory).where(SourceCategory.source == source, SourceCategory.source_key == key))
    if row is None:
        row = SourceCategory(source=source, source_key=key)
        session.add(row)
        session.flush()
    return row.id


def listed(session: Session, source: str) -> list[dict]:
    rows = session.scalars(select(SourceCategory).where(SourceCategory.source == source).order_by(SourceCategory.source_key))
    return [{"key": r.source_key, "name": r.name, "seen": r.last_fetched_at} for r in rows]
