"""Planning a shopping list: database + config -> domain objects -> optimiser -> reports."""

import json
from dataclasses import dataclass, field
from datetime import date, timedelta

from pisaprices.config.schemas import AppConfig
from pisaprices.domain.basket import Plan, StoreOption, best_plans
from pisaprices.domain.enums import Channel, MatchMode, Mechanic, SourceMode, Transport, Unit
from pisaprices.domain.item_cost import ItemChoice, best_choice
from pisaprices.domain.model import Need, Offer, Product, Programs, Promo, Visit
from pisaprices.domain.report import PlanReport, build_reports
from pisaprices.domain.visits import candidate_days, window
from pisaprices.ingest.dates import rome_date
from pisaprices.services.collect import Runtime
from pisaprices.storage.repos import catalog as catalog_repo
from pisaprices.storage.repos import planning as repo


@dataclass
class PlanResult:
    list_name: str
    start: date
    days: list[date]
    needs: dict[int, Need]
    reports: list[PlanReport]
    uncovered: list[str]                                     # list items no store can supply in the window
    card_savings: dict[str, int] = field(default_factory=dict)   # program -> cents saved on the best plan
    store_names: dict[str, str] = field(default_factory=dict)    # slug -> name
    # price report: need id -> store -> the cheapest choice there over the window (any day)
    matrix: dict[int, dict[str, ItemChoice]] = field(default_factory=dict)

    @property
    def simplest(self) -> PlanReport | None:
        """The complete plan with the fewest stores."""
        complete = [r for r in self.reports if not r.plan.missing]
        return min(complete, key=lambda r: r.k) if complete else None

    @property
    def options(self) -> list[PlanReport]:
        """Complete plans worth showing: each one cheaper than every plan with fewer stores."""
        kept: list[PlanReport] = []
        for report in sorted((r for r in self.reports if not r.plan.missing), key=lambda r: r.k):
            if not kept or report.plan.total_cents < min(r.plan.total_cents for r in kept):
                kept.append(report)
        return kept

    @property
    def best(self) -> PlanReport | None:
        complete = [r for r in self.reports if not r.plan.missing]
        return min(complete, key=lambda r: r.plan.total_cents) if complete else None


def programs_of(config: AppConfig) -> Programs:
    return Programs({p.value: c.n_cards for p, c in config.user.programs.items() if c.has_card})


def visits_of(config: AppConfig) -> list[Visit]:
    visits = []
    for store in config.stores.stores:
        if not store.visitable:
            continue
        travel = config.user.stores[store.slug]
        ticket = travel.transport_cost_eur if travel.transport is Transport.BUS else 0
        visits.append(Visit(
            store=store.slug, chain=store.chain.value, name=store.name, transport_cost_cents=round((ticket or 0) * 100),
            round_trip_minutes=2 * travel.travel_minutes_one_way, detour_minutes=travel.detour_minutes,
            allowed_days=frozenset(travel.allowed_days) if travel.allowed_days is not None else None,
            on_route_days=frozenset(travel.on_route_days), promo_only=store.source_mode is SourceMode.PROMO_ONLY,
        ))
    return visits


def _ancestors(category_id: int | None, parents: dict[int, int | None]) -> frozenset[int]:
    found = set()
    while category_id is not None and category_id not in found:
        found.add(category_id)
        category_id = parents.get(category_id)
    return frozenset(found)


def _product(row: dict, parents: dict[int, int | None]) -> Product:
    flags = row.get("flags")
    flags = json.loads(flags) if isinstance(flags, str) else (flags or [])
    return Product(
        id=row["id"], chain=row["chain"], name=row["name"], brand=row["brand"], ean=row["ean"],
        category_ids=_ancestors(row["category_id"], parents), canonical_id=row["canonical_id"], tier=row.get("tier"),
        flags=frozenset(flags), net_qty=row["net_qty"], unit=Unit(row["unit"]) if row["unit"] else None,
        variable_weight=bool(row["variable_weight"]),
    )


def _promo(row: dict) -> Promo:
    params = row["params"] if isinstance(row["params"], dict) else json.loads(row["params"])
    ids = frozenset(int(i) for i in (row["product_ids"] or "").split(",") if i)
    return Promo(
        id=row["id"], store=row["store_slug"], chain=row["chain"], product_ids=ids, mechanic=Mechanic(row["mechanic"]),
        params=params, program=row["program"], channel=Channel(row["channel"]), min_qty=row["min_qty"],
        max_qty_per_card=row["max_qty_per_card"], max_qty_per_receipt=row["max_qty_per_receipt"],
        is_sottocosto=bool(row["is_sottocosto"]), requires_activation=bool(row["requires_activation"]),
        stackable=bool(row["stackable"]),
        valid_from=date.fromisoformat(row["valid_from"]) if row["valid_from"] else None,
        valid_until=date.fromisoformat(row["valid_until"]) if row["valid_until"] else None,
        prior_price_cents=row["prior_price_cents"], needs_review=bool(row["needs_review"]),
    )


