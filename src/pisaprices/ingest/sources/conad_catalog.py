"""spesaonline.conad.it (AEM): catalog of one store, with the user's own session.

Store selection is protected (BotD + reCAPTCHA) and is never automated: the user selects the
store in a browser and saves the session cookies (`pisaprices conad-session import`). Every run
first checks that the session is still on the configured store; otherwise it stops, because the
prices without a store are generic and differ from the store's."""

import json
import re
from datetime import date
from pathlib import Path

from selectolax.lexbor import LexborHTMLParser, LexborNode

from pisaprices.config.schemas import StoreConfig
from pisaprices.domain.enums import Chain, Channel, Mechanic, Program, Source, Unit
from pisaprices.domain.money import to_cents, unit_price
from pisaprices.domain.quantity import Quantity
from pisaprices.ingest.base import ContextError
from pisaprices.ingest.categories import CategoryIndex
from pisaprices.ingest.http import PoliteClient
from pisaprices.ingest.prices import parse_eur, parse_site_unit_price, unit_price_disagrees
from pisaprices.ingest.promotions import read_terms
from pisaprices.ingest.records import ParsedBatch, PriceRecord, ProductRecord, ProductRef, PromotionRecord
from pisaprices.ingest.snapshots import RawSnapshot
from pisaprices.ingest.units import parse_quantity, quantity_from_fields

HOST = "spesaonline.conad.it"
BASE = f"https://{HOST}"
SESSION_COOKIES = ("ecSess", "ecRoute", "affinity", "ecServerUUID", "ecAccess", "ecCooperative")
EXPIRED = ("Conad session is not on store {store}: select 'Ordina e Ritira' and the store in a browser, "
           "then run `pisaprices conad-session import <copied cURL file>`")


def read_session(path: Path) -> dict[str, str]:
    if not path.exists():
        raise FileNotFoundError(f"{path} missing: save the Conad session with `pisaprices conad-session import`")
    cookies = {}
    for part in path.read_text(encoding="utf-8").strip().split(";"):
        if "=" in part:
            name, value = part.split("=", 1)
            cookies[name.strip()] = value.strip().strip('"')
    missing = [name for name in ("ecSess", "ecAccess") if name not in cookies]
    if missing:
        raise ValueError(f"{path}: cookies {missing} missing — the store was not selected when the cURL was copied")
    return cookies


def point_of_service(html: str) -> dict | None:
    """The `pointOfService = {...}` object the page embeds; None when no store is selected."""
    match = re.search(r"pointOfService\s*=\s*", html)
    if not match:
        return None
    try:
        value, _ = json.JSONDecoder().raw_decode(html[match.end():])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def category_links(html: str) -> dict[str, str]:
    """Category code -> path, from the links of a page ("/c/latte--0404")."""
    return {code: f"/c/{slug}--{code}" for slug, code in re.findall(r'href="/c/([^"#?]+?)--(\d+)"', html)}


def _in_carousel(node: LexborNode) -> bool:
    parent = node.parent
    while parent is not None:
        if "swiper" in (parent.attributes.get("class") or ""):
            return True
        parent = parent.parent
    return False


def _text(node: LexborNode | None) -> str:
    return re.sub(r"\s+", " ", node.text(separator=" ")).strip() if node else ""


