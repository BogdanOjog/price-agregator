import random
import time
from datetime import date, timedelta

import pytest

from pisaprices.domain.basket import StoreOption, best_plans, brute_force
from pisaprices.domain.enums import Channel, MatchMode, Mechanic, Unit, Weekday
from pisaprices.domain.item_cost import ItemChoice, acceptable, best_choice
from pisaprices.domain.model import Need, Offer, Product, Programs, Promo, Visit
from pisaprices.domain.promo_cost import promo_cost
from pisaprices.domain.report import build_reports, shared_caps
from pisaprices.domain.visits import candidate_days, window

DAY = date(2026, 10, 26)   # a Monday
NO_CARDS = Programs()


def product(pid=1, qty=500.0, unit=Unit.GRAM, category=10, tier=2, flags=(), variable=False, brand="B", chain="coop"):
    return Product(pid, chain, f"p{pid}", brand, None, frozenset({category, 1}), None, tier, frozenset(flags), qty, unit, variable)


def offer(prod, price, store="s1"):
    return Offer(prod, store, price, "2026-10-23", "label")


def promo(pid=1, mechanic=Mechanic.PRICE_CUT, params=None, **kw):
    return Promo(id=kw.pop("id", 100 + pid), store=kw.pop("store", "s1"), chain="coop", product_ids=frozenset({pid}),
                 mechanic=mechanic, params=params or {"promo_price_cents": 80}, **kw)


def need(qty=500.0, unit=Unit.GRAM, overbuy=0, nid=1, **kw):
    return Need(nid, "item", kw.pop("mode", MatchMode.CATEGORY), qty, unit, category_id=10, overbuy_max_units=overbuy, **kw)


# --- promo_cost: one test per mechanic and modifier -------------------------------------------

def test_price_cut() -> None:
    assert promo_cost(promo(), 3, 100, NO_CARDS) == 240


def test_multibuy_3x2() -> None:
    p = promo(mechanic=Mechanic.MULTIBUY_N_FOR_M, params={"buy": 3, "pay": 2})
    assert promo_cost(p, 3, 100, NO_CARDS) == 200 and promo_cost(p, 4, 100, NO_CARDS) == 300


def test_nth_unit_discount() -> None:
    p = promo(mechanic=Mechanic.NTH_UNIT_DISCOUNT, params={"nth": 2, "pct": 50})
    assert promo_cost(p, 2, 100, NO_CARDS) == 150 and promo_cost(p, 3, 100, NO_CARDS) == 250


def test_tiered_all_units() -> None:
    p = promo(mechanic=Mechanic.TIERED_ALL_UNITS, params={"tiers": [{"min_qty": 2, "unit_price_cents": 90}, {"min_qty": 4, "unit_price_cents": 80}]})
    assert [promo_cost(p, n, 100, NO_CARDS) for n in (1, 2, 4)] == [100, 180, 320]


@pytest.mark.parametrize("mechanic", [Mechanic.BASKET_THRESHOLD, Mechanic.REWARD, Mechanic.UNKNOWN, Mechanic.TIERED_INCREMENTAL])
def test_unusable_mechanics_are_ignored(mechanic) -> None:
    assert promo_cost(promo(mechanic=mechanic, params={}), 1, 100, NO_CARDS) is None


def test_cap_per_card_with_need_of_four() -> None:
    p = promo(program="coop_socio", max_qty_per_card=2)
    assert promo_cost(p, 4, 100, Programs({"coop_socio": 1})) == 2 * 80 + 2 * 100
    assert promo_cost(p, 4, 100, Programs({"coop_socio": 2})) == 4 * 80   # two cards


def test_cap_without_base_uses_prior_then_unavailable() -> None:
    capped = promo(max_qty_per_receipt=1, prior_price_cents=120)
    assert promo_cost(capped, 3, None, NO_CARDS) == 80 + 2 * 120
    assert promo_cost(promo(max_qty_per_receipt=1), 3, None, NO_CARDS) is None


def test_min_qty() -> None:
    p = promo(min_qty=2)
    assert promo_cost(p, 1, 100, NO_CARDS) is None and promo_cost(p, 2, 100, NO_CARDS) == 160


def test_card_price_ignored_without_card_and_online_promo_ignored() -> None:
    assert promo_cost(promo(program="coop_socio"), 1, 100, NO_CARDS) is None
    assert promo_cost(promo(channel=Channel.ONLINE), 1, 100, NO_CARDS) is None
    assert promo_cost(promo(channel=Channel.BOTH), 1, 100, NO_CARDS) == 80


# --- item_cost --------------------------------------------------------------------------------

