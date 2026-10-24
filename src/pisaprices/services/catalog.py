"""Catalog use cases for the CLI and the UI: browse, search, product page, equivalents, promotions."""

import json
from dataclasses import dataclass, field
from datetime import date, timedelta

from pisaprices.config.schemas import StoreConfig
from pisaprices.domain.money import shelf_estimate
from pisaprices.ingest.dates import rome_date
from pisaprices.services.collect import Runtime
from pisaprices.storage.repos import catalog as repo


@dataclass
class StorePrice:
    store: str
    label: str              # "Coop Cisanello" or "preț online Coop Cascina (punto di ritiro)"
    price_cents: int
    unit_price: float | None
    observed_at: str
    stale: bool


@dataclass
class PromoView:
    id: int
    chain: str
    store: str | None
    mechanic: str
    params: dict
    program: str | None
    user_has_card: bool
    channel: str
    valid_from: str | None
    valid_until: str | None
    expiring: bool
    prior_price_cents: int | None
    raw_text: str
    product_ids: list[int] = field(default_factory=list)


@dataclass
class ProductView:
    id: int
    chain: str
    name: str
    brand: str | None
    net_qty: float | None
    unit: str | None
    ean: str | None
    category: str | None
    tier: int | None
    flags: list[str]
    url: str | None
    prices: list[StorePrice]
    promotions: list[PromoView]

    @property
    def best_unit_price(self) -> float | None:
        values = [p.unit_price for p in self.prices if p.unit_price is not None and not p.stale]
        return min(values) if values else None

    @property
    def best_price_cents(self) -> int | None:
        values = [p.price_cents for p in self.prices if not p.stale]
        promo = [v.params["promo_price_cents"] for v in self.promotions if "promo_price_cents" in v.params and v.user_has_card]
        return min(values + promo) if values or promo else None


def today(runtime: Runtime) -> date:
    return rome_date(runtime.clock.now())


def price_label(source: StoreConfig) -> str:
    """Where a data-only store's price comes from: "preț online Coop Cascina (punto di ritiro)", or,
    for a source with a known markup, "estimat la raft: Lidl Ghezzano (Everli) −8%"."""
    if source.price_markup_pct:
        return f"estimat la raft: {source.name} −{source.price_markup_pct:g}%"
    return f"preț online {source.name}"


def _labels(runtime: Runtime) -> dict[str, str]:
    """Visitable stores by name; data-only stores (Cascina) say where the price comes from."""
    return {s.slug: s.name if s.visitable else price_label(s) for s in runtime.config.stores.stores}


def _shelf(runtime: Runtime, store_slug: str | None, cents: int | None, unit: float | None) -> tuple[int | None, float | None]:
    """Estimated shelf price and unit price, for a source with a known markup (Everli)."""
    markup = _markups(runtime).get(store_slug)
    if not markup or cents is None:
        return cents, unit
    return shelf_estimate(cents, markup), (round(unit / (1 + markup / 100), 3) if unit is not None else None)


def _markups(runtime: Runtime) -> dict[str, float]:
    return {s.slug: s.price_markup_pct for s in runtime.config.stores.stores if s.price_markup_pct}


def _promo_views(runtime: Runtime, promos: list[dict], day: date) -> list[PromoView]:
    held = {p.value for p, cfg in runtime.config.user.programs.items() if cfg.has_card}
    warning = runtime.config.settings.optimizer.expiry_warning_days
    views = []
    for p in promos:
        until = date.fromisoformat(p["valid_until"]) if p["valid_until"] else None
        views.append(PromoView(
            id=p["id"], chain=p["chain"], store=p["store_slug"], mechanic=p["mechanic"],
            params=p["params"] if isinstance(p["params"], dict) else json.loads(p["params"]),
            program=p["program"], user_has_card=p["program"] is None or p["program"] in held, channel=p["channel"],
            valid_from=p["valid_from"], valid_until=p["valid_until"],
            expiring=bool(until and (until - day).days <= warning), prior_price_cents=p["prior_price_cents"],
            raw_text=p["raw_text"], product_ids=p["product_ids"],
        ))
    return views


