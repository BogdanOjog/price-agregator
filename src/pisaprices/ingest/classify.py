"""Tier, flags and canonical format of a product, from config/tier_rules.yaml. Pure."""

import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field
from ruamel.yaml import YAML

from pisaprices.domain.enums import Chain, Unit


class LineRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    chain: Chain
    match: list[str] = Field(min_length=1)
    tier: int = Field(ge=1, le=3)


class TierRules(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    lines: list[LineRule] = []
    premium_keywords: list[str] = []
    other_brands_tier: int | None = Field(default=None, ge=1, le=3)
    flags: dict[str, list[str]] = {}


def load_tier_rules(path: Path) -> TierRules:
    if not path.exists():
        return TierRules()
    return TierRules.model_validate(YAML(typ="safe").load(path.read_text(encoding="utf-8")) or {})


@dataclass(frozen=True)
class Classification:
    tier: int | None
    flags: tuple[str, ...]
    format: str | None


def _has(words: list[str], text: str) -> bool:
    return any(re.search(r"(?<!\w)" + re.escape(word.lower()), text) for word in words)


def product_format(net_qty: float | None, unit: str | None, variable_weight: bool) -> str | None:
    """'500 g', '1 kg', '1 l', '6 pz', 'al kg': the pack format used by canonical products."""
    if variable_weight:
        return "al kg"
    if net_qty is None or unit is None:
        return None
    if unit == Unit.GRAM and net_qty >= 1000 and net_qty % 1000 == 0:
        return f"{net_qty / 1000:g} kg"
    if unit == Unit.MILLILITRE and net_qty >= 1000 and net_qty % 1000 == 0:
        return f"{net_qty / 1000:g} l"
    return f"{net_qty:g} {unit}"


def classify(rules: TierRules, chain: str, brand: str | None, name: str, net_qty: float | None,
             unit: str | None, variable_weight: bool) -> Classification:
    text = f"{brand or ''} {name}".lower()
    tier = None
    for line in rules.lines:
        if line.chain.value == chain and _has(line.match, text):
            tier = line.tier
            break
    else:
        if brand and rules.other_brands_tier:
            tier = rules.other_brands_tier
    if tier is not None and _has(rules.premium_keywords, text):
        tier = 3
    flags = tuple(sorted(flag for flag, words in rules.flags.items() if _has(words, text)))
    return Classification(tier, flags, product_format(net_qty, unit, variable_weight))
