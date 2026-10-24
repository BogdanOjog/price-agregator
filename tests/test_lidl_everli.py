"""Lidl's Everli storefront: the catalog of one store, with Everli's prices."""

from pathlib import Path

import pytest
from sqlalchemy import func, select

from pisaprices.config.files import load_config
from pisaprices.domain.enums import Channel, RunStatus, Source, Unit
from pisaprices.ingest.sources.lidl_everli import tag_path
from pisaprices.services.collect import collect
from pisaprices.services.sources import build_collector
from pisaprices.storage.orm import PriceObservation, Product
from tests.helpers import TESTS, make_runtime, routes, snapshot

EVERLI = TESTS / "synthetic" / "lidl_everli"
SITE = {
    "https://lidl.everli.com/api/config": EVERLI / "config.json",
    "https://lidl.everli.com/api/products": EVERLI / "products.json",
    "https://lidl.everli.com/": EVERLI / "home.html",
}


@pytest.fixture
def config():
    return load_config(TESTS / "config")


def test_tag_path_goes_from_top_to_leaf() -> None:
    assert tag_path({"tags": ["458", "100162", "12"]}) == "12/100162/458"


def test_parse_products(config) -> None:
    batch = build_collector(Source.LIDL_EVERLI, config, TESTS).parse(snapshot(EVERLI / "products.json", {"key": "12"}))
    products = {p.external_id: p for p in batch.products}
    assert set(products) == {"900001", "900002", "900003"}   # apples: a tag no selected category maps to
    spaghetti, milk, penne = products["900001"], products["900002"], products["900003"]
    assert (spaghetti.category_slug, spaghetti.net_qty, spaghetti.unit) == ("pasta", 500, Unit.GRAM)
    assert spaghetti.name == "Spaghetti di prova confezione 0.500kg" and spaghetti.brand == "Marca Prova"
    assert (milk.category_slug, milk.net_qty, milk.unit) == ("milk", 1000, Unit.MILLILITRE)
    assert penne.variable_weight and penne.net_qty == 1000   # sold by weight: price per kg
    prices = {p.external_id: p.price_cents for p in batch.prices}
    assert prices == {"900001": 89, "900002": 115, "900003": 300}
    assert {p.store_slug for p in batch.prices} == {"lidl_everli_a"}
    [promo] = batch.promotions   # Everli's own discount: online only
    assert promo.params == {"promo_price_cents": 79} and promo.channel is Channel.ONLINE and promo.prior_price_cents == 89


def test_a_product_of_another_store_fails_the_parse(config, tmp_path: Path) -> None:
    other = tmp_path / "products.json"
    other.write_text((EVERLI / "products.json").read_text(encoding="utf-8").replace('"5390"', '"9999"', 1), encoding="utf-8")
    with pytest.raises(ValueError, match="store 9999"):
        build_collector(Source.LIDL_EVERLI, config, TESTS).parse(snapshot(other, {"key": "12"}))


def test_run_collects_each_top_tag_once(tmp_path: Path) -> None:
    log: list[str] = []
    rt = make_runtime(tmp_path, routes(SITE, log))
    result = collect(rt, build_collector(Source.LIDL_EVERLI, rt.config, tmp_path))
    assert result.status is RunStatus.OK, result.error
    products = [u for u in log if "/api/products" in u]
    assert len(products) == 2 and any("tags=12" in u for u in products) and any("tags=6" in u for u in products)
    assert any("/api/config" in u and "postal_code=56122" in u for u in log)
    with rt.sessions() as session:
        assert session.scalar(select(func.count()).select_from(Product).where(Product.chain == "lidl")) == 3
        assert session.scalar(select(func.count()).select_from(PriceObservation)) == 3


def test_another_store_for_the_address_stops_the_run(tmp_path: Path) -> None:
    rt = make_runtime(tmp_path, routes({**SITE, "https://lidl.everli.com/api/config": EVERLI / "config_other_store.json"}))
    result = collect(rt, build_collector(Source.LIDL_EVERLI, rt.config, tmp_path))
    assert result.status is RunStatus.FAILED and "store 9999" in result.error and "refusing" in result.error
    with rt.sessions() as session:
        assert session.scalar(select(func.count()).select_from(PriceObservation)) == 0


def test_shelf_estimate() -> None:
    from pisaprices.domain.money import shelf_estimate
    assert shelf_estimate(325, 8) == 301 and shelf_estimate(215, 8) == 199   # Patate Selenella, Mele Golden: shelf 2,99 / 1,99
    assert shelf_estimate(89, None) == 89


def test_catalog_and_plan_use_the_estimated_shelf_price(tmp_path: Path) -> None:
    from datetime import date

    from pisaprices.services import catalog, quick

    config_dir = tmp_path / "config"
    import shutil
    shutil.copytree(TESTS / "config", config_dir)
    stores = config_dir / "stores.yaml"
    text = stores.read_text(encoding="utf-8")
    text = text.replace('    name: Lidl A\n    cap: "56122"\n    source_mode: promo_only\n',
                        '    name: Lidl A\n    cap: "56122"\n    source_mode: catalog\n    price_source: lidl_everli_a\n')
    text = text.replace('    name: Lidl A (Everli)\n', '    name: Lidl A (Everli)\n    price_markup_pct: 8\n')
    stores.write_text(text, encoding="utf-8")
    rt = make_runtime(tmp_path, routes(SITE))
    assert collect(rt, build_collector(Source.LIDL_EVERLI, rt.config, tmp_path)).status is RunStatus.OK

    [spaghetti] = [v for v in catalog.browse(rt, "pasta", day=date(2026, 10, 24)) if v.name.startswith("Spaghetti")]
    [price] = spaghetti.prices
    assert price.price_cents == 82 and price.label == "estimat la raft: Lidl A (Everli) −8%"   # Everli 0,89

    drafts = quick.interpret(rt, "spaghetti 500 g")
    result = quick.plan_drafts(rt, drafts, start=date(2026, 10, 26), window_days=7)
    [by_store] = result.matrix.values()
    assert by_store["lidl_a"].cost_cents == 82 and by_store["lidl_a"].price_source.startswith("estimat la raft")
