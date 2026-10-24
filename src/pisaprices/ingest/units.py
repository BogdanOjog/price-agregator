"""Package quantities from store text ("500 gr", "6 x 125 g", "120 g (sgocc.)", "Sfusa al kg").

Rules, in priority order:
0. eggs are counted, not weighed: "6 uova (53-63 g)" is 6 pieces;
1. drained weight ("sgocciolato") wins over net weight;
2. multipack "N x Q" / "N pz x Q" / "N pz da Q" multiplies;
3. a range "190/200 g" keeps the lower bound and is marked approximate;
4. otherwise the last plain quantity in the text ("3 pz 375 gr" means 375 g in total), also when
   the unit comes first ("g. 250");
5. otherwise a piece count ("6 uova", "96 pz");
6. otherwise "al kg" means sold by weight.
Unrecognised text returns None: the caller decides (never guess)."""

import re

from pisaprices.domain.enums import Unit
from pisaprices.domain.quantity import Quantity

NUM = r"(\d+(?:[.,]\d+)?)"
UNITS = {
    "kg": (Unit.GRAM, 1000), "g": (Unit.GRAM, 1), "gr": (Unit.GRAM, 1), "grammi": (Unit.GRAM, 1),
    "l": (Unit.MILLILITRE, 1000), "lt": (Unit.MILLILITRE, 1000), "litro": (Unit.MILLILITRE, 1000),
    "litri": (Unit.MILLILITRE, 1000), "dl": (Unit.MILLILITRE, 100), "cl": (Unit.MILLILITRE, 10),
    "ml": (Unit.MILLILITRE, 1),
}
UNIT = r"(kg|grammi|gr|g|litri|litro|lt|l|dl|cl|ml)\b"
PIECES = r"(?:pz|pezzi)\.?"

DRAINED_AFTER = re.compile(rf"sgocc\w*\.?\)?\s*:?\s*{NUM}\s*{UNIT}")
DRAINED_BEFORE = re.compile(rf"{NUM}\s*{UNIT}\s*\(?\s*sgocc")
MULTIPACK = re.compile(rf"(\d+)\s*(?:{PIECES}\s*)?(?:x|da)\s*{NUM}\s*{UNIT}")
RANGE = re.compile(rf"{NUM}\s*/\s*{NUM}\s*{UNIT}")
PLAIN = re.compile(rf"{NUM}\s*{UNIT}")
UNIT_FIRST = re.compile(r"\b(kg|gr|g|ml|cl|lt|l)\.\s*(\d+(?:[.,]\d+)?)")  # Conad: "Pappardelle g. 250"
PIECE_COUNT = re.compile(rf"(\d+)\s*{PIECES}(?=\W|$)")
EGG_COUNT = re.compile(r"(\d+)\s*uova\b|\buova\b.*?\b(\d+)\s*(?:pz|pezzi)\b|\buova\b.*?\bx\s*(\d+)\b")
# products that mention eggs as an ingredient ("pasta all'uovo con 6 uova") are weighed as usual
NOT_EGGS = re.compile(r"pasta|uovo|tagliatell|fettucc|pappardell|lasagn|tortell|raviol|biscott|cioccolat|dolc|merend")
BY_WEIGHT = re.compile(r"\bal\s+kg\b")


def _number(text: str) -> float:
    return float(text.replace(",", "."))


class _ZeroAmount(Exception):
    pass


def _amount(number: str, unit: str) -> tuple[float, Unit]:
    base, factor = UNITS[unit]
    amount = round(_number(number) * factor, 6)
    if amount <= 0:
        raise _ZeroAmount
    return amount, base


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().replace("×", "x")).strip()


def parse_quantity(text: str) -> Quantity | None:
    """None when there is no quantity, or only a zero one (Coop shows "0g" for "Zafferano 3 bustine")."""
    try:
        return _parse_quantity(_normalise(text))
    except _ZeroAmount:
        return None


def _parse_quantity(text: str) -> Quantity | None:

    if (match := EGG_COUNT.search(text)) and not NOT_EGGS.search(text):
        count = int(next(group for group in match.groups() if group))
        return Quantity(count, Unit.PIECE, pieces=count)

    for pattern in (DRAINED_AFTER, DRAINED_BEFORE):
        if match := pattern.search(text):
            amount, unit = _amount(*match.groups())
            return Quantity(amount, unit, drained=True)

    if match := MULTIPACK.search(text):
        count, number, unit_text = match.groups()
        amount, unit = _amount(number, unit_text)
        return Quantity(round(int(count) * amount, 6), unit, pieces=int(count))

    if match := RANGE.search(text):
        low, high, unit_text = match.groups()
        amount, unit = _amount(min(low, high, key=_number), unit_text)
        return Quantity(amount, unit, approximate=True)

    plain = PLAIN.findall(text) or [(number, unit) for unit, number in UNIT_FIRST.findall(text)]
    pieces = PIECE_COUNT.search(text)
    if plain:
        amount, unit = _amount(*plain[-1])
        return Quantity(amount, unit, pieces=int(pieces.group(1)) if pieces else None)

    if pieces:
        count = int(pieces.group(1))
        return Quantity(count, Unit.PIECE, pieces=count)

    if BY_WEIGHT.search(text):
        return Quantity(1000, Unit.GRAM, variable_weight=True)

    return None


def quantity_from_fields(value: float, unit_of_measure: str) -> Quantity | None:
    """Structured quantities, e.g. Conad's data-product: netQuantity 0.5 + netQuantityUm "KG"."""
    unit_text = unit_of_measure.strip().lower()
    if unit_text in ("pz", "pezzi", "nr", "n"):
        return Quantity(value, Unit.PIECE, pieces=int(value)) if value > 0 else None
    if unit_text not in UNITS or value <= 0:
        return None
    amount, unit = _amount(str(value), unit_text)
    return Quantity(amount, unit)
