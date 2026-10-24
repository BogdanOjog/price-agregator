"""Catalog, lists and planning on a database filled by the synthetic Coop, Lidl and Eurospin runs."""

from datetime import date
from pathlib import Path

import pytest

from pisaprices.domain.enums import Source
from pisaprices.services import catalog, lists, plan
from pisaprices.services.classify import classify_products
from pisaprices.services.collect import collect
from pisaprices.services.sources import build_collector
from tests.helpers import TESTS, make_runtime, routes

SYN = TESTS / "synthetic"
DW = "https://prenotalaspesa.coopfirenze.it/on/demandware.store/Sites-CoopFi-Site/it_IT"
LIST = """
name: Prova
items:
  - {label: Pasta, category: pasta, quantity: 1 kg, overbuy: 1}
  - {label: Penne, category: pasta, quantity: 800 g, contains: [penne]}
"""


@pytest.fixture
def runtime(tmp_path: Path):
    rt = make_runtime(tmp_path, routes({
        f"{DW}/Search-UpdateGrid": SYN / "coop_catalog" / "grid_page2.html",
        f"{DW}/Stores-SelectStore": "{}",
        f"{DW}/Stores-CurrentStore": SYN / "coop_catalog" / "current_store.html",
        "https://prenotalaspesa.coopfirenze.it/stores": SYN / "coop_catalog" / "stores.html",
        "https://prenotalaspesa.coopfirenze.it/pasta/semola/": SYN / "coop_catalog" / "category_page1.html",
        "https://prenotalaspesa.coopfirenze.it/dairy/milk/": "<html></html>",
        "https://prenotalaspesa.coopfirenze.it/": "home",
        "https://www.lidl.it/c/": SYN / "lidl_offers" / "week_page.html",
        "https://www.lidl.it/": SYN / "lidl_offers" / "home.html",
        "https://www.eurospin.com/it/it/home/prodotti.html?page=2": SYN / "eurospin_offers" / "listing_page2.html",
        "https://www.eurospin.com/it-it/prodotti/": SYN / "eurospin_offers" / "listing_page1.html",
    }))
    for source in (Source.COOP_CATALOG, Source.LIDL_OFFERS, Source.EUROSPIN_OFFERS):
        result = collect(rt, build_collector(source, rt.config, tmp_path))
        assert result.error is None, result.error
    classify_products(rt)
    return rt


def test_browse_sorts_by_unit_price_and_labels_the_price_source(runtime) -> None:
    views = catalog.browse(runtime, "pasta", day=date(2026, 10, 26))
    assert views, "no products"
    prices = [v.best_unit_price for v in views if v.best_unit_price is not None]
    assert prices == sorted(prices)
    coop = next(v for v in views if v.chain == "coop")
    assert coop.prices[0].label.startswith("preț online") and not coop.prices[0].stale


def test_browse_filters(runtime) -> None:
    day = date(2026, 10, 26)
    assert all(v.promotions for v in catalog.browse(runtime, "pasta", promo_only=True, day=day))
    assert {v.chain for v in catalog.browse(runtime, "pasta", chain="eurospin", day=day)} == {"eurospin"}
    with pytest.raises(ValueError, match="unknown category"):
        catalog.browse(runtime, "nope")


def test_expired_promotion_is_not_active(runtime) -> None:
    assert catalog.promotions(runtime, date(2026, 10, 26))
    assert not [p for p, _ in catalog.promotions(runtime, date(2026, 12, 1))]


def test_product_page_has_dates_history_and_equivalents(runtime) -> None:
    pasta = catalog.browse(runtime, "pasta", day=date(2026, 10, 26))
    page = catalog.product_page(runtime, pasta[0].id, date(2026, 10, 26))
    assert all(p.observed_at for p in page.product.prices)
    assert len(page.history) == len(page.product.prices)
    for equivalent in page.equivalents:
        assert equivalent.chain != page.product.chain


def test_list_import_export_round_trip(runtime) -> None:
    list_id = lists.import_yaml(runtime, LIST)
    exported = lists.export_yaml(runtime, list_id)
    again = lists.get_list(runtime, lists.import_yaml(runtime, exported))
    original = lists.get_list(runtime, list_id)
    strip = lambda items: [{k: v for k, v in i.items() if k != "id"} for i in items]
    assert strip(again["items"]) == strip(original["items"])


@pytest.mark.parametrize("bad, message", [
    ("items: [{label: X, category: nope, quantity: 1 kg}]", "unknown category"),
    ("items: [{label: X, category: pasta, quantity: lots}]", "not understood"),
    ("items: [{label: X, match: exact_gtin, quantity: 1 kg}]", "needs gtin"),
    ("name: empty", "no items"),
])
def test_list_errors_are_explained(runtime, bad: str, message: str) -> None:
    with pytest.raises(lists.ListError, match=message):
        lists.import_yaml(runtime, bad)


def test_plan_uses_promotions_and_reports_each_k(runtime) -> None:
    list_id = lists.import_yaml(runtime, LIST)
    result = plan.plan_list(runtime, list_id, start=date(2026, 10, 26), window_days=7, max_k=3)
    assert result.reports and result.uncovered == []
    best = result.best
    assert best is not None and not best.plan.missing
    for report in result.reports:
        for store, store_lines in report.lines.items():
            assert store in report.plan.days
            for line in store_lines:
                assert line.choice.observed_at or line.choice.base_price_cents is None   # every regular price is dated
    # Lidl and Eurospin products only appear through an active promotion
    for report in result.reports:
        for line in [l for ls in report.lines.values() for l in ls]:
            if line.choice.store in ("lidl_a", "eurospin_a"):
                assert line.choice.promo is not None


def test_plan_after_promotions_end_drops_promo_only_stores(runtime) -> None:
    list_id = lists.import_yaml(runtime, LIST)
    # dated offers end on 28/10 and 01/11; undated ones count only while seen in the last 14 days
    later = plan.plan_list(runtime, list_id, start=date(2026, 11, 10), window_days=3, max_k=3)
    stores = {s for r in later.reports for s in r.plan.stores}
    assert "lidl_a" not in stores and "eurospin_a" not in stores