class ConadCatalog:
    source = Source.CONAD_CATALOG
    chain = Chain.CONAD
    allowed_hosts = frozenset({HOST})
    forbidden_params = ()
    forbidden_paths = ("/api/common/protection", "/api/ecommerce/it-it.set-ecaccess")

    def __init__(self, store: StoreConfig, categories: CategoryIndex, unit_price_tolerance: float, session_file: Path) -> None:
        if not store.external_id:
            raise ValueError(f"store '{store.slug}' needs external_id (Conad store code)")
        self.store = store
        self.categories = categories
        self.tolerance = unit_price_tolerance
        self.session = read_session(session_file)
        self._links: dict[str, str] = {}

    def cookies(self) -> dict[str, str]:
        return {name: value for name, value in self.session.items() if name in SESSION_COOKIES}

    def verify_context(self, client: PoliteClient) -> None:
        home, _ = client.get(f"{BASE}/", "home")
        pos = point_of_service(home.text)
        if not pos or pos.get("name") != self.store.external_id:
            raise ContextError(EXPIRED.format(store=self.store.external_id))
        self._links = category_links(home.text)

    def site_categories(self, client: PoliteClient) -> dict[str, str]:
        home, _ = client.get(f"{BASE}/", "home")
        names: dict[str, str] = {}
        for link in LexborHTMLParser(home.text).css('a[href^="/c/"]'):
            match = re.fullmatch(r"/c/[^#?]+?--(\d+)", link.attributes.get("href") or "")
            if match and match.group(1) not in names:
                names[match.group(1)] = _text(link) or match.group(1)
        return names

    def fetch(self, client: PoliteClient, source_keys: list[str]) -> None:
        for code in source_keys:
            path = self._links.get(code)
            if path is None:
                raise ContextError(f"Conad category {code} is not linked from the home page any more (run `pisaprices categories check`)")
            first, _ = client.get(f"{BASE}{path}", f"cat {code} p1", context={"key": code, "page": 1})
            if (pos := point_of_service(first.text)) and pos.get("name") != self.store.external_id:
                raise ContextError(EXPIRED.format(store=self.store.external_id))
            tree = LexborHTMLParser(first.text)
            endpoint = tree.css_first("[data-search-endpoint]")
            pages = [int(a.attributes["data-page"]) for a in tree.css(".component-Pagination [data-page]")]
            if endpoint is None:
                continue
            for page in range(2, max(pages, default=1) + 1):
                client.get(f"{BASE}{endpoint.attributes['data-search-endpoint']}", f"cat {code} p{page}",
                           params={"page": page}, context={"key": code, "page": page})

    def parse(self, snapshot: RawSnapshot) -> ParsedBatch:
        html = snapshot.text()
        if re.search(r"pointOfService\s*=", html):   # full pages carry it; the page-N fragments do not
            pos = point_of_service(html)
            if not pos or pos.get("name") != self.store.external_id:
                found = pos.get("name") if pos else "none (generic prices)"
                raise ValueError(f"page belongs to store {found}, not {self.store.external_id}")
        observed = date.fromisoformat(snapshot.fetched_at[:10])
        key = snapshot.context.get("key", "")
        products, prices, promotions, seen = [], [], [], set()
        for card in LexborHTMLParser(html).css("div.component-ProductCard"):
            if _in_carousel(card):   # "in offerta" carousels show products of other categories
                continue
            node = card.css_first("[data-product]")
            if node is None:
                continue
            data = json.loads(node.attributes["data-product"])
            if data["code"] in seen:
                continue
            seen.add(data["code"])
            parsed = self._card(card, data, key, snapshot.fetched_at, observed)
            if parsed:
                product, price, promos = parsed
                products.append(product)
                prices.append(price)
                promotions += promos
        return ParsedBatch(products=products, prices=prices, promotions=promotions)

    def _card(self, card: LexborNode, data: dict, key: str, observed_at: str, observed: date):
        code, name = str(data["code"]), data["nome"]
        regular = to_cents(data.get("basePrice") or 0)
        shown = parse_eur(_text(card.css_first(".product-price"))) or regular
        if not regular:
            return None
        quantity = (quantity_from_fields(data.get("netQuantity") or 0, data.get("netQuantityUm") or "")
                    or parse_quantity(name) or parse_quantity(_text(card.css_first(".product-quantity"))))
        # "Pompelmi rossi al kg": the JSON says 1 KG, but the price is for one estimated piece
        # ("400 g", "0,83€", "2,08 €/Kg"). Such products are sold by weight: store prices per kg.
        piece = parse_quantity(_text(card.css_first(".product-quantity")))
        per_kg = None
        if "al kg" in name.lower() and piece and piece.unit is Unit.GRAM and not piece.variable_weight:
            per_kg = 1000 / piece.amount
            regular, shown = round(regular * per_kg), round(shown * per_kg)
            quantity = Quantity(1000, Unit.GRAM, variable_weight=True)
        link = card.css_first("a.product")
        image = card.css_first(".product-img img")

        site_unit = parse_site_unit_price(_text(card.css_first(".product-price-kg")))
        mismatch = unit_price_disagrees(shown, quantity, site_unit, self.tolerance)   # the site's €/kg uses the shown price

        product = ProductRecord(
            chain=self.chain, external_id=code, name=name, brand=data.get("marchio"),
            net_qty=quantity.amount if quantity else None, unit=quantity.unit if quantity else None,
            variable_weight=bool(quantity and quantity.variable_weight), source_category_key=key or None,
            category_slug=self.categories.classify(self.source, key, name),
            product_url=f"{BASE}{link.attributes['href']}" if link else None,
            image_url=(image.attributes.get("data-src") or image.attributes.get("src")) if image else None,
        )
        price = PriceRecord(chain=self.chain, external_id=code, store_slug=self.store.slug, price_cents=regular,
                            unit_price=unit_price(regular, quantity) if quantity else None, observed_at=observed_at,
                            needs_review=mismatch)

        ref = ProductRef(chain=self.chain, external_id=code, name=name,
                         net_qty=quantity.amount if quantity else None, unit=quantity.unit if quantity else None)
        promotions = []
        badge = card.css_first(".component-PromoBadgeCard")
        if badge is not None and shown < regular:
            # a delivery icon on the badge marks a spesa-online offer
            online = badge.css_first(".icon-delivery") is not None
            terms = read_terms(f"{_text(badge)} {_text(card.css_first('.discount-date'))}", observed)
            promotions.append(PromotionRecord(
                chain=self.chain, store_slug=self.store.slug, mechanic=Mechanic.PRICE_CUT,
                params={"promo_price_cents": shown}, items=[ref], channel=Channel.ONLINE if online else Channel.BOTH,
                valid_until=terms.valid_until, prior_price_cents=regular, needs_review=terms.needs_review,
                raw_text=f"{_text(badge)} {_text(card.css_first('.discount-date'))}", observed_at=observed_at,
            ))
        card_offer = card.css_first(".component-PromoBadgePotentialDiscount")
        if card_offer is not None:
            card_price = parse_eur(_text(card_offer.css_first(".price.sale")))
            if card_price and per_kg:
                card_price = round(card_price * per_kg)
            if card_price and card_price < regular:
                promotions.append(PromotionRecord(
                    chain=self.chain, store_slug=self.store.slug, mechanic=Mechanic.PRICE_CUT,
                    params={"promo_price_cents": card_price}, items=[ref], program=Program.CONAD_CARTA_INSIEME,
                    channel=Channel.BOTH, prior_price_cents=regular, raw_text=_text(card_offer), observed_at=observed_at,
                ))
        return product, price, promotions
