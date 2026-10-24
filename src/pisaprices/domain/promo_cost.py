"""Cost of buying n units of one product under one promotion (formula, then modifiers).

Units outside the promotion (beyond a cap) cost the base price; when the store publishes no base
price (promotion-only chains) the promotion's prior price is used, else they are unavailable
(None): a price is never invented."""

from pisaprices.domain.enums import Mechanic
from pisaprices.domain.model import Programs, Promo

USABLE = {Mechanic.PRICE_CUT, Mechanic.MULTIBUY_N_FOR_M, Mechanic.NTH_UNIT_DISCOUNT, Mechanic.TIERED_ALL_UNITS}


def regular_cost(n: int, base_cents: int | None) -> int | None:
    return None if base_cents is None else n * base_cents


def _formula(promo: Promo, n: int, base: int | None) -> int | None:
    """Cost of n units all inside the promotion."""
    p = promo.params
    if promo.mechanic is Mechanic.PRICE_CUT:
        return n * p["promo_price_cents"]
    if base is None:
        return None   # the other formulas discount a price we do not know
    if promo.mechanic is Mechanic.MULTIBUY_N_FOR_M:
        groups = n // p["buy"]
        return base * (n - (p["buy"] - p["pay"]) * groups)
    if promo.mechanic is Mechanic.NTH_UNIT_DISCOUNT:
        groups = n // p["nth"]
        discount = round(base * p["pct"] / 100)
        return base * n - groups * discount
    if promo.mechanic is Mechanic.TIERED_ALL_UNITS:
        price = base
        for tier in p["tiers"]:
            if n >= tier["min_qty"]:
                price = tier["unit_price_cents"]
        return n * price
    return None


def promo_cost(promo: Promo, n: int, base_cents: int | None, programs: Programs) -> int | None:
    """Cost of n units with this promotion, or None if it cannot be used or the units are unavailable."""
    if promo.mechanic not in USABLE or not programs.has(promo.program) or not promo.applies_in_store():
        return None
    if promo.min_qty and n < promo.min_qty:
        return None
    outside_price = base_cents if base_cents is not None else promo.prior_price_cents
    cap = None
    if promo.max_qty_per_card and promo.program:
        cap = promo.max_qty_per_card * programs.n_cards(promo.program)
    if promo.max_qty_per_receipt:
        cap = min(cap, promo.max_qty_per_receipt) if cap else promo.max_qty_per_receipt
    inside = n if cap is None else min(n, cap)
    rest = n - inside
    inside_cost = _formula(promo, inside, outside_price)
    rest_cost = regular_cost(rest, outside_price) if rest else 0
    if inside_cost is None or rest_cost is None:
        return None
    return inside_cost + rest_cost
