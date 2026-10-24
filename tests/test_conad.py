from pathlib import Path

import pytest
from sqlalchemy import func, select

from pisaprices.config.files import load_config
from pisaprices.domain.enums import Channel, Program, RunStatus, Source
from pisaprices.ingest.sources.conad_catalog import ConadCatalog, category_links, point_of_service
from pisaprices.services.collect import collect
from pisaprices.services.conad_session import check_session, cookies_from_curl, import_session
from pisaprices.services.sources import build_collector
from pisaprices.storage.orm import PriceObservation, Promotion
from tests.helpers import TESTS, make_runtime, routes, snapshot

SYN = TESTS / "synthetic" / "conad_catalog"
BASE = "https://spesaonline.conad.it"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    import_session(tmp_path, (SYN / "copied_curl.txt").read_text(encoding="utf-8"))
    return tmp_path


def conad(root: Path) -> ConadCatalog:
    return build_collector(Source.CONAD_CATALOG, load_config(TESTS / "config"), root)


def test_cookies_from_curl_keeps_session_cookies_only(project: Path) -> None:
    cookies = cookies_from_curl((SYN / "copied_curl.txt").read_text(encoding="utf-8"))
    assert cookies["affinity"] == "0123456789abcdef" and "AMCV_X" in cookies
    saved = (project / "data" / "conad_session.txt").read_text(encoding="utf-8")
    assert "ecAccess=header.payload.signature" in saved and "AMCV_X" not in saved and "s_ecid" not in saved


def test_import_refuses_session_without_store(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="ecAccess"):
        import_session(tmp_path, "curl 'https://x' -b 'ecSess=1; ecRoute=2'")
    with pytest.raises(ValueError, match="no cookies"):
        import_session(tmp_path, "curl 'https://x' -H 'accept: text/html'")


def test_missing_session_file_is_explained(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="conad-session import"):
        conad(tmp_path)


def test_page_helpers() -> None:
    assert point_of_service((SYN / "home.html").read_text(encoding="utf-8"))["name"] == "010529"
    assert point_of_service((SYN / "home_no_store.html").read_text(encoding="utf-8")) is None
    assert category_links((SYN / "home.html").read_text(encoding="utf-8"))["1004"] == "/c/pasta-di-semola--1004"


def test_parse_cards(project: Path) -> None:
    batch = conad(project).parse(snapshot(SYN / "category_1004_page1.html", {"key": "1004"}))
    assert [p.external_id for p in batch.products] == ["900001", "900002", "900003"]  # carousel skipped
    assert all(p.category_slug == "pasta" and p.net_qty == p.net_qty for p in batch.products)
    prices = {p.external_id: p.price_cents for p in batch.prices}
    assert prices == {"900001": 120, "900002": 100, "900003": 200}  # regular = basePrice
    online = next(p for p in batch.promotions if p.program is None)
    assert online.params == {"promo_price_cents": 80} and online.channel is Channel.ONLINE
    assert str(online.valid_until) == "2026-11-04" and online.prior_price_cents == 100
    card = next(p for p in batch.promotions if p.program is Program.CONAD_CARTA_INSIEME)
    assert card.params == {"promo_price_cents": 150} and card.channel is Channel.BOTH


def test_generic_page_is_refused(project: Path, tmp_path: Path) -> None:
    page = tmp_path / "generic.html"
    page.write_text((SYN / "category_1004_page1.html").read_text(encoding="utf-8").replace('"name":"010529"', '"name":"099999"'), encoding="utf-8")
    with pytest.raises(ValueError, match="099999"):
        conad(project).parse(snapshot(page, {"key": "1004"}))


def test_collect_with_session(project: Path) -> None:
    log: list[str] = []
    transport = routes({
        f"{BASE}/c/level2/_jcr_content/root/search.loader.html/pasta-di-semola--1004.html": SYN / "category_1004_page2.html",
        "/c/pasta-di-semola--1004": SYN / "category_1004_page1.html",
        "/": SYN / "home.html",
    }, log)
    rt = make_runtime(project, transport)
    result = collect(rt, build_collector(Source.CONAD_CATALOG, rt.config, project))
    assert result.status is RunStatus.OK, result.error
    assert any(u.endswith("pasta-di-semola--1004.html?page=2") for u in log)
    assert not any("protection" in u or "set-ecaccess" in u for u in log)
    with rt.sessions() as session:
        assert session.scalar(select(func.count()).select_from(PriceObservation)) == 5
        assert session.scalar(select(func.count()).select_from(Promotion)) == 2


def test_expired_session_stops_without_generic_prices(project: Path) -> None:
    rt = make_runtime(project, routes({"/": SYN / "home_no_store.html"}))
    result = collect(rt, build_collector(Source.CONAD_CATALOG, rt.config, project))
    assert result.status is RunStatus.FAILED and "conad-session import" in result.error
    ok, message = check_session(rt)
    assert not ok and "010529" in message
    with rt.sessions() as session:
        assert session.scalar(select(func.count()).select_from(PriceObservation)) == 0


def test_check_session_ok(project: Path) -> None:
    rt = make_runtime(project, routes({"/": SYN / "home.html"}))
    assert check_session(rt) == (True, "Conad session is on store 010529")


def test_al_kg_products_are_priced_per_kg(project: Path) -> None:
    """The card price of a fruit "al kg" is for one estimated piece ("400 g", 0,83 €, 2,08 €/Kg)."""
    batch = conad(project).parse(snapshot(SYN / "category_1004_page2.html", {"key": "1004"}))
    fruit = next(p for p in batch.products if p.external_id == "900009-1_N")
    price = next(p for p in batch.prices if p.external_id == "900009-1_N")
    assert fruit.variable_weight and fruit.net_qty == 1000
    assert price.price_cents == 208 and not price.needs_review
