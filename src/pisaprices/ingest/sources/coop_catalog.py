"""prenotalaspesa.coopfirenze.it (Salesforce Commerce Cloud): catalog of the Cascina pickup point.

Session: home page, then the store's own "Seleziona negozio" request (storeId and polygon read
from /stores), then a check of Stores-CurrentStore. Pages: the category URL, then the site's
"show more" links with the robots.txt-forbidden filters (pmin, prefn*, prefv*) removed.
Every tile promotion is channel=online (Cisanello inherits only the regular price)."""

import re
from datetime import date
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from selectolax.lexbor import LexborHTMLParser, LexborNode

from pisaprices.config.schemas import StoreConfig
from pisaprices.domain.enums import Chain, Channel, Mechanic, Program, Source
from pisaprices.domain.money import unit_price
from pisaprices.ingest.base import ContextError
from pisaprices.ingest.categories import CategoryIndex
from pisaprices.ingest.http import PoliteClient
from pisaprices.ingest.prices import parse_eur, parse_site_unit_price, unit_price_disagrees
from pisaprices.ingest.promotions import read_terms
from pisaprices.ingest.records import ParsedBatch, PriceRecord, ProductRecord, ProductRef, PromotionRecord
from pisaprices.ingest.snapshots import RawSnapshot
from pisaprices.ingest.units import parse_quantity

HOST = "prenotalaspesa.coopfirenze.it"
BASE = f"https://{HOST}"
DW = f"{BASE}/on/demandware.store/Sites-CoopFi-Site/it_IT"
FORBIDDEN_PARAMS = ("pmin", "pmax", "prefn", "prefv", "q")
MAX_PAGES = 200
CATEGORY_LINK = re.compile(r"""href="(/[a-z0-9%\-']+(?:/[a-z0-9%\-']+)*/)\"""")   # menu links end with "/"


def strip_forbidden(url: str) -> str:
    parts = urlsplit(url.replace("&amp;", "&"))
    query = [(k, v) for k, v in parse_qsl(parts.query) if not k.startswith(FORBIDDEN_PARAMS)]
    return urlunsplit(parts._replace(query=urlencode(query)))


def show_more_url(html: str) -> str | None:
    node = LexborHTMLParser(html).css_first(".js-showMore[data-url]")
    return strip_forbidden(node.attributes["data-url"]) if node else None


def menu_categories(html: str) -> dict[str, str]:
    """Category path -> name, from the home page menu (links ending with "/")."""
    found: dict[str, str] = {}
    for link in LexborHTMLParser(html).css("a[href]"):
        href = link.attributes.get("href") or ""
        if CATEGORY_LINK.fullmatch(f'href="{href}"'):
            key = unquote(href).strip("/")
            name = _text(link)
            if name and key not in found:
                found[key] = name
    return found


def _text(node: LexborNode | None) -> str:
    return re.sub(r"\s+", " ", node.text(separator=" ")).strip() if node else ""


