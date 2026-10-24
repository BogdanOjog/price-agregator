"""The optimizer's view of the world: plain frozen dataclasses, no I/O."""

from dataclasses import dataclass, field
from datetime import date

from pisaprices.domain.enums import Channel, MatchMode, Mechanic, Unit, Weekday


@dataclass(frozen=True)
class Product:
    id: int
    chain: str
    name: str
    brand: str | None
    ean: str | None
    category_ids: frozenset[int]          # its category and every ancestor
    canonical_id: int | None
    tier: int | None
    flags: frozenset[str]
    net_qty: float | None
    unit: Unit | None
    variable_weight: bool = False


@dataclass(frozen=True)
class Offer:
    """A product buyable at a visitable store. base_price_cents is None at promotion-only stores."""

    product: Product
    store: str
    base_price_cents: int | None
    observed_at: str | None
    price_source: str                      # label shown with the price ("preț online Coop Cascina")


@dataclass(frozen=True)
class Promo:
    id: int
    store: str | None                      # None = every store of the chain
    chain: str
    product_ids: frozenset[int]
    mechanic: Mechanic
    params: dict
    program: str | None = None
    channel: Channel = Channel.STORE
    min_qty: int | None = None
    max_qty_per_card: int | None = None
    max_qty_per_receipt: int | None = None
    is_sottocosto: bool = False
    requires_activation: bool = False
    stackable: bool = False
    valid_from: date | None = None
    valid_until: date | None = None
    prior_price_cents: int | None = None
    needs_review: bool = False

    def active_on(self, day: date) -> bool:
        return (self.valid_from is None or self.valid_from <= day) and (self.valid_until is None or day <= self.valid_until)

    def applies_in_store(self) -> bool:
        return self.channel in (Channel.STORE, Channel.BOTH)


@dataclass(frozen=True)
class Need:
    """A shopping list item: a requirement, not a product."""

    id: int
    label: str
    match_mode: MatchMode
    qty: float
    unit: Unit
    gtin: str | None = None
    brand: str | None = None
    canonical_id: int | None = None
    category_id: int | None = None
    min_tier: int | None = None
    required_flags: frozenset[str] = frozenset()
    overbuy_max_units: int = 0
    name_words: tuple[str, ...] = ()       # words the product name must contain (at a word start)


@dataclass(frozen=True)
class Visit:
    """A visitable store and what a visit costs."""

    store: str
    chain: str
    name: str
    transport_cost_cents: int
    round_trip_minutes: float
    detour_minutes: float = 0
    allowed_days: frozenset[Weekday] | None = None   # None = any day
    on_route_days: frozenset[Weekday] = frozenset()
    promo_only: bool = False

    def allows(self, day: date) -> bool:
        return self.allowed_days is None or weekday(day) in self.allowed_days

    def minutes(self, day: date) -> float:
        return self.detour_minutes if weekday(day) in self.on_route_days else self.round_trip_minutes


WEEKDAYS = [Weekday.MON, Weekday.TUE, Weekday.WED, Weekday.THU, Weekday.FRI, Weekday.SAT, Weekday.SUN]


def weekday(day: date) -> Weekday:
    return WEEKDAYS[day.weekday()]


@dataclass(frozen=True)
class Programs:
    """Loyalty programs the user holds, with the number of cards."""

    cards: dict[str, int] = field(default_factory=dict)

    def has(self, program: str | None) -> bool:
        return program is None or program in self.cards

    def n_cards(self, program: str | None) -> int:
        return self.cards.get(program, 1) if program else 1

    def with_card(self, program: str) -> "Programs":
        return Programs({**self.cards, program: self.cards.get(program, 1)})
