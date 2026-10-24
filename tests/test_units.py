import pytest

from pisaprices.domain.enums import Unit
from pisaprices.domain.quantity import Quantity
from pisaprices.ingest.units import parse_quantity, quantity_from_fields

G, ML, PZ = Unit.GRAM, Unit.MILLILITRE, Unit.PIECE


@pytest.mark.parametrize(
    "text, expected",
    [
        # plain weights and volumes, as written on the sites
        ("Fusilli pasta di semola 500 gr", Quantity(500, G)),
        ("Riso Arborio 1 kg", Quantity(1000, G)),
        ("MARCA 500g Penne rigate 500 gr", Quantity(500, G)),
        ("1,5 kg rete", Quantity(1500, G)),
        ("Latte intero 1 lt", Quantity(1000, ML)),
        ("Panna da cucina 200 ml", Quantity(200, ML)),
        ("Olio extravergine 0,75 l", Quantity(750, ML)),
        ("Vino rosso 75 cl", Quantity(750, ML)),
        ("Detersivo 1250 ml confezione", Quantity(1250, ML)),
        ("Pappardelle g. 250 Marca", Quantity(250, G)),
        ("217 Reginette g. 500 Marca", Quantity(500, G)),
        # multipacks
        ("Yogurt bianco 6 x 125 g", Quantity(750, G, pieces=6)),
        ("Succo di frutta 3 pz x 200 ml", Quantity(600, ML, pieces=3)),
        ("Burger vegetale 2x 80 g confezione", Quantity(160, G, pieces=2)),
        ("Pomodori pelati 2 pz da 400 gr", Quantity(800, G, pieces=2)),
        ("Bibita 4x33cl", Quantity(1320, ML, pieces=4)),
        # "N pz Q" without x/da: Q is the total
        ("Piadine 3 pz 375 gr", Quantity(375, G, pieces=3)),
        # drained weight wins
        ("Tonno all'olio 160 g sgocciolato 112 g", Quantity(112, G, drained=True)),
        ("Filetti marinati 120 g (sgocc.) conf.", Quantity(120, G, drained=True)),
        # ranges keep the lower bound
        ("Box assortito 190/200 g confezione", Quantity(190, G, approximate=True)),
        # pieces only
        ("Pastiglie lavastoviglie 96 pz", Quantity(96, PZ, pieces=96)),
        ("Uova fresche grandi 6 pz", Quantity(6, PZ, pieces=6)),
        ("6 uova medie 53-63 g", Quantity(6, PZ, pieces=6)),
        ("Uova da allevamento a terra x10", Quantity(10, PZ, pieces=10)),
        # eggs as an ingredient are not a pack of eggs
        ("Tagliatelle all'uovo con 6 uova 250 g", Quantity(250, G)),
        # sold by weight
        ("Sfusa al kg", Quantity(1000, G, variable_weight=True)),
        ("Mele Golden Al kg", Quantity(1000, G, variable_weight=True)),
    ],
)
def test_parse_quantity(text: str, expected: Quantity) -> None:
    assert parse_quantity(text) == expected


@pytest.mark.parametrize("text", ["Detersivo Gel 30 lavaggi", "Confezione regalo", "", "n. 112"])
def test_unrecognised_quantity_is_none(text: str) -> None:
    assert parse_quantity(text) is None


@pytest.mark.parametrize(
    "value, um, expected",
    [
        (0.5, "KG", Quantity(500, G)),
        (0.25, "kg", Quantity(250, G)),
        (1.0, "L", Quantity(1000, ML)),
        (6, "PZ", Quantity(6, PZ, pieces=6)),
        (0, "KG", None),
        (1, "SCATOLE", None),
    ],
)
def test_quantity_from_fields(value: float, um: str, expected: Quantity | None) -> None:
    assert quantity_from_fields(value, um) == expected


def test_quantity_must_be_positive() -> None:
    with pytest.raises(ValueError):
        Quantity(0, G)


def test_a_zero_quantity_is_no_quantity() -> None:
    assert parse_quantity("0g") is None   # Coop shows "0g" for "Zafferano 3 bustine"
