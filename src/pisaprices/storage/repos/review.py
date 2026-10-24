"""Product linking for promotion items, and the review queue."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from pisaprices.domain.enums import ReviewKind, ReviewStatus
from pisaprices.storage.orm import Product, PromotionItem, ReviewItem


def unresolved_items(session: Session) -> list[dict]:
    rows = session.scalars(select(PromotionItem).where(PromotionItem.product_id.is_(None)))
    return [{"promotion_id": r.promotion_id, "ref_key": r.ref_key, "ref": r.ref} for r in rows]


def products_of_chain(session: Session, chain: str) -> list[dict]:
    rows = session.execute(
        select(Product.id, Product.chain, Product.external_id, Product.ean, Product.name, Product.net_qty, Product.unit)
        .where(Product.chain == chain)
    )
    return [dict(row._mapping) for row in rows]


def link_item(session: Session, promotion_id: int, ref_key: str, product_id: int) -> None:
    session.get(PromotionItem, (promotion_id, ref_key)).product_id = product_id


def add_review(session: Session, kind: ReviewKind, subject_table: str, subject_id: int, key: str,
               payload: dict, created_at: str) -> bool:
    """Open a review item unless an open one with the same kind and key exists. Returns True if added."""
    existing = session.scalars(select(ReviewItem).where(
        ReviewItem.kind == kind.value, ReviewItem.status == ReviewStatus.OPEN.value))
    if any(item.payload.get("key") == key for item in existing):
        return False
    session.add(ReviewItem(kind=kind.value, subject_table=subject_table, subject_id=subject_id,
                           payload={"key": key, **payload}, created_at=created_at))
    return True


def open_reviews(session: Session, kind: ReviewKind | None = None) -> list[dict]:
    query = select(ReviewItem).where(ReviewItem.status == ReviewStatus.OPEN.value).order_by(ReviewItem.id)
    if kind is not None:
        query = query.where(ReviewItem.kind == kind.value)
    return [{"id": r.id, "kind": r.kind, "subject_table": r.subject_table, "subject_id": r.subject_id,
             "payload": r.payload, "created_at": r.created_at} for r in session.scalars(query)]


def decide(session: Session, review_id: int, status: ReviewStatus, decided_at: str) -> dict:
    item = session.get(ReviewItem, review_id)
    if item is None or item.status != ReviewStatus.OPEN.value:
        raise ValueError(f"review {review_id} does not exist or is already decided")
    item.status, item.decided_at = status.value, decided_at
    return {"kind": item.kind, "subject_table": item.subject_table, "subject_id": item.subject_id, "payload": item.payload}


def keys_of_kind(session: Session, kind: ReviewKind) -> set[str]:
    """Keys of every review item of this kind, open or decided."""
    return {item.payload.get("key") for item in session.scalars(select(ReviewItem).where(ReviewItem.kind == kind.value))}


def drop_settled_category_proposals(session: Session) -> int:
    """Open category proposals whose product got a category meanwhile (a later run placed it).
    Nobody decided them, so they are removed rather than kept as decisions."""
    settled = session.scalars(select(ReviewItem).join(Product, Product.id == ReviewItem.subject_id).where(
        ReviewItem.kind == ReviewKind.CATEGORY_RULE.value, ReviewItem.status == ReviewStatus.OPEN.value,
        Product.category_id.is_not(None))).all()
    for item in settled:
        session.delete(item)
    return len(settled)