def _views(runtime: Runtime, session, rows: list[dict], day: date) -> list[ProductView]:
    labels = _labels(runtime)
    max_age = runtime.config.settings.optimizer.max_price_age_days
    horizon = (day - timedelta(days=max_age)).isoformat()
    prices: dict[int, list[StorePrice]] = {}
    for p in repo.current_prices(session, [r["id"] for r in rows]):
        cents, unit = _shelf(runtime, p["store_slug"], p["price_cents"], p["unit_price"])
        prices.setdefault(p["product_id"], []).append(StorePrice(
            p["store_slug"], labels.get(p["store_slug"], p["store_slug"]), cents, unit,
            p["observed_at"], p["observed_at"][:10] < horizon))
    promos = _promo_views(runtime, repo.active_promotions(session, day, max_age), day)
    by_product: dict[int, list[PromoView]] = {}
    for promo in promos:
        for pid in promo.product_ids:
            by_product.setdefault(pid, []).append(promo)
    return [ProductView(
        id=r["id"], chain=r["chain"], name=r["name"], brand=r["brand"], net_qty=r["net_qty"], unit=r["unit"], ean=r["ean"],
        category=r["category_slug"], tier=r["tier"], flags=json.loads(r["flags"]) if isinstance(r["flags"], str) else (r["flags"] or []),
        url=r["product_url"], prices=prices.get(r["id"], []), promotions=by_product.get(r["id"], []),
    ) for r in rows]


SORTS = {
    "unit_price": lambda v: (v.best_unit_price is None, v.best_unit_price or 0, v.name),
    "price": lambda v: (v.best_price_cents is None, v.best_price_cents or 0, v.name),
    "name": lambda v: v.name.lower(),
    "discount": lambda v: (-max((1 - p.params.get("promo_price_cents", 0) / p.prior_price_cents
                                 for p in v.promotions if p.prior_price_cents), default=0), v.name),
}


def browse(runtime: Runtime, category: str, *, chain: str | None = None, promo_only: bool = False,
           brand: str | None = None, tier: int | None = None, flags: list[str] | None = None,
           sort: str = "unit_price", day: date | None = None) -> list[ProductView]:
    day = day or today(runtime)
    with runtime.sessions() as session:
        ids = repo.category_ids(session, category)
        if not ids:
            raise ValueError(f"unknown category '{category}'")
        views = _views(runtime, session, repo.products(session, category=ids), day)
    result = [v for v in views
              if (chain is None or v.chain == chain)
              and (not promo_only or v.promotions)
              and (brand is None or (v.brand or "").lower() == brand.lower())
              and (tier is None or (v.tier or 0) >= tier)
              and all(f in v.flags for f in (flags or []))
              and (v.prices or v.promotions)]
    return sorted(result, key=SORTS[sort])


def search(runtime: Runtime, words: str, limit: int = 50) -> list[dict]:
    with runtime.sessions() as session:
        rows = repo.search_products(session, words, limit)
    for row in rows:
        row["price_cents"], row["unit_price"] = _shelf(runtime, row.get("store_slug"), row["price_cents"], row["unit_price"])
    return rows


@dataclass
class ProductPage:
    product: ProductView
    history: list[dict]
    min_30_days_cents: int | None
    equivalents: list[ProductView]


def product_page(runtime: Runtime, product_id: int, day: date | None = None) -> ProductPage:
    day = day or today(runtime)
    with runtime.sessions() as session:
        rows = repo.products(session, ids=[product_id])
        if not rows:
            raise ValueError(f"no product {product_id}")
        [view] = _views(runtime, session, rows, day)
        history = repo.price_history(session, product_id)
        since = (day - timedelta(days=30)).isoformat()
        recent = [h["price_cents"] for h in history if h["observed_at"][:10] >= since]
        canonical = rows[0]["canonical_id"]
        others = [r for r in repo.products(session, canonical=canonical) if r["chain"] != view.chain] if canonical else []
        equivalents = sorted(_views(runtime, session, others, day), key=SORTS["unit_price"])
    return ProductPage(view, history, min(recent) if recent else None, [e for e in equivalents if e.prices or e.promotions])


def promotions(runtime: Runtime, day: date | None = None, category: str | None = None,
               chain: str | None = None) -> list[tuple[PromoView, list[ProductView]]]:
    day = day or today(runtime)
    with runtime.sessions() as session:
        promos = _promo_views(runtime, repo.active_promotions(session, day, runtime.config.settings.optimizer.max_price_age_days), day)
        ids = {pid for p in promos for pid in p.product_ids}
        allowed = set(repo.category_ids(session, category)) if category else None
        products = {r["id"]: r for r in repo.products(session, ids=list(ids))}
        views = {v.id: v for v in _views(runtime, session, list(products.values()), day)}
    result = []
    for promo in promos:
        if chain and promo.chain != chain:
            continue
        items = [views[pid] for pid in promo.product_ids if pid in views]
        if allowed is not None and not any(products[i.id]["category_id"] in allowed for i in items):
            continue
        result.append((promo, items))
    return sorted(result, key=lambda pair: (pair[0].valid_until or "9999", pair[0].chain))


def unlinked_promotions(runtime: Runtime) -> list[dict]:
    with runtime.sessions() as session:
        return repo.unlinked_promotions(session)