class CoopCatalog:
    source = Source.COOP_CATALOG
    chain = Chain.COOP
    allowed_hosts = frozenset({HOST})
    forbidden_params = FORBIDDEN_PARAMS
    forbidden_paths = ("/on/demandware.static/Sites-CoopFi-Site/", "/search")

    def __init__(self, store: StoreConfig, categories: CategoryIndex, unit_price_tolerance: float) -> None:
        if not store.external_id:
            raise ValueError(f"store '{store.slug}' needs external_id (prenotalaspesa storeId)")
        self.store = store
        self.categories = categories
        self.tolerance = unit_price_tolerance

    def cookies(self):
        return None

    def verify_context(self, client: PoliteClient) -> None:
        store_id = self.store.external_id
        client.get(f"{BASE}/", "home")
        stores, _ = client.get(f"{BASE}/stores", "stores")
        button = LexborHTMLParser(stores.text).css_first(f'button[data-storeid="{store_id}"][data-action*="Stores-SelectStore"]')
        if button is None:
            raise ContextError(f"pickup point {store_id} not found on {BASE}/stores (closed or renamed?)")
        attrs = button.attributes
        client.get(f"{DW}/Stores-SelectStore", "select store", headers={"X-Requested-With": "XMLHttpRequest"}, params={
            "actionUrl": f"{BASE}/stores",
            "storeId": store_id,
            "storePolygon": attrs.get("data-storepolygon", ""),
            "isNewPickingSystemEnabled": attrs.get("data-isnewpickingsystem", "true"),
            "isFamilyAndFriendsEnabled": attrs.get("data-familyandfriends", "false"),
        })
        current, _ = client.get(f"{DW}/Stores-CurrentStore", "current store", headers={"X-Requested-With": "XMLHttpRequest"})
        if f'data-preferredstoreid="{store_id}"' not in current.text:
            raise ContextError(f"prenotalaspesa did not select pickup point {store_id}: refusing generic prices")

    def site_categories(self, client: PoliteClient) -> dict[str, str]:
        home, _ = client.get(f"{BASE}/", "home")
        return menu_categories(home.text)

    def fetch(self, client: PoliteClient, source_keys: list[str]) -> None:
        for key in source_keys:
            response, _ = client.get(f"{BASE}/{key.strip('/')}/", f"cat {key} p1", context={"key": key, "page": 1})
            next_url, page = show_more_url(response.text), 1
            while next_url and page < MAX_PAGES:
                page += 1
                response, _ = client.get(next_url, f"cat {key} p{page}", context={"key": key, "page": page})
                next_url = show_more_url(response.text)

    def parse(self, snapshot: RawSnapshot) -> ParsedBatch:
        observed = date.fromisoformat(snapshot.fetched_at[:10])
        key = snapshot.context.get("key", "")
        products, prices, promotions = [], [], []
        seen = set()
        for tile in LexborHTMLParser(snapshot.text()).css("div.product[data-pid]"):
            pid = tile.attributes["data-pid"]
            if pid in seen:
                continue
            seen.add(pid)
            parsed = self._tile(tile, pid, key, snapshot.fetched_at, observed)
            if parsed:
                product, price, promos = parsed
                products.append(product)
                prices.append(price)
                promotions += promos
        return ParsedBatch(products=products, prices=prices, promotions=promotions)

    def _tile(self, tile: LexborNode, pid: str, key: str, observed_at: str, observed: date):
        name = _text(tile.css_first(".cc-productTile__linkText"))
        if not name:
            raise ValueError(f"tile {pid} has no product name (layout changed?)")
        if not self.categories.is_ours(self.source, key, name):
            return None   # excluded by the category's rules ("Candeline per torta")
        link = tile.css_first("a.cc-productTile__link")
        image = tile.css_first("img.cc-tile-image")
        quantity = parse_quantity(name) or parse_quantity(_text(tile.css_first(".cc-unitQuantity")))

        member_block = tile.css_first(".cc-prices__wrp .cc-promotions")
        member_price = None
        if member_block is not None and tile.css_first(".cc-promotions__membercard"):
            member_price = parse_eur(_text(member_block.css_first(".cc-prices__item .cc-value")))
        public_block = tile.css_first(".cc-prices__nonMember") or tile.css_first(".cc-prices .price")
        public_price = parse_eur(_text(public_block.css_first(".cc-prices__item .cc-value"))) if public_block else None
        public_cut = public_block is not None and public_block.css_first(".cc-promotions__value") is not None
        previous = parse_eur(_text(tile.css_first(".cc-prices__original .cc-value")))
        if not public_price:
            return None  # price 0 or missing: excluded by the filter we may not use (pmin)

        regular = previous or public_price
        site_unit = parse_site_unit_price(_text(public_block.css_first(".cc-prices__unit")) if public_block else "")
        mismatch = unit_price_disagrees(public_price, quantity, site_unit, self.tolerance)

        product = ProductRecord(
            chain=self.chain, external_id=pid, name=name, brand=_text(tile.css_first(".cc-productTile__brand__label")) or None,
            net_qty=quantity.amount if quantity else None, unit=quantity.unit if quantity else None,
            variable_weight=bool(quantity and quantity.variable_weight), source_category_key=key or None,
            category_slug=self.categories.classify(self.source, key, name),
            product_url=f"{BASE}{link.attributes['href']}" if link else None,
            image_url=image.attributes.get("src") if image else None,
        )
        price = PriceRecord(
            chain=self.chain, external_id=pid, store_slug=self.store.slug, price_cents=regular,
            unit_price=unit_price(regular, quantity) if quantity else None, observed_at=observed_at,
            needs_review=mismatch,
        )

        label = _text(tile.css_first(".cc-promotions__label"))
        limit = _text(tile.css_first(".cc-maximumNumber"))
        terms = read_terms(f"{label} {limit}", observed)
        ref = ProductRef(chain=self.chain, external_id=pid, name=name,
                         net_qty=quantity.amount if quantity else None, unit=quantity.unit if quantity else None)
        common = dict(
            chain=self.chain, store_slug=self.store.slug, mechanic=Mechanic.PRICE_CUT, items=[ref], channel=Channel.ONLINE,
            max_qty_per_receipt=terms.max_qty_per_receipt, valid_until=terms.valid_until,
            prior_price_cents=previous, observed_at=observed_at, needs_review=terms.needs_review,
        )
        promotions = []
        if public_cut and public_price < regular:
            promotions.append(PromotionRecord(params={"promo_price_cents": public_price},
                                              raw_text=_text(public_block), **common))
        if member_price is not None and member_price < regular:
            promotions.append(PromotionRecord(params={"promo_price_cents": member_price}, program=Program.COOP_SOCIO,
                                              raw_text=_text(member_block), **common))
        return product, price, promotions
