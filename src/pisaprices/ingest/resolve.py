"""ProductRef -> product id. Pure: candidates are passed in.

Order: external_id, then EAN within the same chain, then fuzzy name + same quantity. Only the
first two link; a fuzzy match is a proposal for the review queue. Private labels never match
across chains because only same-chain candidates are ever considered."""

import re
import unicodedata
from dataclasses import dataclass

from rapidfuzz import fuzz

from pisaprices.ingest.records import ProductRef

QUANTITY_TOLERANCE = 0.01


@dataclass(frozen=True)
class Candidate:
    id: int
    chain: str
    external_id: str
    ean: str | None
    name: str
    net_qty: float | None
    unit: str | None


@dataclass(frozen=True)
class Resolution:
    product_id: int | None = None       # linked
    proposal_id: int | None = None      # fuzzy candidate, needs confirmation
    score: float = 0.0
    method: str = "none"                # external_id | ean | fuzzy | none


def _normalise(name: str) -> str:
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    name = re.sub(r"\d+(?:[.,]\d+)?\s*(?:kg|gr|g|ml|cl|lt|l|pz)\b", " ", name)  # quantities compared separately
    return re.sub(r"[^a-z0-9]+", " ", name).strip()


def _same_quantity(ref: ProductRef, candidate: Candidate) -> bool:
    if ref.net_qty is None or candidate.net_qty is None:
        return False
    if (ref.unit.value if ref.unit else None) != candidate.unit:
        return False
    return abs(ref.net_qty - candidate.net_qty) <= QUANTITY_TOLERANCE * candidate.net_qty


def resolve(ref: ProductRef, candidates: list[Candidate], threshold: float) -> Resolution:
    same_chain = [c for c in candidates if c.chain == ref.chain.value]
    if ref.external_id:
        for c in same_chain:
            if c.external_id == ref.external_id:
                return Resolution(product_id=c.id, score=100, method="external_id")
    if ref.ean:
        matches = [c for c in same_chain if c.ean == ref.ean]
        if len(matches) == 1:
            return Resolution(product_id=matches[0].id, score=100, method="ean")

    target = _normalise(ref.name)
    best, best_score = None, 0.0
    for c in same_chain:
        if not _same_quantity(ref, c):
            continue
        score = fuzz.token_sort_ratio(target, _normalise(c.name))
        if score > best_score:
            best, best_score = c, score
    if best is not None and best_score >= threshold:
        return Resolution(proposal_id=best.id, score=best_score, method="fuzzy")
    return Resolution()
