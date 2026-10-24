"""eurospin.com public offers listing: promotions only, chain-wide (both Pisa stores have the same).

One listing per coarse category (by_category facet) so every card has a known category; next
pages come from the links the page publishes. data-promotion-type decides who gets the price
(decision at T0.7): family -> Eurospin Family card; offerta, ribassati -> everyone; limitati ->
everyone, limited stock. The "Prezzo Family" label printed on every card is not used."""

import re
from datetime import date

from selectolax.lexbor import LexborHTMLParser, LexborNode

from pisaprices.config.schemas import StoreConfig
from pisaprices.domain.enums import Chain, Channel, Mechanic, Program, Source
from pisaprices.ingest.categories import CategoryIndex
from pisaprices.ingest.http import PoliteClient
from pisaprices.ingest.prices import parse_eur
from pisaprices.ingest.promotions import read_terms
from pisaprices.ingest.records import ParsedBatch, ProductRecord, ProductRef, PromotionRecord
from pisaprices.ingest.snapshots import RawSnapshot
from pisaprices.ingest.units import parse_quantity

HOST = "www.eurospin.com"
BASE = f"https://{HOST}"
LISTING = f"{BASE}/it-it/prodotti/"
OFFER_TYPES = ("offerta", "family", "limitati", "ribassati")
MAX_PAGES = 100


def _text(node: LexborNode | None) -> str:
    return re.sub(r"\s+", " ", node.text(separator=" ")).strip() if node else ""


def facet_categories(html: str) -> dict[str, str]:
    """by_category value -> label, from the listing's filter."""
    found = {}
    for option in LexborHTMLParser(html).css(".product-listing__option"):
        node = option.css_first('input[name="by_category"]')
        if node is not None and node.attributes.get("value"):
            found[node.attributes["value"]] = _text(option.css_first(".product-listing__option-text")) or node.attributes["value"]
    for node in LexborHTMLParser(html).css('input[name="by_category"]'):
        found.setdefault(node.attributes.get("value") or "", node.attributes.get("value") or "")
    found.pop("", None)
    return found


def next_page_url(html: str, current: int) -> str | None:
    """The page's own link to page current+1, if it publishes one."""
    for href in re.findall(r'href="([^"]*[?&]page=(\d+)[^"]*)"', html):
        url, page = href
        if int(page) == current + 1:
            url = url.replace("&amp;", "&")
            return url if url.startswith("http") else f"{BASE}{url}"
    return None


class EurospinOffers:
    source = Source.EUROSPIN_OFFERS
    chain = Chain.EUROSPIN
    allowed_hosts = frozenset({HOST})
    forbidden_params = ()
    forbidden_paths = ("/leaflet/", "/search-results/", "/area-riservata/")

    def __init__(self, store: StoreConfig, categories: CategoryIndex) -> None:
        self.store = store
        self.categories = categories

    def cookies(self):
        return None

    def verify_context(self, client: PoliteClient) -> None:
        """No session: offers are the same for every visitor and for both Pisa stores."""

    def site_categories(self, client: PoliteClient) -> dict[str, str]:
        params = [("by_typeOffer", t) for t in OFFER_TYPES]
        page, _ = client.get(LISTING, "listing facets", params=params)
        return facet_categories(page.text)

    def promo_titles(self, snapshot: RawSnapshot) -> list[tuple[str, str]]:
        """(coarse category, title) of every card of a listing page."""
        key = snapshot.context.get("key") or ""
        return [(key, _text(card.css_first(".product-card__title")))
                for card in LexborHTMLParser(snapshot.text()).css("li.product-card[data-product-code]")]

    def fetch(self, client: PoliteClient, source_keys: list[str]) -> None:
        for key in source_keys:
            params = [("by_category", key), *(("by_typeOffer", t) for t in OFFER_TYPES)]
            response, _ = client.get(LISTING, f"offers {key} p1", params=params, context={"key": key, "page": 1})
            page = 1
            while (url := next_page_url(response.text, page)) and page < MAX_PAGES:
                page += 1
                response, _ = client.get(url, f"offers {key} p{page}", context={"key": key, "page": page})

    def parse(self, snapshot: RawSnapshot) -> ParsedBatch:
        observed = date.fromisoformat(snapshot.fetched_at[:10])
        key = snapshot.context.get("key")
        products, promotions, seen = [], [], set()
        for card in LexborHTMLParser(snapshot.text()).css("li.product-card[data-product-code]"):
            code = card.attributes["data-product-code"]
            if code in seen:
                continue
            seen.add(code)
            title = _text(card.css_first(".product-card__title"))
            if not title:
                raise ValueError(f"card {code} has no title (layout changed?)")
            if not self.categories.is_ours(self.source, key, title):
                continue   # a category that is not selected
            category = self.categories.classify(self.source, key, title)
            product, promotion = self._card(card, code, title, category, key, snapshot.fetched_at, observed)
            products.append(product)
            if promotion:
                promotions.append(promotion)
        return ParsedBatch(products=products, promotions=promotions)

    def _card(self, card: LexborNode, code: str, title: str, category: str | None, key: str | None, observed_at: str, observed: date):
        quantity = parse_quantity(f"{title} {_text(card.css_first('.product-card__meta'))}")
        link = card.css_first("a.product-card__media-link")
        image = card.css_first("img.product-card__img")
        product = ProductRecord(
            chain=self.chain, external_id=code, name=title, brand=_text(card.css_first(".product-card__brand")) or None,
            net_qty=quantity.amount if quantity else None, unit=quantity.unit if quantity else None,
            variable_weight=bool(quantity and quantity.variable_weight), source_category_key=key, category_slug=category,
            product_url=link.attributes.get("href") if link else None, image_url=image.attributes.get("src") if image else None,
        )
        promo_price = parse_eur(_text(card.css_first(".product-card__price-current")))
        if not promo_price:
            return product, None
        full = parse_eur(_text(card.css_first(".product-card__price-full")))
        kind = (card.attributes.get("data-promotion-type") or "").strip()
        terms = read_terms(_text(card.css_first(".product-card__badge-row")), observed)
        mechanic = Mechanic.PRICE_CUT if kind in OFFER_TYPES else Mechanic.UNKNOWN
        promotion = PromotionRecord(
            chain=self.chain, store_slug=self.store.slug, mechanic=mechanic,
            params={"promo_price_cents": promo_price} if mechanic is Mechanic.PRICE_CUT else {},
            items=[ProductRef(chain=self.chain, external_id=code, name=title,
                              net_qty=quantity.amount if quantity else None, unit=quantity.unit if quantity else None)],
            program=Program.EUROSPIN_FAMILY if kind == "family" else None, channel=Channel.STORE,
            is_sottocosto=kind == "limitati", valid_until=terms.valid_until,
            prior_price_cents=full if full and full > promo_price else None, needs_review=terms.needs_review,
            raw_text=f"type={kind or '(none)'} | {_text(card.css_first('.product-card__prices'))} | {_text(card.css_first('.product-card__badge-row'))}",
            observed_at=observed_at,
        )
        return product, promotion
