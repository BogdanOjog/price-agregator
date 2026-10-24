"""What a parser produces. Strict models: an unknown or missing field fails the run loudly
instead of storing wrong data."""

import hashlib
import json
from datetime import date

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pisaprices.domain.enums import Chain, Channel, Mechanic, ObservationSource, Program, ReviewKind, Unit
from pisaprices.ingest.promotions import validate_params


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProductRecord(Record):
    """A product seen in a catalog source (has the site's own id)."""

    chain: Chain
    external_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    brand: str | None = None
    ean: str | None = None
    net_qty: float | None = Field(default=None, gt=0)
    unit: Unit | None = None
    variable_weight: bool = False
    source_category_key: str | None = None
    category_slug: str | None = None     # own category, from categories.yaml
    product_url: str | None = None
    image_url: str | None = None


class PriceRecord(Record):
    """The regular price of a product at a store, as observed."""

    chain: Chain
    external_id: str
    store_slug: str
    price_cents: int = Field(ge=0)
    unit_price: float | None = None      # ours, €/kg or €/l or €/pz
    observed_at: str                     # ISO UTC
    source: ObservationSource = ObservationSource.ONLINE
    needs_review: bool = False


class ProductRef(Record):
    """How a promotion points at a product; `resolve` turns it into a product id."""

    chain: Chain
    external_id: str | None = None
    ean: str | None = None
    name: str
    net_qty: float | None = None
    unit: Unit | None = None

    def key(self) -> str:
        if self.external_id:
            return f"{self.chain}:id:{self.external_id}"
        if self.ean:
            return f"{self.chain}:ean:{self.ean}"
        return f"{self.chain}:name:{self.name.lower()}:{self.net_qty}:{self.unit}"


class PromotionRecord(Record):
    chain: Chain
    store_slug: str | None = None        # None = chain-wide
    mechanic: Mechanic
    params: dict = {}
    items: list[ProductRef] = Field(min_length=1)
    program: Program | None = None
    channel: Channel = Channel.STORE
    min_qty: int | None = Field(default=None, ge=1)
    max_qty_per_card: int | None = Field(default=None, ge=1)
    max_qty_per_receipt: int | None = Field(default=None, ge=1)
    is_sottocosto: bool = False
    stock_qty: int | None = None
    requires_activation: bool = False
    stackable: bool = False
    excludes_already_promoted: bool = False
    valid_from: date | None = None
    valid_until: date | None = None
    prior_price_cents: int | None = Field(default=None, ge=0)
    raw_text: str
    needs_review: bool = False
    observed_at: str

    @model_validator(mode="before")
    @classmethod
    def _typed_params(cls, data: dict) -> dict:
        """params must match the mechanic; UNKNOWN is always reviewed."""
        if isinstance(data, dict) and "mechanic" in data:
            mechanic = Mechanic(data["mechanic"])
            data = {**data, "params": validate_params(mechanic, data.get("params") or {})}
            if mechanic is Mechanic.UNKNOWN:
                data["needs_review"] = True
        return data

    def natural_key(self) -> str:
        """Same promotion seen in two runs -> same key. Text, review flag and time are not identity."""
        identity = self.model_dump(mode="json", exclude={"raw_text", "needs_review", "observed_at", "items"})
        identity["items"] = sorted(ref.key() for ref in self.items)
        canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()


class ReviewRecord(Record):
    """Something the parser could not decide alone (e.g. a promo product that no category rule matched)."""

    kind: ReviewKind
    key: str                 # deduplication key, stable across runs
    payload: dict = {}


class ParsedBatch(Record):
    products: list[ProductRecord] = []
    prices: list[PriceRecord] = []
    promotions: list[PromotionRecord] = []
    reviews: list[ReviewRecord] = []

    @property
    def items(self) -> int:
        """Products seen: catalog products, or (promo-only sources) distinct promoted products."""
        return len(self.products) or len({ref.key() for p in self.promotions for ref in p.items})