def test_3x2_with_need_of_two_takes_third_unit_only_if_overbuy_allowed() -> None:
    p = product()
    multibuy = promo(mechanic=Mechanic.MULTIBUY_N_FOR_M, params={"buy": 3, "pay": 2})
    with_extra = best_choice(need(qty=1000, overbuy=1), [offer(p, 100)], [multibuy], DAY, NO_CARDS, False)
    assert (with_extra.units, with_extra.cost_cents) == (2, 200)   # 3 units would also cost 200: not strictly cheaper
    cheaper = promo(mechanic=Mechanic.MULTIBUY_N_FOR_M, params={"buy": 3, "pay": 1})
    assert best_choice(need(qty=1000, overbuy=1), [offer(p, 100)], [cheaper], DAY, NO_CARDS, False).units == 3
    assert best_choice(need(qty=1000, overbuy=0), [offer(p, 100)], [cheaper], DAY, NO_CARDS, False).units == 2


def test_expired_promotion_ignored() -> None:
    old = promo(valid_until=DAY - timedelta(days=1))
    assert best_choice(need(), [offer(product(), 100)], [old], DAY, NO_CARDS, False).cost_cents == 100


def test_promo_only_store_needs_an_active_promotion() -> None:
    p = product()
    assert best_choice(need(), [offer(p, None)], [], DAY, NO_CARDS, True) is None
    assert best_choice(need(), [offer(p, None)], [promo()], DAY, NO_CARDS, True).cost_cents == 80


def test_variable_weight_compared_per_kg() -> None:
    loose = product(qty=1000, variable=True)
    choice = best_choice(need(qty=1234), [offer(loose, 299)], [], DAY, NO_CARDS, False)
    assert choice.cost_cents == 369 and choice.units == 1


def test_acceptable_filters() -> None:
    p = product(tier=2, flags={"bio"})
    assert acceptable(need(), p)
    assert not acceptable(need(min_tier=3), p)
    assert not acceptable(need(required_flags=frozenset({"gluten_free"})), p)
    assert not acceptable(need(unit=Unit.PIECE), p)
    assert acceptable(need(mode=MatchMode.BRAND, brand="b"), p)
    assert not acceptable(need(mode=MatchMode.EXACT_GTIN, gtin="800"), p)


def test_brand_matches_the_start_of_the_site_brand() -> None:
    conad = need(mode=MatchMode.BRAND, brand="Conad")
    assert acceptable(conad, product(brand="CONAD SAPORI E IDEE")) and acceptable(conad, product(brand="CONAD"))
    assert not acceptable(conad, product(brand="CONADINA")) and not acceptable(conad, product(brand=None))
    assert acceptable(need(mode=MatchMode.BRAND, brand="coop"), product(brand="COOP -"))


def test_cheapest_quantity_combination_rounds_packs_up() -> None:
    small, big = product(1, qty=500), product(2, qty=1000)
    choice = best_choice(need(qty=1200), [offer(small, 100), offer(big, 150)], [], DAY, NO_CARDS, False)
    assert (choice.product.id, choice.units, choice.cost_cents) == (2, 2, 300)


# --- visits -----------------------------------------------------------------------------------

def visit(**kw):
    return Visit(kw.pop("store", "s1"), "coop", "S1", kw.pop("ticket", 0), kw.pop("minutes", 20), **kw)


def test_on_route_day_costs_only_the_detour() -> None:
    v = visit(on_route_days=frozenset({Weekday.TUE}), detour_minutes=3)
    assert v.minutes(DAY + timedelta(days=1)) == 3 and v.minutes(DAY) == 20


def test_allowed_days_restrict_candidates() -> None:
    v = visit(allowed_days=frozenset({Weekday.SAT, Weekday.SUN}))
    assert {d.weekday() for d in candidate_days(v, [], window(DAY, 7))} <= {5, 6}


def test_one_day_promotion_is_its_own_regime() -> None:
    v = visit(on_route_days=frozenset({Weekday.TUE}), detour_minutes=0)
    flash = promo(valid_from=DAY + timedelta(days=3), valid_until=DAY + timedelta(days=3))
    days = candidate_days(v, [flash], window(DAY, 7))
    assert DAY + timedelta(days=3) in days and DAY + timedelta(days=1) in days and len(days) == 2


# --- basket vs brute force -------------------------------------------------------------------