def _need(row: dict) -> Need:
    return Need(
        id=row["id"], label=row["label"], match_mode=MatchMode(row["match_mode"]), qty=row["qty_needed"], unit=Unit(row["qty_unit"]),
        gtin=row["gtin"], brand=row["brand"], canonical_id=row["canonical_id"], category_id=row["category_id"],
        min_tier=row["min_tier"], required_flags=frozenset(row["required_flags"] or []), overbuy_max_units=row["overbuy_max_units"],
        name_words=tuple(row["name_contains"] or []),
    )


@dataclass
class _Inputs:
    needs: dict[int, Need]
    visits: list[Visit]
    offers: dict[str, list[Offer]]
    promos: dict[str, list[Promo]]
    all_promos: list[Promo]


def _inputs(runtime: Runtime, items: list[dict], start: date, days: int) -> _Inputs:
    config = runtime.config
    end = start + timedelta(days=days - 1)
    max_age = config.settings.optimizer.max_price_age_days
    visits = visits_of(config)
    by_slug = {s.slug: s for s in config.stores.stores}
    with runtime.sessions() as session:
        parents = repo.category_parents(session)
        store_ids = {s["slug"]: s["id"] for s in catalog_repo.stores(session)}
        promo_rows = repo.window_promotions(session, start, end, start - timedelta(days=max_age))
        promos = [_promo(r) for r in promo_rows]

        offers: dict[str, list[Offer]] = {}
        store_promos: dict[str, list[Promo]] = {}
        for visit in visits:
            store = by_slug[visit.store]
            source = by_slug[store.price_source] if store.price_source else store
            label = store.name if source.slug == store.slug else f"preț online {source.name}"
            sources = {visit.store, source.slug}
            store_promos[visit.store] = [p for p in promos if p.store in sources or (p.store is None and p.chain == visit.chain)]
            if visit.promo_only:
                ids = sorted({pid for p in store_promos[visit.store] for pid in p.product_ids})
                rows = repo.products_by_ids(session, ids)
                offers[visit.store] = [Offer(_product(r, parents), visit.store, None, None, store.name) for r in rows]
            else:
                rows = repo.priced_products(session, [store_ids[source.slug]], (start - timedelta(days=max_age)).isoformat())
                offers[visit.store] = [Offer(_product(r, parents), visit.store, r["price_cents"], r["observed_at"], label) for r in rows]
    needs = {row["id"]: _need(row) for row in items}
    return _Inputs(needs, visits, offers, store_promos, promos)


def _options(inputs: _Inputs, days: list[date], programs: Programs) -> list[StoreOption]:
    options = []
    for visit in inputs.visits:
        promos = inputs.promos[visit.store]
        choices, minutes = {}, {}
        for day in candidate_days(visit, promos, days):
            minutes[day] = visit.minutes(day)
            choices[day] = {}
            for need in inputs.needs.values():
                choice = best_choice(need, inputs.offers[visit.store], promos, day, programs, visit.promo_only)
                if choice is not None:
                    choices[day][need.id] = choice
        options.append(StoreOption(visit.store, visit.transport_cost_cents, minutes, choices))
    return options


def _best_total(plans: dict[int, Plan]) -> int | None:
    complete = [p.total_cents for p in plans.values() if not p.missing]
    return min(complete) if complete else None


def plan_list(runtime: Runtime, list_id: int, start: date | None = None, window_days: int | None = None,
              max_k: int | None = None) -> PlanResult:
    with runtime.sessions() as session:
        found = repo.get_list(session, list_id)
    if found is None:
        raise ValueError(f"no shopping list {list_id}")
    return plan_items(runtime, found["name"], found["items"], start, window_days, max_k)


def plan_items(runtime: Runtime, name: str, items: list[dict], start: date | None = None,
               window_days: int | None = None, max_k: int | None = None) -> PlanResult:
    """Plan list items given as rows (the fields of list_items, with a unique `id`), saved or not."""
    settings = runtime.config.settings.optimizer
    start = start or rome_date(runtime.clock.now())
    window_days = window_days or settings.planning_window_days
    max_k = max_k or settings.max_stores_k
    inputs = _inputs(runtime, items, start, window_days)
    days = window(start, window_days)
    programs = programs_of(runtime.config)

    options = _options(inputs, days, programs)
    need_ids = sorted(inputs.needs)
    plans = best_plans(options, need_ids, max_k)
    reports = build_reports(plans, inputs.needs, programs, inputs.all_promos, settings.expiry_warning_days)
    covered = {n for o in options for c in o.choices.values() for n in c}
    uncovered = [inputs.needs[n].label for n in need_ids if n not in covered]

    savings = {}
    base_total = _best_total(plans)
    for program in sorted({p.program for p in inputs.all_promos if p.program and not programs.has(p.program)}):
        with_card = best_plans(_options(inputs, days, programs.with_card(program)), need_ids, max_k)
        total = _best_total(with_card)
        if base_total is not None and total is not None and total < base_total:
            savings[program] = base_total - total
    matrix: dict[int, dict[str, ItemChoice]] = {}
    for option in options:
        for day_choices in option.choices.values():
            for need_id, choice in day_choices.items():
                best = matrix.setdefault(need_id, {}).get(option.store)
                if best is None or choice.cost_cents < best.cost_cents:
                    matrix[need_id][option.store] = choice
    names = {v.store: v.name for v in inputs.visits}
    return PlanResult(name, start, days, inputs.needs, reports, uncovered, savings, names, matrix)
