"""Invariants on real store pages (local only: tests/fixtures/ is not in git).

Content changes every week, so these tests check properties, not values: every listing page
parses, products have names and (almost always) a quantity, prices are positive, and a Conad
page without the configured store is refused. Rebuild the pages with `pisaprices fixtures capture`."""

import json
from pathlib import Path

import pytest

from pisaprices.config.files import load_config
from pisaprices.domain.enums import Source
from pisaprices.services.sources import build_collector
from tests.helpers import TESTS, snapshot

PROJECT = TESTS.parent
FIXTURES = TESTS / "fixtures"
NOT_LISTINGS = ("product_", "store_", "stores", "current_store", "selectstore", "home", "puntovendita", "flyer")

pytestmark = pytest.mark.real_fixtures


def pages(source: str) -> list[Path]:
    found = [p for p in sorted((FIXTURES / source).rglob("*.html"))
             if not any(word in p.name.lower() for word in NOT_LISTINGS)]
    if not found:
        pytest.skip(f"no real pages for {source} (run `pisaprices fixtures capture`)")
    return found


def context(page: Path) -> dict:
    meta = page.with_name(page.stem + ".meta.json")
    return json.loads(meta.read_text(encoding="utf-8")).get("context", {}) if meta.exists() else {}


@pytest.fixture(scope="module")
def project_config():
    if not (PROJECT / "config" / "user.yaml").exists():
        pytest.skip("personal config/user.yaml not present")
    return load_config(PROJECT / "config")


@pytest.mark.parametrize("source", [s for s in Source])
def test_real_pages_parse_with_sane_values(source: Source, project_config) -> None:
    try:
        collector = build_collector(source, project_config, PROJECT)
    except FileNotFoundError as exc:   # Conad without a saved session
        pytest.skip(str(exc))
    products = with_quantity = 0
    for page in pages(source.value):
        snap = snapshot(page, context(page))
        if "nostore" in page.name:
            with pytest.raises(ValueError, match="generic prices"):
                collector.parse(snap)
            continue
        batch = collector.parse(snap)
        products += len(batch.products)
        with_quantity += sum(p.net_qty is not None for p in batch.products)
        assert all(p.name.strip() for p in batch.products)
        assert all(p.price_cents > 0 for p in batch.prices)
        assert all(p.store_slug for p in batch.prices)
        for promo in batch.promotions:
            assert promo.items and promo.raw_text
            if "promo_price_cents" in promo.params:
                assert promo.params["promo_price_cents"] > 0
                if promo.prior_price_cents:
                    assert promo.params["promo_price_cents"] < promo.prior_price_cents
    if products:
        assert with_quantity / products >= 0.9, f"{source}: only {with_quantity}/{products} products have a quantity"
