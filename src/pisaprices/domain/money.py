"""Money is integer cents everywhere. Unit prices (€/kg, €/l, €/pz) are floats for display only."""

from decimal import ROUND_HALF_UP, Decimal

from pisaprices.domain.enums import Unit
from pisaprices.domain.quantity import Quantity

CENT = Decimal("0.01")
# base units per display unit: €/kg for grams, €/l for millilitres, €/pz for pieces
PER_DISPLAY_UNIT = {Unit.GRAM: 1000, Unit.MILLILITRE: 1000, Unit.PIECE: 1}


def to_cents(eur: Decimal | str | int | float) -> int:
    """Euros to cents, rounding half up. Floats go through str() so 2.19 stays 219, not 218."""
    value = Decimal(str(eur)) if isinstance(eur, float) else Decimal(eur)
    return int((value / CENT).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def to_eur(cents: int) -> Decimal:
    return (Decimal(cents) * CENT).quantize(CENT)


def unit_price(price_cents: int, quantity: Quantity) -> float:
    """€/kg, €/l or €/pz of a package priced `price_cents`. For variable weight the price is per kg already."""
    per_unit = Decimal(price_cents) * CENT * PER_DISPLAY_UNIT[quantity.unit] / Decimal(str(quantity.amount))
    return float(per_unit.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP))


def cost_for_amount(price_per_display_unit_cents: int, amount: float, unit: Unit) -> int:
    """Cost of `amount` base units sold by weight or volume, rounded to the cent (variable-weight items)."""
    value = Decimal(price_per_display_unit_cents) * Decimal(str(amount)) / PER_DISPLAY_UNIT[unit]
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))
