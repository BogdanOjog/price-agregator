"""Candidate visit days per store, reduced to promotion regimes.

Two allowed days with the same set of active (relevant) promotions at a store give the same item
costs, so only one of them is kept: the one with fewer extra minutes, then the earlier."""

from datetime import date, timedelta

from pisaprices.domain.model import Promo, Visit


def window(start: date, days: int) -> list[date]:
    return [start + timedelta(days=i) for i in range(days)]


def candidate_days(visit: Visit, promos: list[Promo], days: list[date]) -> list[date]:
    regimes: dict[frozenset[int], date] = {}
    for day in days:
        if not visit.allows(day):
            continue
        regime = frozenset(p.id for p in promos if p.active_on(day))
        kept = regimes.get(regime)
        if kept is None or (visit.minutes(day), day) < (visit.minutes(kept), kept):
            regimes[regime] = day
    return sorted(regimes.values())
