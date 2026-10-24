"""Promotions: typed parameters per mechanic, and a reader for the Italian promotion wording.

Collectors that get prices as data (JSON, price elements) build PRICE_CUT records directly and use
`read_terms` only for caps, minimum quantities and dates. Text that is not recognised yields
mechanic UNKNOWN with needs_review: a promotion is never guessed."""

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from pisaprices.domain.enums import Mechanic
from pisaprices.ingest.dates import parse_validity
from pisaprices.ingest.prices import parse_eur


class Params(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PriceCutParams(Params):
    mechanic: Literal[Mechanic.PRICE_CUT]
    promo_price_cents: int = Field(ge=0)


class MultibuyParams(Params):
    mechanic: Literal[Mechanic.MULTIBUY_N_FOR_M]
    buy: int = Field(ge=2)
    pay: int = Field(ge=1)

    @model_validator(mode="after")
    def _pay_less(self) -> "MultibuyParams":
        if self.pay >= self.buy:
            raise ValueError("pay must be less than buy")
        return self


class NthUnitParams(Params):
    mechanic: Literal[Mechanic.NTH_UNIT_DISCOUNT]
    nth: int = Field(ge=2)
    pct: float = Field(gt=0, le=100)   # discount on the nth unit


class Tier(Params):
    min_qty: int = Field(ge=1)
    unit_price_cents: int = Field(ge=0)


class TieredParams(Params):
    mechanic: Literal[Mechanic.TIERED_ALL_UNITS, Mechanic.TIERED_INCREMENTAL]
    tiers: list[Tier] = Field(min_length=1)

    @model_validator(mode="after")
    def _ordered(self) -> "TieredParams":
        quantities = [t.min_qty for t in self.tiers]
        if quantities != sorted(set(quantities)):
            raise ValueError("tiers must have increasing, distinct min_qty")
        return self


class ThresholdParams(Params):
    mechanic: Literal[Mechanic.BASKET_THRESHOLD]
    amount_cents: int = Field(gt=0)
    discount_cents: int = Field(gt=0)


class RewardParams(Params):
    mechanic: Literal[Mechanic.REWARD]
    reward_kind: Literal["voucher", "points", "gift"]
    value_cents: int | None = None


class UnknownParams(Params):
    mechanic: Literal[Mechanic.UNKNOWN]


PromotionParams = Annotated[
    PriceCutParams | MultibuyParams | NthUnitParams | TieredParams | ThresholdParams | RewardParams | UnknownParams,
    Field(discriminator="mechanic"),
]
_ADAPTER = TypeAdapter(PromotionParams)


def validate_params(mechanic: Mechanic, params: dict) -> dict:
    """Validate `params` for `mechanic`; returns the canonical dict stored in promotions.params."""
    model = _ADAPTER.validate_python({**params, "mechanic": mechanic})
    return model.model_dump(mode="json", exclude={"mechanic"})


# reading the wording ---------------------------------------------------------------------------

NTH_WORDS = {"secondo": 2, "terzo": 3, "quarto": 4, "2°": 2, "3°": 3, "4°": 4}

PLUS = re.compile(r"(?<![\d,.])(\d)\s*\+\s*(\d)(?![\d%])")                      # 1+1, 2+1
TIMES = re.compile(r"(?<![\d,.])(\d)\s*x\s*(\d)(?![\d,.]|\s*(?:g|gr|kg|ml|cl|l|lt|pz)\b)")  # 3x2, not 6 x 125 g
TAKE_PAY = re.compile(r"prendi\s*(\d+)\s*,?\s*paghi\s*(\d+)")
NTH = re.compile(r"(?:il\s+)?(secondo|terzo|quarto|2°|3°|4°)\s*(?:pezzo\s*)?(?:al|a)\s*(?:-\s*)?(\d+)\s*%")
NTH_HALF = re.compile(r"(?:il\s+)?(secondo|terzo|2°|3°)\s*(?:pezzo\s*)?a\s+met[aà]\s+prezzo")
FROM_NTH = re.compile(r"dal\s+(\d+)\s*°?\s*(?:pezzo|articolo)")
CAP = re.compile(r"max\.?\s*(\d+)\s*(?:pezzi|pz)?\.?\s*(?:acquistabili\s*)?per\s+(carta|ordine|scontrino|spesa|cliente)")
LIMIT = re.compile(r"acquisto\s+limitato\s*(?:a\s*)?(\d+)\s*(?:pezzi|pz)")
MIN_QTY = re.compile(r"acquisto\s+minimo\s*(?:di\s*)?(\d+)")
THRESHOLD = re.compile(r"spendi\s*(?:almeno\s*)?(?:€\s*)?(\d+(?:[.,]\d{1,2})?)\s*€?\s*(?:e\s+)?risparmi\s*(?:€\s*)?(\d+(?:[.,]\d{1,2})?)")
SOTTOCOSTO = re.compile(r"sottocosto")


@dataclass(frozen=True)
class PromoTerms:
    """Everything the wording says. `mechanic` is None when the text only carries modifiers."""

    mechanic: Mechanic | None = None
    params: dict = field(default_factory=dict)
    max_qty_per_card: int | None = None
    max_qty_per_receipt: int | None = None
    min_qty: int | None = None
    is_sottocosto: bool = False
    valid_from: date | None = None
    valid_until: date | None = None
    needs_review: bool = False


def read_terms(text: str, observed: date) -> PromoTerms:
    lowered = re.sub(r"\s+", " ", text.lower())
    mechanic, params, review = None, {}, False

    if match := TAKE_PAY.search(lowered):
        mechanic, params = Mechanic.MULTIBUY_N_FOR_M, {"buy": int(match[1]), "pay": int(match[2])}
    elif match := PLUS.search(lowered):
        paid, free = int(match[1]), int(match[2])
        mechanic, params = Mechanic.MULTIBUY_N_FOR_M, {"buy": paid + free, "pay": paid}
    elif (match := TIMES.search(lowered)) and int(match[1]) > int(match[2]):
        mechanic, params = Mechanic.MULTIBUY_N_FOR_M, {"buy": int(match[1]), "pay": int(match[2])}
    elif match := NTH.search(lowered):
        mechanic, params = Mechanic.NTH_UNIT_DISCOUNT, {"nth": NTH_WORDS[match[1]], "pct": float(match[2])}
    elif match := NTH_HALF.search(lowered):
        mechanic, params = Mechanic.NTH_UNIT_DISCOUNT, {"nth": NTH_WORDS[match[1]], "pct": 50.0}
    elif FROM_NTH.search(lowered):
        mechanic, review = Mechanic.TIERED_INCREMENTAL, True   # ambiguous by design: always reviewed
    elif match := THRESHOLD.search(lowered):
        mechanic = Mechanic.BASKET_THRESHOLD
        params = {"amount_cents": parse_eur(match[1]), "discount_cents": parse_eur(match[2])}

    per_card = per_receipt = None
    if match := CAP.search(lowered):
        if match[2] == "carta":
            per_card = int(match[1])
        else:
            per_receipt = int(match[1])
    if match := LIMIT.search(lowered):
        per_receipt = int(match[1])
    min_qty = int(match[1]) if (match := MIN_QTY.search(lowered)) else None

    validity = parse_validity(lowered, observed)
    return PromoTerms(
        mechanic=mechanic, params=params, max_qty_per_card=per_card, max_qty_per_receipt=per_receipt,
        min_qty=min_qty, is_sottocosto=bool(SOTTOCOSTO.search(lowered)), valid_from=validity.valid_from,
        valid_until=validity.valid_until, needs_review=review or validity.needs_review,
    )
