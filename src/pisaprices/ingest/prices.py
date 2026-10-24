"""Prices from store text: "€ 1,33", "1,75€", "€1.84", "3,50 € / Kg", "1 kg = 1.46 €"."""

import re
from dataclasses import dataclass

from pisaprices.domain.enums import Unit
from pisaprices.domain.money import to_cents, unit_price
from pisaprices.domain.quantity import Quantity

AMOUNT = re.compile(r"\d[\d.,]*")
SITE_UNIT_PRICE = [
    # "€ 2,66/KG", "3,50 € / Kg", "8,74€/Kg", "€ 1,66/LT"
    re.compile(r"(?:€\s*)?(\d[\d.,]*)\s*(?:€)?\s*/\s*(kg|l|lt|pz)\b"),
    # "1 kg = 1.46 €" (Lidl basePrice)
    re.compile(r"1\s*(kg|l|lt|pz)\s*=\s*(?:€\s*)?(\d[\d.,]*)"),
]
DISPLAY_UNITS = {"kg": Unit.GRAM, "l": Unit.MILLILITRE, "lt": Unit.MILLILITRE, "pz": Unit.PIECE}


@dataclass(frozen=True)
class SiteUnitPrice:
    cents: int   # per kg, per l or per piece
    unit: Unit   # base unit of the package it refers to (g -> €/kg, ml -> €/l, pz -> €/pz)


def _decimal_text(number: str) -> str:
    """'1,33' -> '1.33'; '1.84' -> '1.84'; '1.234,56' -> '1234.56'."""
    number = number.rstrip(".,")
    if "," in number and "." in number:
        return number.replace(".", "").replace(",", ".")
    return number.replace(",", ".")


def parse_eur(text: str) -> int | None:
    """First amount in the text, in cents. None when there is no number."""
    match = AMOUNT.search(text)
    return to_cents(_decimal_text(match.group())) if match else None


def parse_site_unit_price(text: str) -> SiteUnitPrice | None:
    lowered = text.lower()
    first, second = SITE_UNIT_PRICE
    if match := first.search(lowered):
        number, unit = match.groups()
    elif match := second.search(lowered):
        unit, number = match.groups()
    else:
        return None
    return SiteUnitPrice(to_cents(_decimal_text(number)), DISPLAY_UNITS[unit])


def unit_price_mismatch(computed: float, site: SiteUnitPrice, tolerance: float) -> bool:
    """True when our €/kg (or €/l, €/pz) differs from the one the site prints by more than `tolerance` €."""
    return abs(computed - site.cents / 100) > tolerance


def unit_price_disagrees(price_cents: int, quantity: Quantity | None, site: SiteUnitPrice | None, tolerance: float) -> bool:
    """Our unit price for `price_cents` vs the one printed next to it, when they are comparable.

    Not comparable: no quantity or no site value, different units, or the site counting the whole
    package as one piece (Coop eggs: "6 pz", "€ 2,08/PZ" for a 2,08 € box)."""
    if quantity is None or site is None or site.unit != quantity.unit:
        return False
    if site.unit is Unit.PIECE and site.cents == price_cents:
        return False
    return unit_price_mismatch(unit_price(price_cents, quantity), site, tolerance)
