"""Exact basket optimisation by enumeration (ISHOP with visit days).

For every subset of stores and every combination of their candidate days, each item goes to the
cheapest (store, day) of the subset. Money: item costs + transport tickets. Time is not money
here (decision at T0.7): the result is the frontier K = 1..max_k, with extra minutes and the
break-even €/h of each additional store. Ties: fewer uncovered items, lower cost, fewer stores,
fewer minutes, earlier days, store order."""

from dataclasses import dataclass
from datetime import date
from itertools import combinations, product

from pisaprices.domain.item_cost import ItemChoice


@dataclass(frozen=True)
class StoreOption:
    store: str
    transport_cost_cents: int
    minutes: dict[date, float]                       # candidate day -> extra minutes
    choices: dict[date, dict[int, ItemChoice]]       # candidate day -> need id -> best choice


@dataclass(frozen=True)
class Plan:
    stores: tuple[str, ...]
    days: dict[str, date]
    assignment: dict[int, ItemChoice]                # need id -> choice
    missing: tuple[int, ...]                         # need ids no store of the plan covers
    items_cents: int
    transport_cents: int
    minutes: float

    @property
    def total_cents(self) -> int:
        return self.items_cents + self.transport_cents

    def key(self, order: dict[str, int]) -> tuple:
        return (len(self.missing), self.total_cents, len(self.stores), self.minutes,
                tuple(sorted(self.days[s] for s in self.stores)), tuple(order[s] for s in self.stores))


def _evaluate(options: tuple[StoreOption, ...], days: tuple[date, ...], need_ids: list[int], order: dict[str, int]) -> Plan:
    assignment, missing, items = {}, [], 0
    for need in need_ids:
        best = None
        for option, day in zip(options, days):
            choice = option.choices[day].get(need)
            if choice is None:
                continue
            if best is None or (choice.cost_cents, order[option.store]) < (best.cost_cents, order[best.store]):
                best = choice
        if best is None:
            missing.append(need)
        else:
            assignment[need] = best
            items += best.cost_cents
    return Plan(
        stores=tuple(o.store for o in options), days={o.store: d for o, d in zip(options, days)},
        assignment=assignment, missing=tuple(missing), items_cents=items,
        transport_cents=sum(o.transport_cost_cents for o in options),
        minutes=sum(o.minutes[d] for o, d in zip(options, days)),
    )


def best_plans(options: list[StoreOption], need_ids: list[int], max_k: int) -> dict[int, Plan]:
    """The best plan with exactly k stores, for k = 1..max_k (stores that offer nothing are skipped)."""
    order = {o.store: i for i, o in enumerate(options)}
    usable = [o for o in options if o.choices and any(o.choices.values())]
    best: dict[int, Plan] = {}
    for k in range(1, min(max_k, len(usable)) + 1):
        for subset in combinations(usable, k):
            for days in product(*(sorted(o.choices) for o in subset)):
                plan = _evaluate(subset, days, need_ids, order)
                if k not in best or plan.key(order) < best[k].key(order):
                    best[k] = plan
    return best


def brute_force(options: list[StoreOption], need_ids: list[int]) -> Plan | None:
    """Reference for tests: every assignment item -> (store, day), one day per visited store."""
    order = {o.store: i for i, o in enumerate(options)}
    slots = [(o, d) for o in options for d in sorted(o.choices)]
    best = None
    for picks in product(slots, repeat=len(need_ids)):
        days: dict[str, date] = {}
        if any(days.setdefault(o.store, d) != d for o, d in picks):
            continue
        if any(o.choices[d].get(n) is None for (o, d), n in zip(picks, need_ids)):
            continue
        visited = tuple(sorted({o.store for o, _ in picks}, key=order.get))
        by_store = {o.store: o for o in options}
        plan = Plan(
            stores=visited, days=days,
            assignment={n: o.choices[d][n] for (o, d), n in zip(picks, need_ids)}, missing=(),
            items_cents=sum(o.choices[d][n].cost_cents for (o, d), n in zip(picks, need_ids)),
            transport_cents=sum(by_store[s].transport_cost_cents for s in visited),
            minutes=sum(by_store[s].minutes[days[s]] for s in visited),
        )
        if best is None or plan.total_cents < best.total_cents:
            best = plan
    return best
