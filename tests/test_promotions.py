from datetime import date

import pytest
from pydantic import ValidationError

from pisaprices.domain.enums import Chain, Mechanic, Program
from pisaprices.ingest.dates import Validity, parse_validity
from pisaprices.ingest.promotions import read_terms, validate_params
from pisaprices.ingest.records import ProductRef, PromotionRecord

OBSERVED = date(2026, 10, 23)


@pytest.mark.parametrize(
    "text, mechanic, params",
    [
        ("1+1 gratis", Mechanic.MULTIBUY_N_FOR_M, {"buy": 2, "pay": 1}),
        ("Offerta 2+1", Mechanic.MULTIBUY_N_FOR_M, {"buy": 3, "pay": 2}),
        ("3x2 su tutta la gamma", Mechanic.MULTIBUY_N_FOR_M, {"buy": 3, "pay": 2}),
        ("Prendi 3 paghi 2", Mechanic.MULTIBUY_N_FOR_M, {"buy": 3, "pay": 2}),
        ("Il secondo al 50%", Mechanic.NTH_UNIT_DISCOUNT, {"nth": 2, "pct": 50.0}),
        ("il 3° pezzo al 70%", Mechanic.NTH_UNIT_DISCOUNT, {"nth": 3, "pct": 70.0}),
        ("il secondo a metà prezzo", Mechanic.NTH_UNIT_DISCOUNT, {"nth": 2, "pct": 50.0}),
        ("Spendi 10 € e risparmi 3 €", Mechanic.BASKET_THRESHOLD, {"amount_cents": 1000, "discount_cents": 300}),
        ("Sconto dal 4° pezzo", Mechanic.TIERED_INCREMENTAL, {}),
    ],
)
def test_mechanic_wording(text: str, mechanic: Mechanic, params: dict) -> None:
    terms = read_terms(text, OBSERVED)
    assert (terms.mechanic, terms.params) == (mechanic, params)


@pytest.mark.parametrize(
    "text",
    [
        "Yogurt 6 x 125 g", "Pack 2x 80 g", "Prezzo -20%", "Sconto del 30 per cento", "Novità",
        "Prodotto pesante", "Bassi e Fissi", "€ 1,33 € 1,33 € 2,66/KG",
    ],
)
def test_wording_without_a_mechanic_is_not_guessed(text: str) -> None:
    assert read_terms(text, OBSERVED).mechanic is None


def test_incremental_tiers_are_always_reviewed() -> None:
    assert read_terms("dal 4° pezzo -20%", OBSERVED).needs_review


@pytest.mark.parametrize(
    "text, per_card, per_receipt, min_qty, sottocosto",
    [
        ("Max 2 pezzi per ordine", None, 2, None, False),
        ("MAX 2 PEZZI PER CARTA SOCIO", 2, None, None, False),
        ("max 4 pz per scontrino", None, 4, None, False),
        ("Acquisto minimo 2 pezzi", None, None, 2, False),
        ("SOTTOCOSTO acquisto limitato a 3 pezzi", None, 3, None, True),
    ],
)
def test_modifiers(text: str, per_card, per_receipt, min_qty, sottocosto) -> None:
    terms = read_terms(text, OBSERVED)
    assert (terms.max_qty_per_card, terms.max_qty_per_receipt, terms.min_qty, terms.is_sottocosto) == (
        per_card, per_receipt, min_qty, sottocosto)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Prezzo promo per consegne fino al 04/11", Validity(None, date(2026, 11, 4))),
        ("-20% Fino al 4 novembre", Validity(None, date(2026, 11, 4))),
        ("Fino al 01/11/2026", Validity(None, date(2026, 11, 1))),
        ("OFFERTE VALIDE DAL 16 AL 31 OTTOBRE", Validity(date(2026, 10, 16), date(2026, 10, 31))),
        ("Dal 22 ottobre al 4 novembre 2026", Validity(date(2026, 10, 22), date(2026, 11, 4))),
        ("dal 22/10 al 04/11/2026", Validity(date(2026, 10, 22), date(2026, 11, 4))),
        ("nessuna data", Validity()),
    ],
)
def test_validity(text: str, expected: Validity) -> None:
    assert parse_validity(text, OBSERVED) == expected


