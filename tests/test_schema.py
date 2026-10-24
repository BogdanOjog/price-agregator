from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pisaprices.storage.orm import (
    Base,
    PriceObservation,
    Product,
    Promotion,
    PromotionSighting,
    ScrapeRun,
    Store,
)
from pisaprices.storage.session import make_engine, sqlite_url

ROOT = Path(__file__).resolve().parents[1]
TABLES = {
    "stores", "categories", "source_categories", "canonical_products", "products", "scrape_runs",
    "price_observations", "promotions", "promotion_sightings", "promotion_items", "review_items",
    "shopping_lists", "list_items",
}


def alembic_config(db_url: str) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.attributes["db_url"] = db_url
    config.attributes["skip_logging_config"] = True
    return config


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    url = sqlite_url(tmp_path / "test.db")
    command.upgrade(alembic_config(url), "head")
    return url


@pytest.fixture
def engine(db_url: str) -> Engine:
    engine = make_engine(db_url)
    yield engine
    engine.dispose()


def store(**overrides) -> Store:
    values = dict(slug="coop_test", chain="coop", name="Coop test", cap="56124", source_mode="catalog")
    return Store(**{**values, **overrides})


def product(**overrides) -> Product:
    values = dict(chain="coop", external_id="0100000000001", name="Spaghetti n. 5 500 g", brand="Barilla")
    return Product(**{**values, **overrides})


def promotion(**overrides) -> Promotion:
    values = dict(natural_key="k1", chain="coop", mechanic="PRICE_CUT", params={"promo_price_cents": 99}, raw_text="-20%")
    return Promotion(**{**values, **overrides})


def run(**overrides) -> ScrapeRun:
    return ScrapeRun(**{**dict(source="coop_catalog", started_at="2026-10-23T10:00:00Z"), **overrides})


def search(session: Session, query: str) -> list[int]:
    rows = session.execute(text("SELECT rowid FROM products_fts WHERE products_fts MATCH :q"), {"q": query})
    return [row[0] for row in rows]


def test_upgrade_creates_tables_fts_and_view(engine: Engine) -> None:
    names = set(inspect(engine).get_table_names())
    assert TABLES <= names
    assert "products_fts" in names
    assert "current_prices" in inspect(engine).get_view_names()


def test_migration_matches_orm_models(engine: Engine) -> None:
    with engine.connect() as connection:
        context = MigrationContext.configure(
            connection,
            opts={"include_object": lambda obj, name, *a: not (name or "").startswith(("products_fts", "current_prices"))},
        )
        assert compare_metadata(context, Base.metadata) == []


def test_downgrade_removes_everything(db_url: str) -> None:
    command.downgrade(alembic_config(db_url), "base")
    engine = make_engine(db_url)
    assert set(inspect(engine).get_table_names()) <= {"alembic_version"}
    assert inspect(engine).get_view_names() == []
    engine.dispose()


def test_fts_follows_insert_update_delete(engine: Engine) -> None:
    with Session(engine) as session:
        p = product(name="Caffè macinato 250 g", brand="Lavazza")
        session.add(p)
        session.commit()
        assert search(session, "caffe") == [p.id]  # diacritics removed

        p.name = "Orzo solubile 120 g"
        session.commit()
        assert search(session, "caffe") == []
        assert search(session, "orzo") == [p.id]

        session.delete(p)
        session.commit()
        assert search(session, "orzo") == []


@pytest.mark.parametrize("cap", ["56100", "56012", "50100"])
def test_store_outside_pisa_is_rejected(engine: Engine, cap: str) -> None:
    with Session(engine) as session, pytest.raises(IntegrityError, match="cap_in_pisa"):
        session.add(store(cap=cap))
        session.commit()


def test_an_approved_store_outside_pisa_may_be_visited(engine: Engine) -> None:
    with Session(engine) as session:   # Lidl Ghezzano: the user shops there (migration a3c1e7d05b92)
        session.add(store(slug="lidl_ghezzano", cap="56010", outside_scope_approved=True, visitable=True))
        session.commit()
    with Session(engine) as session, pytest.raises(IntegrityError, match="cap_in_pisa"):   # approval still required
        session.add(store(slug="coop_elsewhere", cap="56012", outside_scope_approved=False, visitable=True))
        session.commit()


