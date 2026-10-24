"""Canonical products: rows keyed by (category, format, tier, flags)."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from pisaprices.storage.orm import CanonicalProduct, Product


def products_with_category(session: Session) -> list[dict]:
    rows = session.execute(select(Product.id, Product.chain, Product.brand, Product.name, Product.net_qty, Product.unit,
                                  Product.variable_weight, Product.category_id, Product.canonical_id)
                           .where(Product.category_id.is_not(None)))
    return [dict(row._mapping) for row in rows]


def canonical_id(session: Session, category_id: int, fmt: str | None, tier: int | None, flags: list[str]) -> int:
    for row in session.scalars(select(CanonicalProduct).where(CanonicalProduct.category_id == category_id,
                                                              CanonicalProduct.format.is_(fmt) if fmt is None else CanonicalProduct.format == fmt,
                                                              CanonicalProduct.tier.is_(tier) if tier is None else CanonicalProduct.tier == tier)):
        if sorted(row.flags or []) == sorted(flags):
            return row.id
    row = CanonicalProduct(category_id=category_id, format=fmt, tier=tier, flags=sorted(flags))
    session.add(row)
    session.flush()
    return row.id


def set_canonical(session: Session, product_id: int, canonical: int) -> None:
    session.get(Product, product_id).canonical_id = canonical


def products_without_category(session: Session) -> list[dict]:
    rows = session.execute(select(Product.id, Product.chain, Product.name, Product.brand).where(Product.category_id.is_(None)))
    return [dict(row._mapping) for row in rows]


def set_category(session: Session, product_id: int, category_id: int) -> None:
    session.get(Product, product_id).category_id = category_id
