from datetime import timedelta
from pathlib import Path

from pisaprices.domain.enums import Source
from pisaprices.services import health
from pisaprices.services.clock import FixedClock
from pisaprices.services.collect import collect
from pisaprices.services.conad_session import import_session
from pisaprices.services.sources import build_collector
from tests.helpers import NOW, TESTS, make_runtime, routes

SYN = TESTS / "synthetic"


def test_doctor_reports_missing_failed_and_stale_sources(tmp_path: Path) -> None:
    rt = make_runtime(tmp_path, routes({"https://www.lidl.it/c/": SYN / "lidl_offers" / "week_page.html",
                                        "https://www.lidl.it/": SYN / "lidl_offers" / "home.html"}))
    collect(rt, build_collector(Source.LIDL_OFFERS, rt.config, tmp_path))
    report = health.doctor(rt, check_conad=False)
    by_source = {s.source: s for s in report.sources}
    assert by_source["lidl_offers"].problems == []
    assert by_source["coop_catalog"].problems == ["never collected"]
    assert not report.ok

    rt.clock = FixedClock(NOW + timedelta(days=10))
    stale = {s.source: s for s in health.doctor(rt, check_conad=False).sources}["lidl_offers"]
    assert stale.age_days == 10.0 and "days old" in stale.problems[0]


def test_doctor_checks_conad_session(tmp_path: Path) -> None:
    import_session(tmp_path, (SYN / "conad_catalog" / "copied_curl.txt").read_text(encoding="utf-8"))
    rt = make_runtime(tmp_path, routes({"/": SYN / "conad_catalog" / "home_no_store.html"}))
    ok, message = health.doctor(rt).conad_session
    assert not ok and "conad-session import" in message


def test_categories_check_finds_keys_gone_from_the_site(tmp_path: Path) -> None:
    import_session(tmp_path, (SYN / "conad_catalog" / "copied_curl.txt").read_text(encoding="utf-8"))
    rt = make_runtime(tmp_path, routes({
        "https://prenotalaspesa.coopfirenze.it/": '<a href="/pasta/semola/">pasta</a><a href="/dairy/milk/">milk</a>',
        "https://spesaonline.conad.it/": '<a href="/c/latte--0404">latte</a>',   # 1004 (pasta) is gone
        "https://www.lidl.it/": SYN / "lidl_offers" / "home.html",
        "https://www.eurospin.com/it-it/prodotti/": '<input name="by_category" value="/0006"><input name="by_category" value="/0009">',
    }))
    checks = {c.source: c for c in health.check_categories(rt)}
    assert checks["coop_catalog"].missing == [] and checks["coop_catalog"].error is None
    assert checks["conad_catalog"].missing == ["1004"]
    assert checks["eurospin_offers"].unmapped == ["/0009"]
    assert checks["lidl_offers"].error is None
