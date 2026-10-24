import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from pisaprices.config.files import load_config
from pisaprices.domain.enums import Chain, ReviewKind, Unit
from pisaprices.ingest.records import ProductRef
from pisaprices.ingest.resolve import Candidate, resolve
from pisaprices.services.clock import FixedClock
from pisaprices.services.collect import Runtime
from pisaprices.services.review import accept, open_reviews, reject, resolve_pending
from pisaprices.storage.orm import Base, Product, Promotion, PromotionItem
from pisaprices.storage.session import make_engine, sqlite_url

CANDIDATES = [
    Candidate(1, "coop", "0100635078500", None, "Spaghettoni n. 412 pasta di semola 500 gr", 500, "g"),
    Candidate(2, "coop", "0100700342600", "8005110001001", "Pomodori pelati 2 pz da 400 gr", 800, "g"),
    Candidate(3, "conad", "401495", "8003170062443", "Pizzoccheri della Valtellina IGP 500 g Conad", 500, "g"),
    Candidate(4, "coop", "0100999000000", None, "Mozzarella di bufala campana DOP 500 g", 500, "g"),
]


def ref(**values) -> ProductRef:
    return ProductRef(**{"chain": Chain.COOP, "name": "x", **values})


def test_external_id_links() -> None:
    assert resolve(ref(external_id="0100635078500"), CANDIDATES, 90).product_id == 1


def test_ean_links_within_chain_only() -> None:
    assert resolve(ref(ean="8005110001001"), CANDIDATES, 90).product_id == 2
    # a Conad EAN never links a Coop reference
    assert resolve(ref(ean="8003170062443"), CANDIDATES, 90).product_id is None


def test_fuzzy_match_is_only_a_proposal() -> None:
    result = resolve(ref(name="MOZZARELLA DI BUFALA CAMPANA DOP 500 g", net_qty=500, unit=Unit.GRAM), CANDIDATES, 90)
    assert result.product_id is None and result.proposal_id == 4 and result.method == "fuzzy"


def test_fuzzy_needs_the_same_quantity() -> None:
    assert resolve(ref(name="Mozzarella di bufala campana DOP 250 g", net_qty=250, unit=Unit.GRAM), CANDIDATES, 90).proposal_id is None
    assert resolve(ref(name="Mozzarella di bufala campana DOP"), CANDIDATES, 90).proposal_id is None


def test_below_threshold_is_no_match() -> None:
    assert resolve(ref(name="Spaghetti integrali bio 500 g", net_qty=500, unit=Unit.GRAM), CANDIDATES, 90) .method == "none"


@pytest.fixture
def runtime(tmp_path: Path):
    shutil.copytree(Path(__file__).parent / "config", tmp_path / "config")
    engine = make_engine(sqlite_url(tmp_path / "db.sqlite"))
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    rt = Runtime(config=load_config(tmp_path / "config"), root=tmp_path, sessions=sessions,
                 clock=FixedClock(datetime(2026, 10, 23, tzinfo=UTC)))
    with sessions.begin() as session:
        session.add_all([
            Product(chain="coop", external_id="A", name="Mozzarella di bufala campana DOP 500 g", net_qty=500, unit="g"),
            Product(chain="coop", external_id="B", name="Latte intero 1 l", net_qty=1000, unit="ml"),
        ])
        promo = Promotion(natural_key="k", chain="coop", mechanic="PRICE_CUT", params={"promo_price_cents": 339}, raw_text="soci")
        session.add(promo)
        session.flush()
        for key, name, qty, unit in [("r1", "MOZZARELLA DI BUFALA CAMPANA DOP", 500, "g"), ("r2", "Chianti classico", 750, "ml")]:
            session.add(PromotionItem(promotion_id=promo.id, ref_key=key, product_id=None,
                                      ref=ProductRef(chain=Chain.COOP, name=name, net_qty=qty, unit=Unit(unit)).model_dump(mode="json")))
    yield rt
    engine.dispose()


def item_product(rt: Runtime, key: str) -> int | None:
    with rt.sessions() as session:
        return session.scalar(select(PromotionItem.product_id).where(PromotionItem.ref_key == key))


def test_resolve_pending_proposes_and_accept_links(runtime: Runtime) -> None:
    report = resolve_pending(runtime)
    assert (report.linked, report.proposed, report.unmatched) == (0, 1, 1)
    assert item_product(runtime, "r1") is None  # nothing linked before confirmation

    [review] = open_reviews(runtime, ReviewKind.PRODUCT_MATCH)
    assert review["payload"]["ref_name"] == "MOZZARELLA DI BUFALA CAMPANA DOP"
    assert resolve_pending(runtime).proposed == 0  # no duplicate proposal on the next run

    accept(runtime, review["id"])
    assert item_product(runtime, "r1") is not None
    assert open_reviews(runtime) == []


def test_reject_keeps_item_unlinked(runtime: Runtime) -> None:
    resolve_pending(runtime)
    [review] = open_reviews(runtime)
    reject(runtime, review["id"])
    assert item_product(runtime, "r1") is None
    with pytest.raises(ValueError, match="already decided"):
        accept(runtime, review["id"])
