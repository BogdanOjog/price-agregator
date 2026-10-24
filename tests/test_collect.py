"""Collection runs end to end with a fake collector over a mock transport (no network)."""

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from pisaprices.config.files import load_config, set_category_selected
from pisaprices.domain.enums import Chain, Mechanic, RunStatus, Source
from pisaprices.ingest.base import ContextError
from pisaprices.ingest.records import ParsedBatch, PriceRecord, ProductRecord, ProductRef, PromotionRecord
from pisaprices.services.clock import FixedClock
from pisaprices.services.collect import Runtime, collect, reparse, source_keys
from pisaprices.storage.orm import Base, PriceObservation, Product, Promotion, PromotionItem, PromotionSighting, ScrapeRun
from pisaprices.storage.session import make_engine, sqlite_url
from pisaprices.storage.sync import sync_config

TEST_CONFIG = Path(__file__).resolve().parent / "config"


class FakeShop:
    """A tiny site: /store tells which store is selected, /cat/<key> lists products as JSON."""

    def __init__(self, products_per_category: int = 3, store: str = "store-ok") -> None:
        self.products = products_per_category
        self.store = store
        self.requests: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request.url.path)
        if request.url.path == "/store":
            return httpx.Response(200, text=self.store)
        key = request.url.path.removeprefix("/cat/")
        items = [{"id": f"{key}-{i}", "name": f"Latte {i} 1 l", "price": 1.09 + i} for i in range(self.products)]
        return httpx.Response(200, json={"key": key, "items": items})


class FakeCollector:
    source = Source.COOP_CATALOG
    chain = Chain.COOP
    allowed_hosts = frozenset({"shop.test"})
    forbidden_params = ("pmin",)
    forbidden_paths = ()

    def __init__(self, broken: bool = False) -> None:
        self.broken = broken

    def cookies(self):
        return None

    def verify_context(self, client) -> None:
        response, _ = client.get("https://shop.test/store", "store")
        if response.text != "store-ok":
            raise ContextError("selected store is not the expected one")

    def fetch(self, client, source_keys) -> None:
        for key in source_keys:
            client.get(f"https://shop.test/cat/{key}", f"cat {key}", context={"key": key})

    def parse(self, snapshot) -> ParsedBatch:
        data = snapshot.json()
        if self.broken:
            raise KeyError("layout changed")
        products, prices = [], []
        for item in data["items"]:
            products.append(ProductRecord(chain=Chain.COOP, external_id=item["id"], name=item["name"], category_slug="milk"))
            prices.append(PriceRecord(chain=Chain.COOP, external_id=item["id"], store_slug="coop_source",
                                      price_cents=round(item["price"] * 100), observed_at=snapshot.fetched_at))
        promo = PromotionRecord(
            chain=Chain.COOP, store_slug="coop_source", mechanic=Mechanic.PRICE_CUT, params={"promo_price_cents": 99},
            items=[ProductRef(chain=Chain.COOP, external_id=data["items"][0]["id"], name=data["items"][0]["name"])],
            raw_text="-10%", observed_at=snapshot.fetched_at,
        )
        return ParsedBatch(products=products, prices=prices, promotions=[promo])


@pytest.fixture
def runtime(tmp_path: Path):
    config_dir = tmp_path / "config"
    shutil.copytree(TEST_CONFIG, config_dir)
    set_category_selected(config_dir, "pasta", False)  # one category keeps the counts simple
    config = load_config(config_dir)
    engine = make_engine(sqlite_url(tmp_path / "db.sqlite"))
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    with sessions.begin() as session:
        sync_config(session, config)
    shop = FakeShop()
    rt = Runtime(config=config, root=tmp_path, sessions=sessions, clock=FixedClock(datetime(2026, 10, 23, 10, tzinfo=UTC)),
                 transport=httpx.MockTransport(shop), sleep=lambda _: None)
    rt.shop = shop
    yield rt
    engine.dispose()


def count(rt: Runtime, model, *where) -> int:
    with rt.sessions() as session:
        return session.scalar(select(func.count()).select_from(model).where(*where))


def test_source_keys_come_only_from_selected_categories(runtime: Runtime) -> None:
    assert source_keys(runtime.config, Source.COOP_CATALOG) == ["dairy/milk"]
    assert source_keys(runtime.config, Source.CONAD_CATALOG) == ["0404"]  # "04" belongs to unselected 'dairy'


def test_successful_run_stores_everything(runtime: Runtime) -> None:
    result = collect(runtime, FakeCollector())
    assert result.status is RunStatus.OK, result.error
    assert (result.items, result.requests) == (3, 2)
    assert runtime.shop.requests == ["/store", "/cat/dairy/milk"]  # unselected categories: no request
    assert count(runtime, Product) == 3 and count(runtime, PriceObservation) == 3
    assert count(runtime, Promotion) == 1 and count(runtime, PromotionSighting) == 1
    with runtime.sessions() as session:
        item = session.scalar(select(PromotionItem))
        assert item.product_id is not None  # linked by external id
        run = session.get(ScrapeRun, result.run_id)
        assert (run.status, run.items, run.requests, run.error) == ("ok", 3, 2, None)
    assert len(list((runtime.raw_root / "coop_catalog" / str(result.run_id)).glob("*.meta.json"))) == 2