def test_year_rolls_over_at_new_year() -> None:
    assert parse_validity("fino al 03/01", date(2026, 12, 28)) == Validity(None, date(2027, 1, 3))
    assert parse_validity("dal 28/12 al 03/01", date(2026, 12, 28)) == Validity(date(2026, 12, 28), date(2027, 1, 3))


def test_far_away_date_is_flagged() -> None:
    # observed in October, "fino al 01/10" can only mean next year: suspicious
    assert parse_validity("fino al 01/10", OBSERVED).needs_review


def test_impossible_date_is_flagged_not_raised() -> None:
    assert parse_validity("fino al 31/02", OBSERVED) == Validity(needs_review=True)


@pytest.mark.parametrize(
    "mechanic, params",
    [
        (Mechanic.PRICE_CUT, {}),
        (Mechanic.MULTIBUY_N_FOR_M, {"buy": 2, "pay": 2}),
        (Mechanic.NTH_UNIT_DISCOUNT, {"nth": 2, "pct": 150}),
        (Mechanic.TIERED_ALL_UNITS, {"tiers": [{"min_qty": 3, "unit_price_cents": 90}, {"min_qty": 1, "unit_price_cents": 99}]}),
        (Mechanic.PRICE_CUT, {"promo_price_cents": 99, "extra": 1}),
    ],
)
def test_invalid_params_are_rejected(mechanic: Mechanic, params: dict) -> None:
    with pytest.raises(ValidationError):
        validate_params(mechanic, params)


def record(**overrides) -> PromotionRecord:
    values = dict(
        chain=Chain.COOP, store_slug="coop_a", mechanic=Mechanic.PRICE_CUT, params={"promo_price_cents": 131},
        items=[ProductRef(chain=Chain.COOP, external_id="0100700342600", name="Pomodori pelati 2 pz da 400 gr")],
        program=Program.COOP_SOCIO, max_qty_per_receipt=6, valid_until=date(2026, 11, 4),
        raw_text="Prezzo in promozione: €1.84 € 1,31 Con Carta Soci", observed_at="2026-10-23T10:00:00Z",
    )
    return PromotionRecord(**{**values, **overrides})


def test_natural_key_is_stable_across_runs() -> None:
    first = record()
    again = record(raw_text="same offer, slightly different wording", observed_at="2026-10-24T09:00:00Z")
    assert first.natural_key() == again.natural_key()


@pytest.mark.parametrize(
    "change",
    [{"params": {"promo_price_cents": 129}}, {"program": None}, {"valid_until": date(2026, 11, 5)}, {"store_slug": "coop_b"}],
)
def test_natural_key_changes_with_identity(change: dict) -> None:
    assert record().natural_key() != record(**change).natural_key()


def test_unknown_mechanic_is_always_reviewed() -> None:
    promo = record(mechanic=Mechanic.UNKNOWN, params={})
    assert promo.needs_review and promo.params == {}


def test_record_rejects_params_of_another_mechanic() -> None:
    with pytest.raises(ValidationError):
        record(mechanic=Mechanic.MULTIBUY_N_FOR_M)  # still carries promo_price_cents


@pytest.mark.parametrize(
    "moment, expected",
    [
        ("2026-10-28T22:59:59Z", date(2026, 10, 28)),   # winter: 23:59:59 in Rome
        ("2026-10-28T23:00:00Z", date(2026, 10, 29)),
        ("2026-07-15T21:59:59Z", date(2026, 7, 15)),    # summer: 23:59:59 in Rome
        ("2026-07-15T22:00:00Z", date(2026, 7, 16)),
        ("2026-03-29T00:59:59Z", date(2026, 3, 29)),    # just before the spring switch
        ("2026-10-25T00:30:00Z", date(2026, 10, 25)),   # still summer time: 02:30 in Rome
    ],
)
def test_rome_date(moment: str, expected: date) -> None:
    from pisaprices.ingest.dates import rome_date_from_iso
    assert rome_date_from_iso(moment) == expected
