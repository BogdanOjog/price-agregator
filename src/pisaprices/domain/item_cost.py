"""The exact cost of one shopping list item at one store on one day (c_is in the model).

One item = one product bought in n packs, n from ceil(need / pack) to that + overbuy; for each
(product, n) the best single applicable promotion (or none). Extra units are kept only when they
make the item strictly cheaper. Products sold by weight cost price-per-kg × the needed amount."""

import math
import re
from dataclasses import dataclass
from datetime import date

from pisaprices.domain.enums import MatchMode
from pisaprices.domain.model import Need, Offer, Product, Programs, Promo
from pisaprices.domain.money import cost_for_amount
from pisaprices.domain.promo_cost import promo_cost, regular_cost


@dataclass(frozen=True)
class ItemChoice:
    need_id: int
    store: str
    product: Product
    units: int                     # packs (1 for products sold by weight)
    cost_cents: int
    promo: Promo | None
    base_price_cents: int | None
    price_source: str
    observed_at: str | None


def acceptable(need: Need, product: Product) -> bool:
    if need.unit != product.unit:
        return False
    if need.min_tier is not None and (product.tier or 0) < need.min_tier:
        return False
    if not need.required_flags <= product.flags:
        return False
    name = product.name.lower()
    if any(re.search(r"(?<!\w)" + re.escape(word.lower()), name) is None for word in need.name_words):
        return False
    if need.match_mode is MatchMode.EXACT_GTIN:
        return bool(need.gtin) and product.ean == need.gtin
    if need.match_mode is MatchMode.CANONICAL:
        return need.canonical_id is not None and product.canonical_id == need.canonical_id
    in_category = need.category_id is None or need.category_id in product.category_ids
    if need.match_mode is MatchMode.BRAND:
        # "Conad" accepts "CONAD SAPORI E IDEE", "Coop" accepts "COOP -"
        brand = (product.brand or "").strip(" -").lower()
        return in_category and bool(need.brand) and re.match(re.escape(need.brand.strip().lower()) + r"(?!\w)", brand) is not None
    return need.category_id is not None and need.category_id in product.category_ids   # CATEGORY


def best_choice(need: Need, offers: list[Offer], promos: list[Promo], day: date, programs: Programs,
                promo_only: bool) -> ItemChoice | None:
    """Cheapest way to cover `need` at one store on `day`; None if the store cannot."""
    by_product: dict[int, list[Promo]] = {}
    for promo in promos:
        if promo.active_on(day):
            for pid in promo.product_ids:
                by_product.setdefault(pid, []).append(promo)

    best: ItemChoice | None = None
    for offer in offers:
        product = offer.product
        if not acceptable(need, product):
            continue
        candidates = by_product.get(product.id, [])
        for units, cost, promo in _options(need, offer, candidates, programs, promo_only):
            choice = ItemChoice(need.id, offer.store, product, units, cost, promo, offer.base_price_cents,
                                offer.price_source, offer.observed_at)
            if best is None or (cost, units, product.id) < (best.cost_cents, best.units, best.product.id):
                best = choice
    return best


def _options(need: Need, offer: Offer, promos: list[Promo], programs: Programs, promo_only: bool):
    product = offer.product
    base = None if promo_only else offer.base_price_cents
    if product.variable_weight:
        prices = [] if base is None else [(base, None)]
        prices += [(p.params["promo_price_cents"], p) for p in promos
                   if "promo_price_cents" in p.params and promo_cost(p, 1, base, programs) is not None]
        for price, promo in prices:
            yield 1, cost_for_amount(price, need.qty, need.unit), promo
        return
    if not product.net_qty:
        return
    minimum = math.ceil(need.qty / product.net_qty - 1e-9)
    for units in range(minimum, minimum + need.overbuy_max_units + 1):
        if not promo_only:
            cost = regular_cost(units, base)
            if cost is not None:
                yield units, cost, None
        for promo in promos:
            cost = promo_cost(promo, units, base, programs)
            if cost is not None:
                yield units, cost, promo
