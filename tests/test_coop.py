from pathlib import Path

import pytest
from sqlalchemy import func, select

from pisaprices.config.files import load_config
from pisaprices.domain.enums import Channel, Program, RunStatus, Source
from pisaprices.ingest.sources.coop_catalog import CoopCatalog, strip_forbidden
from pisaprices.services.collect import collect
from pisaprices.services.sources import build_collector
from pisaprices.storage.orm import PriceObservation, Product, Promotion
from tests.helpers import TESTS, make_runtime, routes, snapshot

SYN = TESTS / "synthetic" / "coop_catalog"
BASE = "https://prenotalaspesa.coopfirenze.it"
DW = f"{BASE}/on/demandware.store/Sites-CoopFi-Site/it_IT"


@pytest.fixture
def config():
    return load_config(TESTS / "config")


@pytest.fixture
def catalog(config) -> CoopCatalog:
    return build_collector(Source.COOP_CATALOG, config, TESTS)


def test_strip_forbidden_keeps_paging_only() -> None:
    url = f"{DW}/Search-UpdateGrid?cgid=pasta&amp;pmin=0%2c01&amp;prefn1=excludeForPickup&amp;prefv1=false&amp;start=12&amp;sz=12"
    assert strip_forbidden(url) == f"{DW}/Search-UpdateGrid?cgid=pasta&start=12&sz=12"


def test_parse_category_tiles(catalog: CoopCatalog) -> None:
    batch = catalog.parse(snapshot(SYN / "category_page1.html", {"key": "pasta/semola"}))
    products = {p.external_id: p for p in batch.products}
    prices = {p.external_id: p for p in batch.prices}

    assert set(products) == {"9900000000001", "9900000000002", "9900000000003"}  # zero price skipped
    assert products["9900000000003"].net_qty == 800 and products["9900000000003"].category_slug == "pasta"
    assert products["9900000000001"].brand == "MARCA UNO"
    # regular price = "Prezzo precedente" when the tile shows one
    assert prices["9900000000002"].price_cents == 200 and prices["9900000000001"].price_cents == 120
    assert all(p.store_slug == "coop_source" for p in batch.prices)

    cut = next(p for p in batch.promotions if p.items[0].external_id == "9900000000002")
    assert cut.params == {"promo_price_cents": 150} and cut.program is None
    assert str(cut.valid_until) == "2026-11-04" and cut.channel is Channel.ONLINE and cut.prior_price_cents == 200

    socio = [p for p in batch.promotions if p.items[0].external_id == "9900000000003"]
    assert {(p.program, p.params["promo_price_cents"]) for p in socio} == {(None, 210), (Program.COOP_SOCIO, 150)}
    assert all(p.max_qty_per_receipt == 4 for p in socio)


def test_collect_selects_store_follows_pages_and_respects_robots(tmp_path: Path) -> None:
    log: list[str] = []
    transport = routes({
        "/": "<html>home</html>",
        "/stores": SYN / "stores.html",
        f"{DW}/Stores-SelectStore": '{"action": "Stores-SelectStore"}',
        f"{DW}/Stores-CurrentStore": SYN / "current_store.html",
        "/pasta/semola/": SYN / "category_page1.html",
        f"{DW}/Search-UpdateGrid": SYN / "grid_page2.html",
    }, log)
    rt = make_runtime(tmp_path, transport)
    result = collect(rt, build_collector(Source.COOP_CATALOG, rt.config, tmp_path))
    assert result.status is RunStatus.OK, result.error

    select_store = next(u for u in log if "SelectStore" in u)
    assert "storeId=iestore34446" in select_store and "storePolygon=1253" in select_store
    assert any("Search-UpdateGrid?cgid=pasta-semola&start=12&sz=12" in u for u in log)
    assert not any(bad in u for u in log for bad in ("pmin", "prefn", "prefv", "demandware.static"))
    with rt.sessions() as session:
        assert session.scalar(select(func.count()).select_from(Product)) == 4
        assert session.scalar(select(func.count()).select_from(PriceObservation)) == 4
        assert session.scalar(select(func.count()).select_from(Promotion)) == 3


def test_wrong_store_stops_the_run(tmp_path: Path) -> None:
    rt = make_runtime(tmp_path, routes({
        "/": "home", "/stores": SYN / "stores.html", f"{DW}/Stores-SelectStore": "{}",
        f"{DW}/Stores-CurrentStore": '<div data-preferredstoreid="iestore99999"></div>',
    }))
    result = collect(rt, build_collector(Source.COOP_CATALOG, rt.config, tmp_path))
    assert result.status is RunStatus.FAILED and "refusing generic prices" in result.error
    with rt.sessions() as session:
        assert session.scalar(select(func.count()).select_from(PriceObservation)) == 0


def test_missing_pickup_point_stops_the_run(tmp_path: Path) -> None:
    rt = make_runtime(tmp_path, routes({"/": "home", "/stores": "<html>no stores</html>"}))
    result = collect(rt, build_collector(Source.COOP_CATALOG, rt.config, tmp_path))
    assert result.status is RunStatus.FAILED and "not found" in result.error


def test_parse_socio_offers(config) -> None:
    soci = build_collector(Source.COOP_SOCI, config, TESTS)
    [promo] = soci.parse(snapshot(TESTS / "synthetic" / "coop_soci" / "offerte_per_i_soci.html")).promotions
    assert promo.store_slug == "coop_a" and promo.program is Program.COOP_SOCIO and promo.channel is Channel.STORE
    assert promo.params == {"promo_price_cents": 249} and promo.prior_price_cents == 499 and promo.max_qty_per_card == 2
    assert (str(promo.valid_from), str(promo.valid_until)) == ("2026-10-16", "2026-10-31")
    assert promo.items[0].external_id is None and promo.items[0].net_qty == 250  # linked later by resolve