def test_wrong_context_fails_without_storing(runtime: Runtime) -> None:
    runtime.shop.store = "another-store"
    result = collect(runtime, FakeCollector())
    assert result.status is RunStatus.FAILED
    assert "selected store is not the expected one" in result.error
    assert count(runtime, PriceObservation) == 0 and result.requests == 1


def test_refused_request_fails_the_run(runtime: Runtime) -> None:
    class Greedy(FakeCollector):
        def fetch(self, client, source_keys) -> None:
            client.get("https://shop.test/cat/x", "x", params={"pmin": "0,01"})

    result = collect(runtime, Greedy())
    assert result.status is RunStatus.FAILED and "forbidden by robots.txt" in result.error


def test_drop_in_products_marks_run_partial(runtime: Runtime) -> None:
    runtime.shop.products = 10
    assert collect(runtime, FakeCollector()).status is RunStatus.OK
    runtime.shop.products = 2
    result = collect(runtime, FakeCollector())
    assert result.status is RunStatus.PARTIAL
    assert "previous run had 10" in result.warnings[0]


def test_same_promotion_in_two_runs_is_one_promotion_two_sightings(runtime: Runtime) -> None:
    collect(runtime, FakeCollector())
    collect(runtime, FakeCollector())
    assert count(runtime, Promotion) == 1 and count(runtime, PromotionSighting) == 2
    assert count(runtime, PriceObservation) == 6  # append-only history


def test_parse_failure_stores_nothing_and_reparse_recovers(runtime: Runtime) -> None:
    result = collect(runtime, FakeCollector(broken=True))
    assert result.status is RunStatus.FAILED and "layout changed" in result.error
    assert count(runtime, Product) == 0
    snapshots = runtime.raw_root / "coop_catalog" / str(result.run_id)
    assert len(list(snapshots.glob("*.meta.json"))) == 2  # kept for reparse

    runtime.transport = httpx.MockTransport(lambda request: pytest.fail("reparse must not use the network"))
    fixed = reparse(runtime, FakeCollector(), result.run_id)
    assert fixed.status is RunStatus.OK and count(runtime, PriceObservation) == 3


def test_reparse_twice_gives_same_rows(runtime: Runtime) -> None:
    run_id = collect(runtime, FakeCollector()).run_id
    reparse(runtime, FakeCollector(), run_id)
    reparse(runtime, FakeCollector(), run_id)
    assert count(runtime, PriceObservation) == 3 and count(runtime, PromotionSighting) == 1
    with runtime.sessions() as session:
        assert session.get(ScrapeRun, run_id).requests == 2  # reparse keeps the original count


def test_reparse_unknown_run_is_an_error(runtime: Runtime) -> None:
    with pytest.raises(ValueError, match="does not exist"):
        reparse(runtime, FakeCollector(), 999)


def test_snapshot_metadata_is_json(runtime: Runtime) -> None:
    result = collect(runtime, FakeCollector())
    meta = sorted((runtime.raw_root / "coop_catalog" / str(result.run_id)).glob("*.meta.json"))[1]
    assert json.loads(meta.read_text(encoding="utf-8"))["context"] == {"key": "dairy/milk"}


def test_product_listed_twice_in_one_run_gets_one_price(runtime: Runtime) -> None:
    class TwoPages(FakeCollector):
        def fetch(self, client, source_keys) -> None:
            for page in (1, 2):   # the same products on two pages (e.g. two site categories)
                client.get("https://shop.test/cat/dairy/milk", f"page {page}", context={"key": "dairy/milk"})

    result = collect(runtime, TwoPages())
    assert result.status is RunStatus.OK, result.error
    assert count(runtime, PriceObservation) == 3


def test_offer_sources_also_collect_the_keys_of_groups(runtime: Runtime) -> None:
    from pisaprices.config.schemas import CategoriesFile
    tree = CategoriesFile.model_validate({"categories": [
        {"slug": "dairy", "name": "Dairy", "sources": {"lidl_offers": ["Cibo/Dairy"], "conad_catalog": ["04"]}},
        {"slug": "milk", "name": "Milk", "parent": "dairy", "selected": True, "review": False, "sources": {"conad_catalog": ["0404"]}},
    ]})
    config = runtime.config.model_copy(update={"categories": tree})
    assert source_keys(config, Source.LIDL_OFFERS) == ["Cibo/Dairy"]
    assert source_keys(config, Source.CONAD_CATALOG) == ["0404"]   # a catalog never fetches the group page
