from pathlib import Path

import pytest

from pisaprices.config.files import load_config
from pisaprices.domain.enums import Source
from pisaprices.services import category_setup
from pisaprices.services.collect import collect, source_keys
from pisaprices.services.conad_session import import_session
from pisaprices.services.sources import build_collector
from tests.helpers import TESTS, make_runtime, routes
from tests.test_cli import SITE


@pytest.fixture
def runtime(tmp_path: Path):
    import_session(tmp_path, (TESTS / "synthetic" / "conad_catalog" / "copied_curl.txt").read_text(encoding="utf-8"))
    rt = make_runtime(tmp_path, routes(SITE))
    for source in (Source.LIDL_OFFERS, Source.EUROSPIN_OFFERS):
        assert collect(rt, build_collector(source, rt.config, tmp_path)).error is None
    return rt


def test_refresh_stores_named_site_categories(runtime) -> None:
    result = category_setup.refresh_site_categories(runtime)
    assert result == {"coop_catalog": 2, "conad_catalog": 2, "eurospin_offers": 2}
    assert {c["key"]: c["name"] for c in category_setup.site_categories(runtime, "conad_catalog")} == {
        "1004": "Pasta di semola", "0404": "Latte"}
    assert {"key": "/0005", "name": "Latticini e uova"} in [
        {k: c[k] for k in ("key", "name")} for c in category_setup.site_categories(runtime, "eurospin_offers")]


def test_lidl_coarse_categories_come_from_the_last_run(runtime) -> None:
    coarse = dict(category_setup.lidl_coarse_categories(runtime))
    assert coarse["Cibo e quasi cibo/Dairy"] == 2 and "Cibo e quasi cibo/Pantry/Pasta" in coarse


def test_preview_applies_the_rules_to_saved_pages(runtime) -> None:
    lines, missing = category_setup.preview(runtime, "lidl_offers", ["Cibo e quasi cibo/Dairy"], ["latte"], ["cioccolato"])
    assert missing == []
    assert {(l.title, l.included) for l in lines} == {("Latte di prova intero", True), ("Cioccolato al latte", False)}


def test_preview_of_a_category_never_downloaded_costs_one_request_on_demand(runtime) -> None:
    lines, missing = category_setup.preview(runtime, "eurospin_offers", ["/0001"], [], [])
    assert lines == [] and missing == ["/0001"]
    lines, missing = category_setup.preview(runtime, "eurospin_offers", ["/0001"], ["spaghetti"], [], allow_fetch=True)
    assert missing == [] and any(l.included for l in lines)


def test_save_makes_the_category_collected(runtime) -> None:
    category_setup.save(runtime, "eggs", coop=["dairy/eggs"], lidl=["Cibo e quasi cibo/Dairy"], eurospin=["/0005"],
                        include=["uova", " "], exclude=[])
    config = load_config(runtime.root / "config")
    eggs = next(c for c in config.categories.categories if c.slug == "eggs")
    assert eggs.selected and not eggs.review and eggs.title_rules.include == ["uova"]
    assert "dairy/eggs" in source_keys(config, Source.COOP_CATALOG)
    text = (runtime.root / "config" / "categories.yaml").read_text(encoding="utf-8")
    assert text.startswith("# Test taxonomy.")   # comments kept


def test_save_refuses_unknown_category(runtime) -> None:
    with pytest.raises(ValueError, match="unknown category"):
        category_setup.save(runtime, "nope", coop=[], lidl=[], eurospin=[], include=[], exclude=[])
