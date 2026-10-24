"""Own categories: site keys and title rules (ingest), and open products placed from similar ones (services)."""

from pathlib import Path

import pytest
from sqlalchemy import select

from pisaprices.config.files import load_config
from pisaprices.config.schemas import CategoriesFile
from pisaprices.domain.enums import ReviewKind, Source
from pisaprices.ingest.categories import CategoryIndex, title_matches
from pisaprices.ingest.titles import head, head_end, without_brand_prefix
from pisaprices.services.classify import place_open_products
from pisaprices.services.conad_session import import_session
from pisaprices.services.review import accept, open_reviews
from pisaprices.services.runs import collect_sources
from pisaprices.storage.orm import Category, Product, ReviewItem
from tests.helpers import TESTS, make_runtime, routes
from tests.test_cli import SITE

L = "Cibo e quasi cibo/"
TREE = {"categories": [
    {"slug": "condimenti", "name": "Condimenti", "sources": {"lidl_offers": [L + "Oli"], "eurospin_offers": ["/0006"]}},
    {"slug": "olio_evo", "name": "Olio EVO", "parent": "condimenti", "selected": True, "review": False,
     "sources": {"conad_catalog": ["0801"], "coop_catalog": ["olio/olive"]},
     "title_rules": {"include": ["extravergine", "extra vergine"], "exclude": ["spray"]}},
    {"slug": "aceto", "name": "Aceto", "parent": "condimenti", "selected": True, "review": False,
     "sources": {"conad_catalog": ["0801"]}, "title_rules": {"include": ["aceto"]}},
    {"slug": "salse", "name": "Salse", "parent": "condimenti", "selected": True, "review": False,
     "sources": {"conad_catalog": ["0801", "0805"]}},
    {"slug": "pesce", "name": "Pesce", "sources": {"eurospin_offers": ["/0006"]}},
    {"slug": "tonno", "name": "Tonno", "parent": "pesce", "selected": True, "review": False,
     "title_rules": {"include": ["tonno"]}},
]}


@pytest.fixture
def index() -> CategoryIndex:
    config = load_config(TESTS / "config")
    return CategoryIndex(config.model_copy(update={"categories": CategoriesFile.model_validate(TREE)}))


def test_title_head_and_brand_prefix() -> None:
    assert without_brand_prefix("DELUXE Torta Sbrisolona") == "Torta Sbrisolona"
    assert without_brand_prefix("SELECT & GO Sushi box") == "Sushi box"
    assert head("Tonno all'olio extravergine") == ["tonno"]
    assert head_end("olio extra vergine di oliva") == len("olio extra vergine ")
    assert title_matches(["extravergine"], [], "Olio extravergine di oliva 1 l")
    assert not title_matches(["extravergine"], [], "Tonno all'olio extravergine")   # the word is not what the product is
    assert title_matches(["olio di semi"], [], "Olio di semi di girasole")          # a rule may span a connector
    assert not title_matches(["aceto"], [], "Glassa all'aceto balsamico")


def test_a_group_key_makes_every_category_of_the_group_a_candidate(index) -> None:
    assert {c.slug for c in index.candidates(Source.LIDL_OFFERS, L + "Oli/Olio di oliva")} == {"olio_evo", "aceto", "salse"}
    assert index.candidates(Source.LIDL_OFFERS, L + "Moda") == []
    assert {c.slug for c in index.candidates(Source.EUROSPIN_OFFERS, "/0006")} == {"olio_evo", "aceto", "salse", "tonno"}


def test_rules_decide_among_candidates(index) -> None:
    assert index.classify(Source.CONAD_CATALOG, "0801", "Olio extra vergine di oliva 1 l") == "olio_evo"
    assert index.classify(Source.CONAD_CATALOG, "0801", "Aceto balsamico di Modena") == "aceto"
    assert index.classify(Source.CONAD_CATALOG, "0801", "Glassa all'aceto balsamico") == "salse"   # the one catch-all
    assert index.classify(Source.CONAD_CATALOG, "0805", "Maionese") == "salse"                     # a single candidate
    assert index.classify(Source.COOP_CATALOG, "olio/olive", "Olio di oliva") == "olio_evo"        # the site decided
    assert index.classify(Source.EUROSPIN_OFFERS, "/0006", "Tonno all'olio extravergine") == "tonno"
    assert index.classify(Source.EUROSPIN_OFFERS, "/0006", "Olio spray") == "salse"   # excluded from olio_evo: the catch-all
    assert index.classify(Source.LIDL_OFFERS, L + "Oli", "Sale fino") == "salse"


def test_without_a_single_catch_all_the_category_stays_open() -> None:
    tree = {"categories": [*TREE["categories"],
                           {"slug": "spezie", "name": "Spezie", "parent": "condimenti", "selected": True, "review": False}]}
    config = load_config(TESTS / "config")
    index = CategoryIndex(config.model_copy(update={"categories": CategoriesFile.model_validate(tree)}))
    assert index.classify(Source.LIDL_OFFERS, L + "Oli", "Sale fino") is None   # salse or spezie: decided later


