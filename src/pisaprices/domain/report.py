"""What the user reads: plans per K with savings, warnings and post-hoc suggestions.

Nothing here changes a plan's choices. The shared-cap check recomputes the cost when two list
items draw on the same per-card/per-receipt cap and shows the corrected total next to it."""

from dataclasses import dataclass, field
from datetime import date

from pisaprices.domain.basket import Plan
from pisaprices.domain.enums import Mechanic
from pisaprices.domain.item_cost import ItemChoice
from pisaprices.domain.model import Need, Programs, Promo


@dataclass(frozen=True)
class Line:
    need: Need
    choice: ItemChoice
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class PlanReport:
    k: int
    plan: Plan
    lines: dict[str, list[Line]]                    # store -> lines
    saving_vs_best_single_cents: int | None         # when the single-store plan covers the same items
    marginal_saving_cents: int | None               # vs the K-1 plan, when both cover the same items
    extra_minutes: float | None                     # vs the K-1 plan
    break_even_eur_per_hour: float | None           # marginal saving per extra hour
    cap_warnings: tuple[str, ...] = ()
    corrected_total_cents: int | None = None        # when a shared cap is exceeded
    suggestions: tuple[str, ...] = field(default_factory=tuple)


def line_warnings(choice: ItemChoice, day: date, expiry_warning_days: int) -> tuple[str, ...]:
    promo = choice.promo
    if promo is None:
        return ()
    warnings = []
    if promo.valid_until and (promo.valid_until - day).days <= expiry_warning_days:
        warnings.append(f"offer ends {promo.valid_until.isoformat()}")
    if promo.is_sottocosto:
        warnings.append("limited stock: may sell out early")
    if promo.requires_activation:
        warnings.append("activate the coupon in the app first")
    if promo.needs_review:
        warnings.append("offer details to check (in review queue)")
    if promo.program:
        warnings.append(f"show card: {promo.program}")
    return tuple(warnings)


def shared_caps(plan: Plan, programs: Programs) -> tuple[list[str], int]:
    """Units bought under the same capped promotion by several items; returns warnings and the extra cost."""
    used: dict[int, list[ItemChoice]] = {}
    for choice in plan.assignment.values():
        if choice.promo and (choice.promo.max_qty_per_card or choice.promo.max_qty_per_receipt):
            used.setdefault(choice.promo.id, []).append(choice)
    warnings, extra = [], 0
    for choices in used.values():
        if len(choices) < 2:
            continue
        promo = choices[0].promo
        cap = promo.max_qty_per_receipt or promo.max_qty_per_card * programs.n_cards(promo.program)
        units = sum(c.units for c in choices)
        if units <= cap:
            continue
        over = units - cap
        promo_unit = promo.params.get("promo_price_cents", 0)
        full_unit = choices[0].base_price_cents or promo.prior_price_cents or promo_unit
        extra += over * (full_unit - promo_unit)
        warnings.append(f"{units} units share a cap of {cap} on '{choices[0].product.name}': {over} at full price")
    return warnings, extra


def suggestions(plan: Plan, promos: list[Promo]) -> list[str]:
    """Basket thresholds and rewards: shown, never optimised (v1)."""
    out = []
    spent: dict[str, int] = {}
    for choice in plan.assignment.values():
        spent[choice.store] = spent.get(choice.store, 0) + choice.cost_cents
    for promo in promos:
        store = promo.store
        if store not in plan.days or not promo.active_on(plan.days[store]):
            continue
        if promo.mechanic is Mechanic.BASKET_THRESHOLD:
            missing = promo.params["amount_cents"] - spent.get(store, 0)
            if missing > 0:
                out.append(f"{store}: {missing / 100:.2f} € more to reach the {promo.params['discount_cents'] / 100:.2f} € threshold discount")
        elif promo.mechanic is Mechanic.REWARD:
            out.append(f"{store}: bonus {promo.params.get('reward_kind')} available")
    return out


def build_reports(plans: dict[int, Plan], needs: dict[int, Need], programs: Programs, promos: list[Promo],
                  expiry_warning_days: int) -> list[PlanReport]:
    reports = []
    full = {k: p for k, p in plans.items()}
    best_single = full.get(1)
    previous = None
    for k in sorted(full):
        plan = full[k]
        lines: dict[str, list[Line]] = {s: [] for s in plan.stores}
        for need_id, choice in plan.assignment.items():
            lines[choice.store].append(Line(needs[need_id], choice, line_warnings(choice, plan.days[choice.store], expiry_warning_days)))
        cap_warnings, extra = shared_caps(plan, programs)
        marginal = extra_minutes = break_even = None
        if previous is not None and len(previous.missing) == len(plan.missing):   # only plans covering the same items compare
            marginal = previous.total_cents - plan.total_cents
            extra_minutes = plan.minutes - previous.minutes
            if marginal > 0 and extra_minutes > 0:
                break_even = round(marginal / 100 / (extra_minutes / 60), 2)
        reports.append(PlanReport(
            k=k, plan=plan, lines=lines,
            saving_vs_best_single_cents=(best_single.total_cents - plan.total_cents)
            if best_single and len(best_single.missing) == len(plan.missing) else None,
            marginal_saving_cents=marginal, extra_minutes=extra_minutes, break_even_eur_per_hour=break_even,
            cap_warnings=tuple(cap_warnings), corrected_total_cents=plan.total_cents + extra if extra else None,
            suggestions=tuple(suggestions(plan, promos)),
        ))
        previous = plan
    return reports