def random_options(rng: random.Random, n_items: int, n_stores: int, n_days: int, ticket_max: int = 300) -> list[StoreOption]:
    options = []
    for s in range(n_stores):
        days = sorted(rng.sample(range(7), rng.randint(1, n_days)))
        choices, minutes = {}, {}
        for d in days:
            day = DAY + timedelta(days=d)
            minutes[day] = rng.choice([0, 10, 20, 30])
            choices[day] = {}
            for i in range(n_items):
                if rng.random() < 0.85:
                    choices[day][i] = ItemChoice(i, f"s{s}", product(i), 1, rng.randint(50, 500), None, None, "x", None)
        options.append(StoreOption(f"s{s}", rng.choice([0, 0, rng.randint(1, ticket_max)]), minutes, choices))
    return options


@pytest.mark.parametrize("seed", range(200))
def test_enumeration_matches_brute_force(seed: int) -> None:
    rng = random.Random(seed)
    options = random_options(rng, rng.randint(1, 6), rng.randint(1, 4), 3)
    need_ids = sorted({i for o in options for c in o.choices.values() for i in c})
    if not need_ids:
        assert best_plans(options, need_ids, max_k=len(options)) == {}
        return
    reference = brute_force(options, need_ids)
    plans = best_plans(options, need_ids, max_k=len(options))
    complete = [p for p in plans.values() if not p.missing]
    if reference is None:
        assert not complete
        return
    best = min(complete, key=lambda p: p.total_cents)
    assert best.total_cents == reference.total_cents
    assert sum(c.cost_cents for c in best.assignment.values()) + best.transport_cents == reference.total_cents


def test_zero_visit_cost_puts_every_item_at_its_cheapest_store() -> None:
    # One visit per store (one day each): with a single candidate day per store and no visit
    # cost, the unlimited-K plan buys every item where it is cheapest.
    rng = random.Random(7)
    options = [StoreOption(o.store, 0, o.minutes, o.choices) for o in random_options(rng, 6, 4, 1)]
    need_ids = sorted({i for o in options for c in o.choices.values() for i in c})
    plans = best_plans(options, need_ids, max_k=4)
    best = min((p for p in plans.values() if not p.missing), key=lambda p: p.total_cents)
    cheapest = sum(min(o.choices[d][i].cost_cents for o in options for d in o.choices if i in o.choices[d]) for i in need_ids)
    assert best.total_cents == cheapest


def test_fifty_items_five_stores_seven_days_under_two_seconds() -> None:
    rng = random.Random(1)
    options = random_options(rng, 50, 5, 7)
    need_ids = list(range(50))
    start = time.perf_counter()
    best_plans(options, need_ids, max_k=3)
    assert time.perf_counter() - start < 2


# --- report -----------------------------------------------------------------------------------

def test_reports_frontier_savings_and_shared_caps() -> None:
    capped = promo(pid=1, program="coop_socio", max_qty_per_card=2, id=500)
    p = product(1)
    a = ItemChoice(1, "s1", p, 2, 160, capped, 100, "x", None)
    b = ItemChoice(2, "s1", p, 2, 160, capped, 100, "x", None)
    s1 = StoreOption("s1", 0, {DAY: 5}, {DAY: {1: a, 2: b}})
    s2 = StoreOption("s2", 400, {DAY: 30}, {DAY: {1: ItemChoice(1, "s2", product(9), 1, 100, None, 100, "x", None)}})
    plans = best_plans([s1, s2], [1, 2], max_k=2)
    needs = {1: need(nid=1), 2: need(nid=2)}
    threshold = promo(id=900, mechanic=Mechanic.BASKET_THRESHOLD, params={"amount_cents": 1000, "discount_cents": 300})
    reports = build_reports(plans, needs, Programs({"coop_socio": 1}), [threshold], 1)
    one = reports[0]
    assert one.plan.stores == ("s1",) and one.plan.total_cents == 320
    assert one.cap_warnings and one.corrected_total_cents == 320 + 2 * 20   # 4 units share a cap of 2
    assert one.suggestions and "threshold" in one.suggestions[0]
    assert one.plan.total_cents == 320   # suggestions never change the plan
    two = reports[1]
    assert two.marginal_saving_cents == one.plan.total_cents - two.plan.total_cents
    warnings, extra = shared_caps(two.plan, Programs({"coop_socio": 1}))
    assert extra >= 0


def test_name_words_narrow_a_category() -> None:
    oil = Product(1, "coop", "Olio extravergine di oliva 1 l", "B", None, frozenset({10, 1}), None, 3, frozenset(), 1000, Unit.MILLILITRE)
    vinegar = Product(2, "coop", "Aceto balsamico IGP 500 ml", "B", None, frozenset({10, 1}), None, 3, frozenset(), 500, Unit.MILLILITRE)
    wanted = Need(1, "olio", MatchMode.CATEGORY, 1000, Unit.MILLILITRE, category_id=10, name_words=("extravergine",))
    assert acceptable(wanted, oil) and not acceptable(wanted, vinegar)