@pytest.fixture
def runtime(tmp_path: Path):
    import_session(tmp_path, (TESTS / "synthetic" / "conad_catalog" / "copied_curl.txt").read_text(encoding="utf-8"))
    rt = make_runtime(tmp_path, routes(SITE))
    collect_sources(rt)
    return rt


def _add_open_product(rt, name: str, site_key: str | None = "Cibo e quasi cibo/Pantry") -> int:
    from pisaprices.storage.repos import site_categories
    with rt.sessions.begin() as session:
        product = Product(chain="lidl", external_id=name, name=name,
                          source_category_id=site_categories.key_id(session, "lidl_offers", site_key) if site_key else None)
        session.add(product)
        session.flush()
        return product.id


def _category_of(rt, product_id: int) -> str | None:
    with rt.sessions() as session:
        category_id = session.get(Product, product_id).category_id
        return session.scalar(select(Category.slug).where(Category.id == category_id)) if category_id else None


def test_an_open_product_takes_the_category_of_products_named_alike(runtime) -> None:
    spaghetti = _add_open_product(runtime, "MARCA X Spaghetti integrali bio")
    unknown = _add_open_product(runtime, "Candela profumata")
    report = place_open_products(runtime)
    assert report.placed >= 1
    assert _category_of(runtime, spaghetti) == "pasta"     # "spaghetti integrali" -> "spaghetti": all pasta
    assert _category_of(runtime, unknown) is None          # nothing named alike: not ours, no review
    assert not [r for r in open_reviews(runtime) if r["subject_id"] == unknown]


def test_a_product_no_current_run_listed_is_left_alone(runtime) -> None:
    stale = _add_open_product(runtime, "MARCA X Spaghetti vecchi", site_key=None)
    place_open_products(runtime)
    assert _category_of(runtime, stale) is None


def test_accepting_a_category_review_sets_the_category(runtime) -> None:
    product = _add_open_product(runtime, "Prodotto incerto")
    with runtime.sessions.begin() as session:
        session.add(ReviewItem(kind=ReviewKind.CATEGORY_RULE.value, subject_table="products", subject_id=product,
                               payload={"key": f"category:{product}", "name": "Prodotto incerto", "suggestion": "milk",
                                        "votes": {"milk": 2, "pasta": 2}}, created_at="2026-10-24T00:00:00Z"))
    [item] = [r for r in open_reviews(runtime) if r["kind"] == ReviewKind.CATEGORY_RULE.value]
    accept(runtime, item["id"])
    assert _category_of(runtime, product) == "milk"
    place_open_products(runtime)   # a decided review is not proposed again
    assert not [r for r in open_reviews(runtime) if r["kind"] == ReviewKind.CATEGORY_RULE.value]


def test_a_catalog_product_is_placed_only_among_its_site_category_candidates(runtime) -> None:
    from pisaprices.storage.repos import site_categories
    with runtime.sessions.begin() as session:
        product = Product(chain="conad", external_id="x1", name="Spaghetti speciali",
                          source_category_id=site_categories.key_id(session, "conad_catalog", "0404"))
        session.add(product)
        session.flush()
        product_id = product.id
    place_open_products(runtime)
    # named alike: pasta; but Conad listed it under "0404", whose only candidate is milk -> a person decides
    assert _category_of(runtime, product_id) is None
    [item] = [r for r in open_reviews(runtime) if r["subject_id"] == product_id]
    assert item["payload"]["suggestion"] is None and item["payload"]["candidates"] == ["milk"]   # no guess offered


def test_among_a_catalog_category_candidates_the_most_named_alike_wins(runtime) -> None:
    from pisaprices.storage.repos import site_categories
    with runtime.sessions.begin() as session:
        product = Product(chain="conad", external_id="x2", name="Penne integrali",
                          source_category_id=site_categories.key_id(session, "conad_catalog", "1004"))
        session.add(product)
        session.flush()
        product_id = product.id
    place_open_products(runtime)
    assert _category_of(runtime, product_id) == "pasta"   # one product named alike is enough here


def test_a_title_every_candidate_excludes_is_not_ours(index) -> None:
    assert index.is_ours(Source.COOP_CATALOG, "olio/olive", "Olio extravergine")
    assert not index.is_ours(Source.COOP_CATALOG, "olio/olive", "Olio spray")          # the only candidate excludes it
    assert index.is_ours(Source.CONAD_CATALOG, "0801", "Olio spray")                   # salse does not


def test_a_proposal_disappears_once_a_later_run_places_the_product(runtime) -> None:
    product = _add_open_product(runtime, "Prodotto senza simili")
    with runtime.sessions.begin() as session:
        session.add(ReviewItem(kind=ReviewKind.CATEGORY_RULE.value, subject_table="products", subject_id=product,
                               payload={"key": f"category:{product}", "name": "x", "suggestion": None},
                               created_at="2026-10-24T00:00:00Z"))
        session.get(Product, product).category_id = session.scalar(select(Category.id).where(Category.slug == "pasta"))
    place_open_products(runtime)
    assert not [r for r in open_reviews(runtime) if r["subject_id"] == product]
