"""Reads for planning and shopping lists. Plain dicts out."""

from datetime import date

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from pisaprices.storage.orm import CanonicalProduct, Category, ListItem, ShoppingList

LIST_ITEM_FIELDS = ("label", "match_mode", "gtin", "brand", "canonical_id", "category_id", "min_tier",
                    "required_flags", "name_contains", "qty_needed", "qty_unit", "overbuy_max_units")


def category_parents(session: Session) -> dict[int, int | None]:
    return dict(session.execute(select(Category.id, Category.parent_id)).all())


def category_slugs(session: Session) -> dict[str, int]:
    return dict(session.execute(select(Category.slug, Category.id)).all())


def canonical_rows(session: Session) -> list[dict]:
    rows = session.execute(select(CanonicalProduct.id, CanonicalProduct.category_id, CanonicalProduct.format,
                                  CanonicalProduct.tier, CanonicalProduct.flags))
    return [dict(row._mapping) for row in rows]


def priced_products(session: Session, store_ids: list[int], not_before: str) -> list[dict]:
    """Latest regular price per (product, store) for these stores, if observed on or after `not_before`."""
    if not store_ids:
        return []
    return [dict(row._mapping) for row in session.execute(text(f"""
        SELECT p.id, p.chain, p.name, p.brand, p.ean, p.category_id, p.canonical_id, p.net_qty, p.unit,
               p.variable_weight, cp.tier, cp.flags, pr.price_cents, pr.observed_at, pr.store_id
        FROM current_prices pr
        JOIN products p ON p.id = pr.product_id
        LEFT JOIN canonical_products cp ON cp.id = p.canonical_id
        WHERE pr.store_id IN ({','.join(str(int(i)) for i in store_ids)}) AND substr(pr.observed_at, 1, 10) >= :since
    """), {"since": not_before})]


def products_by_ids(session: Session, ids: list[int]) -> list[dict]:
    if not ids:
        return []
    return [dict(row._mapping) for row in session.execute(text(f"""
        SELECT p.id, p.chain, p.name, p.brand, p.ean, p.category_id, p.canonical_id, p.net_qty, p.unit,
               p.variable_weight, cp.tier, cp.flags
        FROM products p LEFT JOIN canonical_products cp ON cp.id = p.canonical_id
        WHERE p.id IN ({','.join(str(int(i)) for i in ids)})"""))]


def window_promotions(session: Session, start: date, end: date, seen_since: date) -> list[dict]:
    """Linked promotions that may be active in [start, end]. Open-ended ones only if seen recently."""
    rows = session.execute(text("""
        SELECT pr.*, s.slug AS store_slug, MAX(ps.seen_at) AS last_seen,
               (SELECT group_concat(pi.product_id) FROM promotion_items pi WHERE pi.promotion_id = pr.id) AS product_ids
        FROM promotions pr
        JOIN promotion_sightings ps ON ps.promotion_id = pr.id
        LEFT JOIN stores s ON s.id = pr.store_id
        WHERE (pr.valid_from IS NULL OR pr.valid_from <= :end)
          AND NOT EXISTS (SELECT 1 FROM promotion_items pi WHERE pi.promotion_id = pr.id AND pi.product_id IS NULL)
        GROUP BY pr.id
        HAVING (pr.valid_until IS NOT NULL AND pr.valid_until >= :start)
            OR (pr.valid_until IS NULL AND substr(MAX(ps.seen_at), 1, 10) >= :seen)
    """), {"start": start.isoformat(), "end": end.isoformat(), "seen": seen_since.isoformat()})
    return [dict(row._mapping) for row in rows]


def create_list(session: Session, name: str, created_at: str) -> int:
    row = ShoppingList(name=name, created_at=created_at)
    session.add(row)
    session.flush()
    return row.id


def add_item(session: Session, list_id: int, values: dict) -> int:
    row = ListItem(list_id=list_id, **values)
    session.add(row)
    session.flush()
    return row.id


def lists(session: Session) -> list[dict]:
    return [{"id": r.id, "name": r.name, "created_at": r.created_at} for r in session.scalars(select(ShoppingList).order_by(ShoppingList.id))]


def get_list(session: Session, list_id: int) -> dict | None:
    row = session.get(ShoppingList, list_id)
    if row is None:
        return None
    items = session.scalars(select(ListItem).where(ListItem.list_id == list_id).order_by(ListItem.id))
    return {"id": row.id, "name": row.name, "created_at": row.created_at,
            "items": [{"id": i.id, **{f: getattr(i, f) for f in LIST_ITEM_FIELDS}} for i in items]}


def delete_item(session: Session, item_id: int) -> None:
    session.execute(delete(ListItem).where(ListItem.id == item_id))


def delete_list(session: Session, list_id: int) -> None:
    session.execute(delete(ListItem).where(ListItem.list_id == list_id))
    session.execute(delete(ShoppingList).where(ShoppingList.id == list_id))
