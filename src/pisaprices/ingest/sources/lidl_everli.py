"""lidl.everli.com (Everli's Lidl storefront; robots.txt "Allow: /"): the whole catalog of one Lidl
store, with Everli's prices (they may include Everli's markup, so they are labelled as such).

Session: the home page, then /api/config with the store's own public address, which puts the
address in the session; the store Everli assigns must be the configured one (no generic prices).
Products: /api/products?tags=<top tag>&take=1000 for each top tag that a selected category maps
into, following `links.next`. Each product carries its tag path (leaf, mid, top), classified as
"top/mid/leaf". Everli's own discounts are channel=online: they do not apply in the store."""

import json

from pisaprices.config.schemas import StoreConfig
from pisaprices.domain.enums import Chain, Channel, Mechanic, Source, Unit
from pisaprices.domain.money import unit_price
from pisaprices.domain.quantity import Quantity
from pisaprices.ingest.base import ContextError
from pisaprices.ingest.categories import CategoryIndex
from pisaprices.ingest.http import PoliteClient
from pisaprices.ingest.records import ParsedBatch, PriceRecord, ProductRecord, ProductRef, PromotionRecord
from pisaprices.ingest.snapshots import RawSnapshot
from pisaprices.ingest.units import parse_quantity, quantity_from_fields

HOST = "lidl.everli.com"
BASE = f"https://{HOST}"
BRAND = 50          # Everli's id of the Lidl brand
PAGE_SIZE = 1000
MAX_PAGES = 20
JSON = {"Accept": "application/json"}
TYPE_UNITS = {"kilogram": "kg", "liter": "l", "litre": "l", "piece": "pz", "unit": "pz"}


def tag_path(product: dict) -> str:
    """Everli lists a product's tags leaf first: ["570", "100276", "3"] -> "3/100276/570"."""
    return "/".join(reversed([str(t) for t in product.get("tags") or []]))


def _quantity(product: dict, text: str) -> Quantity | None:
    unit = TYPE_UNITS.get(product.get("type") or "")
    if unit and product.get("value"):
        return quantity_from_fields(float(product["value"]), unit)
    return parse_quantity(text)


class LidlEverli:
    source = Source.LIDL_EVERLI
    chain = Chain.LIDL
    allowed_hosts = frozenset({HOST})
    forbidden_params: tuple[str, ...] = ()
    forbidden_paths: tuple[str, ...] = ()

    def __init__(self, store: StoreConfig, categories: CategoryIndex) -> None:
        if not store.external_id or store.everli_address is None:
            raise ValueError(f"store '{store.slug}' needs external_id (Everli store id) and everli_address")
        self.store = store
        self.categories = categories

    def cookies(self):
        return None

    def verify_context(self, client: PoliteClient) -> None:
        client.get(f"{BASE}/", "home")
        address = self.store.everli_address
        response, _ = client.get(f"{BASE}/api/config", "config", headers=JSON, params={
            "brand": BRAND, "postal_code": address.postal_code, "address": address.street,
            "street_number": address.street_number, "city": address.city, "country": "Italia",
            "lat": address.lat, "lng": address.lng,
        })
        store = ((response.json() if response.status_code == 200 else {}).get("config") or {}).get("store") or {}
        if str(store.get("id")) != self.store.external_id:
            raise ContextError(f"Everli serves store {store.get('id') or 'none'} ({store.get('address')}) for this address, "
                               f"not {self.store.external_id}: refusing another store's prices")

    def site_categories(self, client: PoliteClient) -> dict[str, str]:
        """Tag paths and names ("3/100113" -> "Frutta e Verdura / Verdura Fresca")."""
        self.verify_context(client)
        response, _ = client.get(f"{BASE}/api/tags", "tags", headers=JSON)
        found: dict[str, str] = {}

        def walk(node: dict, path: str, name: str) -> None:
            found[path] = name
            for child in node.get("children") or []:
                walk(child, f"{path}/{child['id']}", f"{name} / {child['name']}")

        for top in response.json().get("tags", []):
            walk(top, str(top["id"]), top["name"])
        return found

    def fetch(self, client: PoliteClient, source_keys: list[str]) -> None:
        tops = list(dict.fromkeys(key.split("/")[0] for key in source_keys))
        for top in tops:
            skip, page = 0, 1
            while page <= MAX_PAGES:
                response, _ = client.get(f"{BASE}/api/products", f"tag {top} p{page}", headers=JSON,
                                         params={"tags": top, "take": PAGE_SIZE, "skip": skip},
                                         context={"key": top, "page": page})
                if not (response.json().get("links") or {}).get("next"):
                    break
                skip, page = skip + PAGE_SIZE, page + 1

    def parse(self, snapshot: RawSnapshot) -> ParsedBatch:
        data = json.loads(snapshot.text())
        if "data" not in data:
            raise ValueError("no 'data' list in the products response (API changed?)")
        products, prices, promotions = [], [], []
        for item in data["data"]:
            if str(item.get("store_id")) != self.store.external_id:
                raise ValueError(f"product {item.get('id')} belongs to store {item.get('store_id')}, not {self.store.external_id}")
            parsed = self._item(item, snapshot.fetched_at)
            if parsed:
                product, price, promo = parsed
                products.append(product)
                prices.append(price)
                promotions += promo
        return ParsedBatch(products=products, prices=prices, promotions=promotions)

    def _item(self, item: dict, observed_at: str):
        pid, key = str(item["id"]), tag_path(item)
        description = (item.get("short_description") or "").strip()
        name = f"{item['name']} {description}".strip()
        if not self.categories.is_ours(self.source, key, name):
            return None   # a tag no selected category maps to, or excluded by the rules
        regular = int(item.get("price") or 0)
        if regular <= 0:
            return None
        if item.get("variable_weight"):   # sold by weight: compare on €/kg only
            quantity = Quantity(1000, Unit.GRAM, variable_weight=True)
            regular = int(item.get("price_per_type") or regular)
        else:
            quantity = _quantity(item, name)
        product = ProductRecord(
            chain=self.chain, external_id=pid, name=name, brand=item.get("brand") or None,
            net_qty=quantity.amount if quantity else None, unit=quantity.unit if quantity else None,
            variable_weight=bool(quantity and quantity.variable_weight), source_category_key=key,
            category_slug=self.categories.classify(self.source, key, name),
            image_url=item.get("thumbnail") or item.get("image") or None,
        )
        price = PriceRecord(chain=self.chain, external_id=pid, store_slug=self.store.slug, price_cents=regular,
                            unit_price=unit_price(regular, quantity) if quantity else None, observed_at=observed_at)
        promotions = []
        discounted = int(item.get("discounted_price") or regular)
        if not item.get("variable_weight") and 0 < discounted < regular:
            promotions.append(PromotionRecord(
                chain=self.chain, store_slug=self.store.slug, mechanic=Mechanic.PRICE_CUT,
                params={"promo_price_cents": discounted}, channel=Channel.ONLINE, prior_price_cents=regular,
                items=[ProductRef(chain=self.chain, external_id=pid, name=name,
                                  net_qty=quantity.amount if quantity else None, unit=quantity.unit if quantity else None)],
                raw_text=f"Everli: {discounted} invece di {regular} (discount {item.get('discount')})",
                observed_at=observed_at,
            ))
        return product, price, promotions
