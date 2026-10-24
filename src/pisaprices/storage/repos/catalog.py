"""Read queries for the catalog (search, browsing, product page, equivalents, active promotions).

A promotion is active on day d when d is inside its validity; one without an end date (Conad
card offers) only while it was seen within the price-age window. Promotions with an item not yet
linked to a product are never active."""

import re
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

ACTIVE_PROMOTIONS = """
    SELECT pr.*, MAX(ps.seen_at) AS last_seen, MIN(ps.seen_at) AS first_seen
    FROM promotions pr
    JOIN promotion_sightings ps ON ps.promotion_id = pr.id
    WHERE (pr.valid_from IS NULL OR pr.valid_from <= :day)
      AND NOT EXISTS (SELECT 1 FROM promotion_items pi WHERE pi.promotion_id = pr.id AND pi.product_id IS NULL)
    GROUP BY pr.id
    HAVING (pr.valid_until IS NOT NULL AND pr.valid_until >= :day)
        OR (pr.valid_until IS NULL AND substr(MAX(ps.seen_at), 1, 10) >= :horizon)
"""


def fts_query(words: str) -> str:
    """User text -> FTS5 query: every word as a prefix, quoted (no FTS syntax injection)."""
    tokens = re.findall(r"\w+", words.lower())
    return " ".join(f'"{t}"*' for t in tokens)


def _rows(session: Session, sql: str, params: dict) -> list[dict]:
    return [dict(row._mapping) for row in session.execute(text(sql), params)]


def search_products(session: Session, words: str, limit: int = 50) -> list[dict]:
    query = fts_query(words)
    if not query:
        return []
    return _rows(session, """
        SELECT p.id, p.chain, p.external_id, p.name, p.brand, p.net_qty, p.unit, p.variable_weight, p.category_id,
               cp.price_cents, cp.unit_price, cp.observed_at, s.slug AS store_slug
        FROM products_fts f
        JOIN products p ON p.id = f.rowid
        LEFT JOIN current_prices cp ON cp.product_id = p.id
        LEFT JOIN stores s ON s.id = cp.store_id
        WHERE products_fts MATCH :query
        ORDER BY cp.unit_price IS NULL, cp.unit_price, p.name
        LIMIT :limit
    """, {"query": query, "limit": limit})


def category_ids(session: Session, slug: str) -> list[int]:
    """The category and all its descendants."""
    return [row["id"] for row in _rows(session, """
        WITH RECURSIVE tree(id) AS (
            SELECT id FROM categories WHERE slug = :slug
            UNION ALL SELECT c.id FROM categories c JOIN tree t ON c.parent_id = t.id)
        SELECT id FROM tree""", {"slug": slug})]


def active_promotions(session: Session, day: date, max_age_days: int) -> list[dict]:
    params = {"day": day.isoformat(), "horizon": (day - timedelta(days=max_age_days)).isoformat()}
    promos = _rows(session, ACTIVE_PROMOTIONS, params)
    if not promos:
        return []
    items = _rows(session, "SELECT promotion_id, product_id FROM promotion_items", {})
    by_promotion: dict[int, list[int]] = {}
    for item in items:
        by_promotion.setdefault(item["promotion_id"], []).append(item["product_id"])
    stores = dict(session.execute(text("SELECT id, slug FROM stores")).all())
    for promo in promos:
        promo["product_ids"] = by_promotion.get(promo["id"], [])
        promo["store_slug"] = stores.get(promo["store_id"])
    return promos


def unlinked_promotions(session: Session) -> list[dict]:
    return _rows(session, """
        SELECT pr.id, pr.chain, pr.raw_text, pr.valid_until, pi.ref
        FROM promotions pr JOIN promotion_items pi ON pi.promotion_id = pr.id
        WHERE pi.product_id IS NULL ORDER BY pr.id""", {})


def products(session: Session, ids: list[int] | None = None, category: list[int] | None = None,
             canonical: int | None = None) -> list[dict]:
    where, params = ["1 = 1"], {}
    if ids is not None:
        where.append(f"p.id IN ({','.join(str(int(i)) for i in ids) or 'NULL'})")
    if category is not None:
        where.append(f"p.category_id IN ({','.join(str(int(i)) for i in category) or 'NULL'})")
    if canonical is not None:
        where.append("p.canonical_id = :canonical")
        params["canonical"] = canonical
    return _rows(session, f"""
        SELECT p.*, c.slug AS category_slug, cp.tier, cp.flags, cp.format
        FROM products p
        LEFT JOIN categories c ON c.id = p.category_id
        LEFT JOIN canonical_products cp ON cp.id = p.canonical_id
        WHERE {' AND '.join(where)}""", params)


def current_prices(session: Session, product_ids: list[int]) -> list[dict]:
    if not product_ids:
        return []
    return _rows(session, f"""
        SELECT cp.product_id, cp.price_cents, cp.unit_price, cp.observed_at, cp.source, s.slug AS store_slug,
               s.visitable, s.chain
        FROM current_prices cp JOIN stores s ON s.id = cp.store_id
        WHERE cp.product_id IN ({','.join(str(int(i)) for i in product_ids)})""", {})


def price_history(session: Session, product_id: int) -> list[dict]:
    return _rows(session, """
        SELECT po.observed_at, po.price_cents, po.unit_price, s.slug AS store_slug
        FROM price_observations po JOIN stores s ON s.id = po.store_id
        WHERE po.product_id = :id ORDER BY po.observed_at""", {"id": product_id})


def stores(session: Session) -> list[dict]:
    return _rows(session, "SELECT id, slug, chain, name, visitable, price_source_store_id, source_mode FROM stores", {})
