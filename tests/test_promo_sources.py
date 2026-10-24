"""Lidl and Eurospin: promotion-only sources."""

from pathlib import Path

import pytest
from sqlalchemy import func, select

from pisaprices.config.files import load_config
from pisaprices.domain.enums import Mechanic, Program, RunStatus, Source
from pisaprices.ingest.sources.eurospin_offers import next_page_url
from pisaprices.ingest.sources.lidl_offers import weekly_pages
from pisaprices.services.collect import collect
from pisaprices.services.review import open_reviews
from pisaprices.services.sources import build_collector
from pisaprices.storage.orm import PriceObservation, Product, Promotion
from tests.helpers import TESTS, make_runtime, routes, snapshot

LIDL = TESTS / "synthetic" / "lidl_offers"
EURO = TESTS / "synthetic" / "eurospin_offers"


@pytest.fixture
def config():
    return load_config(TESTS / "config")


def test_weekly_pages_are_discovered_from_home() -> None:
    assert weekly_pages((LIDL / "home.html").read_text(encoding="utf-8")) == [
        "/c/super-offerte-kw-43-26/a1001", "/c/lidl-plus-kw-43-26/a1002"]


def test_lidl_parse(config) -> None:
    batch = build_collector(Source.LIDL_OFFERS, config, TESTS).parse(snapshot(LIDL / "week_page.html"))
    products = {p.external_id: p for p in batch.products}
    # 9003 not sold in region 600; 9005 ("ripien") and 9006 ("cioccolato") are food of our coarse
    # categories that the rules do not place: kept, their category decided later from similar products
    assert set(products) == {"9001", "9002", "9004", "9005", "9006"}
    assert products["9001"].category_slug == "pasta" and products["9002"].category_slug == "milk"
    assert products["9005"].category_slug is None and products["9006"].category_slug is None
    assert products["9002"].net_qty == 1000 and batch.prices == []   # no invented regular price

    promos = {p.items[0].external_id: p for p in batch.promotions}
    assert promos["9001"].params == {"promo_price_cents": 79} and promos["9001"].prior_price_cents == 99
    assert promos["9001"].program is None and str(promos["9001"].valid_until) == "2026-10-28"
    assert str(promos["9001"].valid_from) == "2026-10-22"   # storeStartDate in Rome time
    assert promos["9002"].program is Program.LIDL_PLUS and str(promos["9002"].valid_from) == "2026-10-23"
    assert promos["9004"].params == {"promo_price_cents": 70} and promos["9004"].needs_review   # region 600 price, regions differ
    assert batch.reviews == []


def test_eurospin_parse_uses_promotion_type(config) -> None:
    batch = build_collector(Source.EUROSPIN_OFFERS, config, TESTS).parse(snapshot(EURO / "listing_page1.html", {"key": "/0006"}))
    promos = {p.items[0].external_id: p for p in batch.promotions}
    assert set(promos) == {"800001", "800002", "800003", "800004", "800005"}
    assert {p.external_id: p.category_slug for p in batch.products}["800004"] is None   # almonds: not pasta, open
    assert promos["800001"].program is None and promos["800001"].params == {"promo_price_cents": 69}
    assert promos["800001"].prior_price_cents == 89 and str(promos["800001"].valid_until) == "2026-11-01"
    assert promos["800002"].program is Program.EUROSPIN_FAMILY
    assert promos["800003"].is_sottocosto and promos["800003"].prior_price_cents is None
    assert promos["800005"].mechanic is Mechanic.UNKNOWN and promos["800005"].needs_review


def test_eurospin_next_page_comes_from_the_page() -> None:
    page1 = (EURO / "listing_page1.html").read_text(encoding="utf-8")
    assert next_page_url(page1, 1) == "https://www.eurospin.com/it/it/home/prodotti.html?page=2&size=12&by_category=%2F0006"
    assert next_page_url((EURO / "listing_page2.html").read_text(encoding="utf-8"), 2) is None


def test_lidl_and_eurospin_runs(tmp_path: Path) -> None:
    log: list[str] = []
    rt = make_runtime(tmp_path, routes({
        "/c/super-offerte-kw-43-26/a1001": LIDL / "week_page.html",
        "/c/lidl-plus-kw-43-26/a1002": LIDL / "week_page.html",
        "https://www.lidl.it/": LIDL / "home.html",
        "https://www.eurospin.com/it/it/home/prodotti.html?page=2": EURO / "listing_page2.html",
        "https://www.eurospin.com/it-it/prodotti/": EURO / "listing_page1.html",
    }, log))
    lidl = collect(rt, build_collector(Source.LIDL_OFFERS, rt.config, tmp_path))
    euro = collect(rt, build_collector(Source.EUROSPIN_OFFERS, rt.config, tmp_path))
    assert lidl.status is RunStatus.OK and euro.status is RunStatus.OK, (lidl.error, euro.error)
    assert any("by_category=%2F0006" in u and "by_typeOffer=family" in u for u in log)
    assert not any("/leaflet/" in u or "/q/search" in u for u in log)
    with rt.sessions() as session:
        assert session.scalar(select(func.count()).select_from(PriceObservation)) == 0
        assert session.scalar(select(func.count()).select_from(Product).where(Product.chain == "eurospin")) == 6
        assert session.scalar(select(func.count()).select_from(Promotion).where(Promotion.chain == "lidl")) == 5
    assert {r["kind"] for r in open_reviews(rt)} == {"unknown_promo"}   # the Eurospin card without a type
