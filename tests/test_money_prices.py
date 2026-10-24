from decimal import Decimal

import pytest

from pisaprices.domain.enums import Unit
from pisaprices.domain.money import cost_for_amount, to_cents, to_eur, unit_price
from pisaprices.domain.quantity import Quantity
from pisaprices.ingest.prices import SiteUnitPrice, parse_eur, parse_site_unit_price, unit_price_mismatch


@pytest.mark.parametrize(
    "value, cents",
    [("1.33", 133), (Decimal("0.845"), 85), (2.19, 219), (0.1 + 0.2, 30), (1.005, 101), (7, 700), ("0", 0)],
)
def test_to_cents(value, cents: int) -> None:
    assert to_cents(value) == cents


def test_to_eur_round_trip() -> None:
    assert to_eur(to_cents("12.34")) == Decimal("12.34")


@pytest.mark.parametrize(
    "price_cents, quantity, expected",
    [
        (133, Quantity(500, Unit.GRAM), 2.66),
        (166, Quantity(1000, Unit.MILLILITRE), 1.66),
        (219, Quantity(1500, Unit.GRAM), 1.46),
        (299, Quantity(6, Unit.PIECE), 0.498),
        (149, Quantity(1000, Unit.GRAM, variable_weight=True), 1.49),
    ],
)
def test_unit_price(price_cents: int, quantity: Quantity, expected: float) -> None:
    assert unit_price(price_cents, quantity) == expected


def test_cost_for_variable_weight_rounds_to_cent() -> None:
    assert cost_for_amount(299, 1234, Unit.GRAM) == 369  # 2.99 €/kg × 1.234 kg = 3.68966 €


@pytest.mark.parametrize(
    "text, cents",
    [
        ("€ 1,33", 133), ("1,75€", 175), ("€1.84", 184), ("Prezzo pieno 8,99€", 899),
        ("€ 1.234,56", 123456), ("2.19", 219), ("€ 17,90 Non Soci", 1790), ("Gratis", None),
    ],
)
def test_parse_eur(text: str, cents: int | None) -> None:
    assert parse_eur(text) == cents


@pytest.mark.parametrize(
    "text, expected",
    [
        ("€ 2,66/KG", SiteUnitPrice(266, Unit.GRAM)),
        ("€ 1,66/LT", SiteUnitPrice(166, Unit.MILLILITRE)),
        ("3,50 € / Kg", SiteUnitPrice(350, Unit.GRAM)),
        ("8,74€/Kg", SiteUnitPrice(874, Unit.GRAM)),
        ("1 kg = 1.46 €", SiteUnitPrice(146, Unit.GRAM)),
        ("1 l = 0.99 €", SiteUnitPrice(99, Unit.MILLILITRE)),
        ("500 g", None),
    ],
)
def test_parse_site_unit_price(text: str, expected: SiteUnitPrice | None) -> None:
    assert parse_site_unit_price(text) == expected


def test_unit_price_mismatch_uses_tolerance() -> None:
    site = SiteUnitPrice(266, Unit.GRAM)
    assert not unit_price_mismatch(2.66, site, tolerance=0.02)
    assert not unit_price_mismatch(2.675, site, tolerance=0.02)
    assert unit_price_mismatch(2.70, site, tolerance=0.02)


def test_unit_price_disagrees_only_when_comparable() -> None:
    from pisaprices.ingest.prices import unit_price_disagrees
    six_eggs = Quantity(6, Unit.PIECE, pieces=6)
    assert not unit_price_disagrees(208, six_eggs, SiteUnitPrice(208, Unit.PIECE), 0.02)   # box counted as one piece
    assert not unit_price_disagrees(133, Quantity(500, Unit.GRAM), SiteUnitPrice(166, Unit.MILLILITRE), 0.02)
    assert not unit_price_disagrees(133, None, SiteUnitPrice(266, Unit.GRAM), 0.02)
    assert unit_price_disagrees(243, Quantity(300, Unit.GRAM), SiteUnitPrice(1215, Unit.GRAM), 0.02)   # drained weight
