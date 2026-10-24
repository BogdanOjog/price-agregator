"""Every CLI command at least once, on a throw-away project folder with a mocked network."""

import shutil
from pathlib import Path

import pytest

from pisaprices import cli
from pisaprices.services import app
from tests.helpers import TESTS, routes

SYN = TESTS / "synthetic"
DW = "https://prenotalaspesa.coopfirenze.it/on/demandware.store/Sites-CoopFi-Site/it_IT"
SITE = {
    f"{DW}/Search-UpdateGrid": SYN / "coop_catalog" / "grid_page2.html",
    f"{DW}/Stores-SelectStore": "{}",
    f"{DW}/Stores-CurrentStore": SYN / "coop_catalog" / "current_store.html",
    "https://prenotalaspesa.coopfirenze.it/stores": SYN / "coop_catalog" / "stores.html",
    "https://prenotalaspesa.coopfirenze.it/pasta/semola/": SYN / "coop_catalog" / "category_page1.html",
    "https://prenotalaspesa.coopfirenze.it/dairy/milk/": "<html></html>",
    "https://prenotalaspesa.coopfirenze.it/": '<a href="/pasta/semola/">p</a><a href="/dairy/milk/">m</a>',
    "https://www.coopfirenze.it/": SYN / "coop_soci" / "offerte_per_i_soci.html",
    "https://spesaonline.conad.it/c/level2": SYN / "conad_catalog" / "category_1004_page2.html",
    "https://spesaonline.conad.it/c/pasta-di-semola--1004": SYN / "conad_catalog" / "category_1004_page1.html",
    "https://spesaonline.conad.it/": SYN / "conad_catalog" / "home.html",
    "https://lidl.everli.com/api/config": SYN / "lidl_everli" / "config.json",
    "https://lidl.everli.com/api/products": SYN / "lidl_everli" / "products.json",
    "https://lidl.everli.com/": SYN / "lidl_everli" / "home.html",
    "https://www.lidl.it/c/": SYN / "lidl_offers" / "week_page.html",
    "https://www.lidl.it/": SYN / "lidl_offers" / "home.html",
    "https://www.eurospin.com/it/it/home/prodotti.html?page=2": SYN / "eurospin_offers" / "listing_page2.html",
    "https://www.eurospin.com/it-it/prodotti/": SYN / "eurospin_offers" / "listing_page1.html",
}


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    shutil.copy(TESTS.parent / "alembic.ini", root / "alembic.ini")
    shutil.copytree(TESTS.parent / "migrations", root / "migrations", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(TESTS / "config", root / "config")
    real_open = app.open_runtime

    def open_with_mock(root_arg=None, **kw):
        runtime, report = real_open(root_arg, transport=routes(SITE), **kw)
        runtime.sleep = lambda _: None
        return runtime, report

    monkeypatch.setattr(cli, "open_runtime", open_with_mock)
    return root


def run(project: Path, capsys, *args: str) -> tuple[int, str]:
    code = cli.main(["--root", str(project), *args])
    return code, capsys.readouterr().out


def test_help_without_command(capsys) -> None:
    assert cli.main([]) == 0
    assert "pisaprices" in capsys.readouterr().out


def test_init_and_categories(project: Path, capsys) -> None:
    code, out = run(project, capsys, "init")
    assert code == 0 and "database ready" in out and "milk, pasta" in out
    code, out = run(project, capsys, "categories", "list", "--selected")
    assert "[x]" in out and "milk" in out
    assert run(project, capsys, "categories", "unselect", "milk")[0] == 0
    assert "milk" not in run(project, capsys, "categories", "list", "--selected")[1]
    code, out = run(project, capsys, "categories", "select", "eggs")
    assert code == 2   # unreviewed mapping: refused with an explanation


def test_collect_reparse_search_review_doctor(project: Path, capsys) -> None:
    (project / "data").mkdir()
    shutil.copy(SYN / "conad_catalog" / "copied_curl.txt", project / "curl.txt")
    code, out = run(project, capsys, "conad-session", "import", str(project / "curl.txt"))
    assert code == 0 and "on store 010529" in out

    code, out = run(project, capsys, "collect", "--all")
    assert code == 0, out
    for source in ("coop_catalog", "coop_soci", "conad_catalog", "lidl_offers", "eurospin_offers"):
        assert source in out
    code, out = run(project, capsys, "reparse", "--source", "coop_catalog", "--run", "1")
    assert code == 0 and "0 requests" in out
    code, out = run(project, capsys, "search", "prova")
    assert "products" in out and "coop" in out
    code, out = run(project, capsys, "review")
    assert "open review items" in out
    code, out = run(project, capsys, "doctor", "--offline")
    assert "coop_catalog" in out and "open review items" in out
    code, out = run(project, capsys, "categories", "check")
    assert "conad_catalog" in out

    code, out = run(project, capsys, "catalog", "browse", "pasta", "--sort", "price")
    assert code == 0 and "products" in out
    product_id = out.split()[0].lstrip("#")
    code, out = run(project, capsys, "catalog", "product", product_id)
    assert code == 0 and "history" in out
    code, out = run(project, capsys, "catalog", "promos", "--date", "2026-10-26")
    assert code == 0 and "active promotions" in out

    (project / "lista.yaml").write_text("name: Prova\nitems:\n  - {label: Pasta, category: pasta, quantity: 1 kg}\n", encoding="utf-8")
    code, out = run(project, capsys, "list", "import", str(project / "lista.yaml"))
    assert code == 0 and "imported" in out
    assert "Prova" in run(project, capsys, "list", "all")[1]
    assert "Pasta" in run(project, capsys, "list", "show", "1")[1]
    assert "category: pasta" in run(project, capsys, "list", "export", "1")[1]
    code, out = run(project, capsys, "plan", "--list-id", "1", "--from", "2026-10-26")
    assert code == 0 and "K=1" in out

    code, out = run(project, capsys, "fixtures", "capture", "--source", "lidl_offers")
    assert code == 0 and (project / "tests" / "fixtures" / "lidl_offers" / "captured").exists()


def test_errors_are_short_messages(project: Path, capsys) -> None:
    code = cli.main(["--root", str(project), "plan"])
    assert code == 2 and "--list" in capsys.readouterr().err