@pytest.mark.parametrize(
    "field, value",
    [("mechanic", "BUY_ONE_GET_ONE"), ("program", "carta_fedelta"), ("channel", "phone"), ("chain", "esselunga")],
)
def test_promotion_with_unknown_value_is_rejected(engine: Engine, field: str, value: str) -> None:
    with Session(engine) as session, pytest.raises(IntegrityError, match=field):
        session.add(promotion(**{field: value}))
        session.commit()


def test_promotion_natural_key_is_unique(engine: Engine) -> None:
    with Session(engine) as session:
        session.add(promotion())
        session.commit()
    with Session(engine) as session, pytest.raises(IntegrityError, match="natural_key"):
        session.add(promotion(raw_text="same promotion, other text"))
        session.commit()


def test_promotion_seen_once_per_run(engine: Engine) -> None:
    with Session(engine) as session:
        r, p = run(), promotion()
        session.add_all([r, p])
        session.flush()
        session.add(PromotionSighting(promotion_id=p.id, run_id=r.id, seen_at="2026-10-23T10:01:00Z"))
        session.commit()
        session.add(PromotionSighting(promotion_id=p.id, run_id=r.id, seen_at="2026-10-23T10:02:00Z"))
        with pytest.raises(IntegrityError):
            session.commit()


def test_observation_unique_per_product_store_run(engine: Engine) -> None:
    with Session(engine) as session:
        s, p, r = store(), product(), run()
        session.add_all([s, p, r])
        session.flush()
        obs = dict(product_id=p.id, store_id=s.id, run_id=r.id, observed_at="2026-10-23T10:00:00Z", source="online")
        session.add(PriceObservation(price_cents=129, **obs))
        session.commit()
        session.add(PriceObservation(price_cents=135, **obs))
        with pytest.raises(IntegrityError, match="UNIQUE"):
            session.commit()


def test_foreign_keys_are_enforced(engine: Engine) -> None:
    with Session(engine) as session, pytest.raises(IntegrityError, match="FOREIGN KEY"):
        session.add(PriceObservation(product_id=999, store_id=999, run_id=999, observed_at="x", price_cents=1, source="online"))
        session.commit()


def test_current_prices_returns_latest_observation(engine: Engine) -> None:
    with Session(engine) as session:
        s, p, r1, r2 = store(), product(), run(), run(started_at="2026-10-24T10:00:00Z")
        session.add_all([s, p, r1, r2])
        session.flush()
        common = dict(product_id=p.id, store_id=s.id, source="online")
        session.add_all([
            PriceObservation(run_id=r1.id, observed_at="2026-10-23T10:00:00Z", price_cents=129, **common),
            PriceObservation(run_id=r2.id, observed_at="2026-10-24T10:00:00Z", price_cents=119, **common),
        ])
        session.commit()
        rows = session.execute(text("SELECT price_cents FROM current_prices")).all()
        assert rows == [(119,)]


def test_the_stores_rebuild_keeps_collected_data(tmp_path) -> None:
    """Migration a3c1e7d05b92 rebuilds `stores`, which every table references: it must work on a full database."""
    import sqlite3

    from alembic import command
    from alembic.config import Config

    from pisaprices.services.conad_session import import_session
    from pisaprices.services.runs import collect_sources
    from tests.helpers import TESTS, make_runtime, routes
    from tests.test_cli import SITE

    import_session(tmp_path, (TESTS / "synthetic" / "conad_catalog" / "copied_curl.txt").read_text(encoding="utf-8"))
    rt = make_runtime(tmp_path, routes(SITE))
    collect_sources(rt)
    rt.sessions.kw["bind"].dispose()
    db = tmp_path / "db.sqlite"
    before = sqlite3.connect(db).execute("select count(*) from price_observations").fetchone()[0]
    config = Config(str(TESTS.parent / "alembic.ini"))
    config.set_main_option("script_location", str(TESTS.parent / "migrations"))
    config.attributes["db_url"] = f"sqlite:///{db.as_posix()}"
    config.attributes["skip_logging_config"] = True
    command.downgrade(config, "f85f9895fb8c")
    command.upgrade(config, "head")
    con = sqlite3.connect(db)
    assert before > 0 and con.execute("select count(*) from price_observations").fetchone()[0] == before
    assert con.execute("pragma foreign_key_check").fetchall() == []
    assert not con.execute("select name from sqlite_master where name like '_alembic_tmp%'").fetchall()
