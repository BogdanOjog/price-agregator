"""Writes done by a collection run. Plain values in and out; no ORM object leaves storage."""

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from pisaprices.domain.enums import RunStatus
from pisaprices.storage.orm import (
    Category,
    PriceObservation,
    Product,
    Promotion,
    PromotionItem,
    PromotionSighting,
    ScrapeRun,
    Store,
)


def start_run(session: Session, source: str, started_at: str) -> int:
    run = ScrapeRun(source=source, started_at=started_at, status=RunStatus.RUNNING.value)
    session.add(run)
    session.flush()
    return run.id


def finish_run(session: Session, run_id: int, *, status: RunStatus, finished_at: str, items: int | None,
               requests: int | None, error: str | None) -> None:
    run = session.get(ScrapeRun, run_id)
    run.status, run.finished_at, run.items, run.error = status.value, finished_at, items, error
    if requests is not None:
        run.requests = requests


def run_info(session: Session, run_id: int) -> dict | None:
    run = session.get(ScrapeRun, run_id)
    if run is None:
        return None
    return {c: getattr(run, c) for c in ("id", "source", "started_at", "finished_at", "status", "items", "requests", "error")}


def previous_items(session: Session, source: str, before_run_id: int) -> int | None:
    """Items of the latest successful (ok or partial) run of the source before this one."""
    return session.scalar(
        select(ScrapeRun.items)
        .where(ScrapeRun.source == source, ScrapeRun.id < before_run_id,
               ScrapeRun.status.in_([RunStatus.OK.value, RunStatus.PARTIAL.value]))
        .order_by(ScrapeRun.id.desc())
        .limit(1)
    )


def ids_by_slug(session: Session) -> tuple[dict[str, int], dict[str, int]]:
    stores = dict(session.execute(select(Store.slug, Store.id)).all())
    categories = dict(session.execute(select(Category.slug, Category.id)).all())
    return stores, categories


def upsert_product(session: Session, chain: str, external_id: str, fields: dict) -> int:
    product = session.scalar(select(Product).where(Product.chain == chain, Product.external_id == external_id))
    if product is None:
        product = Product(chain=chain, external_id=external_id, **fields)
        session.add(product)
    else:
        for name, value in fields.items():
            if value is not None and getattr(product, name) != value:
                setattr(product, name, value)
    session.flush()
    return product.id


def product_id(session: Session, chain: str, external_id: str) -> int | None:
    return session.scalar(select(Product.id).where(Product.chain == chain, Product.external_id == external_id))


def add_price(session: Session, **values) -> None:
    session.add(PriceObservation(**values))


def upsert_promotion(session: Session, natural_key: str, fields: dict) -> int:
    """Insert the promotion the first time it is seen; later runs only add sightings."""
    promotion_id = session.scalar(select(Promotion.id).where(Promotion.natural_key == natural_key))
    if promotion_id is None:
        promotion = Promotion(natural_key=natural_key, **fields)
        session.add(promotion)
        session.flush()
        promotion_id = promotion.id
    return promotion_id


def add_sighting(session: Session, promotion_id: int, run_id: int, seen_at: str) -> None:
    if session.get(PromotionSighting, (promotion_id, run_id)) is None:
        session.add(PromotionSighting(promotion_id=promotion_id, run_id=run_id, seen_at=seen_at))


def add_promotion_item(session: Session, promotion_id: int, ref_key: str, ref: dict, product: int | None) -> None:
    item = session.get(PromotionItem, (promotion_id, ref_key))
    if item is None:
        session.add(PromotionItem(promotion_id=promotion_id, ref_key=ref_key, ref=ref, product_id=product))
    elif item.product_id is None and product is not None:
        item.product_id = product


def delete_run_rows(session: Session, run_id: int) -> None:
    """Rows derived from one run's snapshots; `reparse` replaces them (the only non-append write)."""
    session.execute(delete(PriceObservation).where(PriceObservation.run_id == run_id))
    session.execute(delete(PromotionSighting).where(PromotionSighting.run_id == run_id))
    session.flush()
