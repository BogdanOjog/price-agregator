"""lidl.it weekly offer pages: promotions only, as JSON embedded in each product tile.

The week's pages are discovered from the home page (their slugs change every week). Prices are
regional: the Pisa store uses region 600 "Pontedera - Toscana" (decision at T0.7); a product
not sold there is skipped, and different prices across regions are flagged for review."""

import json
import re
from datetime import UTC, date, datetime

from selectolax.lexbor import LexborHTMLParser

from pisaprices.config.schemas import StoreConfig
from pisaprices.domain.enums import Chain, Channel, Mechanic, Program, Source
from pisaprices.domain.money import to_cents
from pisaprices.ingest.base import ContextError
from pisaprices.ingest.categories import CategoryIndex
from pisaprices.ingest.dates import rome_date, rome_date_from_iso
from pisaprices.ingest.http import PoliteClient
from pisaprices.ingest.records import ParsedBatch, ProductRecord, ProductRef, PromotionRecord
from pisaprices.ingest.snapshots import RawSnapshot
from pisaprices.ingest.units import parse_quantity

HOST = "www.lidl.it"
BASE = f"https://{HOST}"
REGION = "600"
CATEGORY_PREFIX = "I mondi del bisogno/"
WEEKLY_PAGE = re.compile(r'href="(?:https://www\.lidl\.it)?(/c/[a-z0-9-]+-kw-\d+-\d+/a\d+)"')


def weekly_pages(html: str) -> list[str]:
    return list(dict.fromkeys(WEEKLY_PAGE.findall(html)))


def _date_from_epoch(seconds: int | None) -> date | None:
    return rome_date(datetime.fromtimestamp(seconds, UTC)) if seconds else None


class LidlOffers:
    source = Source.LIDL_OFFERS
    chain = Chain.LIDL
    allowed_hosts = frozenset({HOST})
    forbidden_params = ("q", "id")
    forbidden_paths = ("/q/search", "/cc.js", "/cdn/assets/cwv/", *(f"/{d}" for d in "123456789"))

    def __init__(self, store: StoreConfig, categories: CategoryIndex) -> None:
        self.store = store
        self.categories = categories
        self._pages: list[str] = []

    def cookies(self):
        return None

    def verify_context(self, client: PoliteClient) -> None:
        home, _ = client.get(f"{BASE}/", "home")
        self._pages = weekly_pages(home.text)
        if not self._pages:
            raise ContextError("lidl.it home page links no weekly offer page (-kw-<week>-<year>): layout changed?")

    def site_categories(self, client: PoliteClient) -> dict[str, str]:
        """Lidl publishes no category tree for offers: the check is that weekly pages exist."""
        self.verify_context(client)
        return {}

    def promo_titles(self, snapshot: RawSnapshot) -> list[tuple[str, str]]:
        """(coarse category, title) of every product of a weekly page sold in the Pisa region."""
        found = []
        for tile in LexborHTMLParser(snapshot.text()).css("[data-grid-data]"):
            data = json.loads(tile.attributes["data-grid-data"])
            if REGION in (data.get("regionsV2") or {}):
                coarse = (data.get("keyfacts") or {}).get("wonCategoryPrimary", "").removeprefix(CATEGORY_PREFIX)
                found.append((coarse, data.get("fullTitle") or data["title"]))
        return found

    def fetch(self, client: PoliteClient, source_keys: list[str]) -> None:
        if not source_keys:   # no selected category maps to Lidl
            return
        for path in self._pages:
            client.get(f"{BASE}{path}", path.split("/")[2], context={"page": path})

    def parse(self, snapshot: RawSnapshot) -> ParsedBatch:
        tiles = LexborHTMLParser(snapshot.text()).css("[data-grid-data]")
        if not tiles:
            raise ValueError("no data-grid-data tiles (layout changed?)")
        products, promotions, seen = [], [], set()
        for tile in tiles:
            data = json.loads(tile.attributes["data-grid-data"])
            product_id = str(data["productId"])
            if product_id in seen:
                continue
            seen.add(product_id)
            parsed = self._item(data, product_id, snapshot.fetched_at)
            if parsed is None:
                continue
            product, promos = parsed
            products.append(product)
            promotions += promos
        return ParsedBatch(products=products, promotions=promotions)

    def _item(self, data: dict, product_id: str, observed_at: str):
        regions = data.get("regionsV2") or {}
        if REGION not in regions:
            return None   # not sold in the Tuscan region
        title = data.get("fullTitle") or data["title"]
        coarse = (data.get("keyfacts") or {}).get("wonCategoryPrimary", "").removeprefix(CATEGORY_PREFIX)
        if not self.categories.is_ours(self.source, coarse, title):
            return None   # not food, or a category that is not selected

        price_id = regions[REGION]["regionPriceId"]
        regional = len({r["regionPriceId"] for r in regions.values()}) > 1
        prices = (data.get("regionsPrices") or {}).get(price_id) or {}
        packaging = ""
        for kind in prices.values():
            body = kind.get("price") if isinstance(kind.get("price"), dict) else kind
            packaging = packaging or (body.get("packaging") or {}).get("text", "")
        quantity = parse_quantity(packaging) or parse_quantity(title)
        product = ProductRecord(
            chain=self.chain, external_id=product_id, name=title,
            brand=(data.get("brand") or {}).get("name") or None,
            net_qty=quantity.amount if quantity else None, unit=quantity.unit if quantity else None,
            variable_weight=bool(quantity and quantity.variable_weight), source_category_key=coarse or None,
            category_slug=self.categories.classify(self.source, coarse, title),
            product_url=f"{BASE}{data['canonicalUrl']}" if data.get("canonicalUrl") else None, image_url=data.get("image"),
        )
        ref = ProductRef(chain=self.chain, external_id=product_id, name=title,
                         net_qty=quantity.amount if quantity else None, unit=quantity.unit if quantity else None)

        promotions = []
        for kind, value in prices.items():
            if kind == "currentPrice":
                body, program = value, None
            elif kind == "currentLidlPlusPrice":
                body, program = value.get("price") or {}, Program.LIDL_PLUS
            else:
                continue   # an unknown price kind: not guessed
            if body.get("price") is None:
                continue
            discount = body.get("discount") or {}
            prior = discount.get("deletedPrice") or body.get("oldPrice")
            start = rome_date_from_iso(body["startDate"]) if body.get("startDate") else _date_from_epoch(data.get("storeStartDate"))
            until = rome_date_from_iso(body["endDate"]) if body.get("endDate") else _date_from_epoch(data.get("storeEndDate"))
            promotions.append(PromotionRecord(
                chain=self.chain, store_slug=self.store.slug, mechanic=Mechanic.PRICE_CUT,
                params={"promo_price_cents": to_cents(body["price"])}, items=[ref], program=program,
                channel=Channel.STORE, valid_from=start, valid_until=until,
                prior_price_cents=to_cents(prior) if prior else None, needs_review=regional,
                raw_text=json.dumps({kind: value}, ensure_ascii=False)[:2000], observed_at=observed_at,
            ))
        return product, promotions
