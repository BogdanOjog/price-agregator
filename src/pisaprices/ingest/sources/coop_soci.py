"""coopfirenze.it "Offerte per i soci": a short, static list valid in every Unicoop Firenze
supermarket (so at Cisanello, in store). No product ids: items are linked by `resolve`."""

import re
from datetime import date

from selectolax.lexbor import LexborHTMLParser

from pisaprices.config.schemas import StoreConfig
from pisaprices.domain.enums import Chain, Channel, Mechanic, Program, Source
from pisaprices.ingest.prices import parse_eur
from pisaprices.ingest.promotions import read_terms
from pisaprices.ingest.records import ParsedBatch, ProductRef, PromotionRecord
from pisaprices.ingest.snapshots import RawSnapshot
from pisaprices.ingest.units import parse_quantity

HOST = "www.coopfirenze.it"
URL = f"https://{HOST}/negozi-e-promo/offerte-e-volantini/offerte-per-i-soci"


def _text(node) -> str:
    return re.sub(r"\s+", " ", node.text(separator=" ")).strip() if node else ""


class CoopSoci:
    source = Source.COOP_SOCI
    chain = Chain.COOP
    allowed_hosts = frozenset({HOST})
    forbidden_params = ()
    forbidden_paths = ()

    def __init__(self, store: StoreConfig) -> None:
        self.store = store   # the visited Coop store: these offers are valid in every supermarket

    def cookies(self):
        return None

    def verify_context(self, client) -> None:
        """No session: the page is the same for every visitor."""

    def site_categories(self, client) -> dict[str, str]:
        return {}

    def fetch(self, client, source_keys: list[str]) -> None:
        client.get(URL, "offerte per i soci")

    def parse(self, snapshot: RawSnapshot) -> ParsedBatch:
        observed = date.fromisoformat(snapshot.fetched_at[:10])
        tree = LexborHTMLParser(snapshot.text())
        period = next((_text(t) for t in tree.css(".one-column-text .title") if "VALID" in _text(t).upper()), "")
        validity = read_terms(period, observed)
        if period and validity.valid_until is None:
            raise ValueError(f"cannot read the validity period '{period}'")

        promotions = []
        # .section-cards is both the grid wrapper and the card; the title container exists once per offer
        for title in tree.css(".section-title-container"):
            section = title.parent
            name = _text(title.css_first("h2.heading"))
            price_text = _text(section.css_first(".card-title"))
            if not name or not price_text:
                continue
            subtitle = _text(section.css_first(".section-cards-subtitle"))
            details = _text(section.css_first(".large-card-content"))
            terms = read_terms(details, observed)
            quantity = parse_quantity(f"{name} {subtitle}")
            prior = re.search(r"invece di\s*(€\s*[\d.,]+)", details, re.I)
            promotions.append(PromotionRecord(
                chain=self.chain, store_slug=self.store.slug, mechanic=Mechanic.PRICE_CUT,
                params={"promo_price_cents": parse_eur(price_text)},
                items=[ProductRef(chain=self.chain, name=name, net_qty=quantity.amount if quantity else None,
                                  unit=quantity.unit if quantity else None)],
                program=Program.COOP_SOCIO, channel=Channel.STORE, excludes_already_promoted=True,
                max_qty_per_card=terms.max_qty_per_card, valid_from=validity.valid_from, valid_until=validity.valid_until,
                prior_price_cents=parse_eur(prior.group(1)) if prior else None,
                raw_text=f"{period} | {name} | {subtitle} | {details}", observed_at=snapshot.fetched_at,
                needs_review=validity.needs_review,
            ))
        return ParsedBatch(promotions=promotions)
