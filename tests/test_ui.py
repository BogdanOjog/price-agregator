"""Every UI page renders without errors on a seeded throw-away project (Streamlit AppTest)."""

import shutil
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from pisaprices.services import lists
from pisaprices.services.app import open_runtime
from pisaprices.services.conad_session import import_session
from pisaprices.services.runs import collect_sources
from tests.helpers import TESTS, routes
from tests.test_cli import SITE

UI = TESTS.parent / "ui"
PAGES = ["page_shopping", "page_catalog", "page_promotions", "page_lists", "page_review", "page_settings", "page_categories"]


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("ui") / "project"
    root.mkdir()
    shutil.copy(TESTS.parent / "alembic.ini", root / "alembic.ini")
    shutil.copytree(TESTS.parent / "migrations", root / "migrations", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(TESTS / "config", root / "config")
    import_session(root, (TESTS / "synthetic" / "conad_catalog" / "copied_curl.txt").read_text(encoding="utf-8"))
    runtime, _ = open_runtime(root, transport=routes(SITE))
    runtime.sleep = lambda _: None
    collect_sources(runtime)
    lists.import_yaml(runtime, "name: Prova\nitems:\n  - {label: Pasta, category: pasta, quantity: 1 kg}\n")
    runtime.sessions.kw["bind"].dispose()
    return root


@pytest.mark.parametrize("page", PAGES)
def test_page_renders(project: Path, page: str, monkeypatch) -> None:
    monkeypatch.setenv("PISAPRICES_HOME", str(project))
    script = f"import sys\nsys.path.insert(0, {str(UI)!r})\nimport pages\npages.runtime.clear()\npages.{page}()\n"
    app = AppTest.from_string(script, default_timeout=60).run()
    assert not app.exception, [e.message for e in app.exception]
    assert app.title


def test_shopping_flow_from_text_to_plan(project: Path, monkeypatch) -> None:
    monkeypatch.setenv("PISAPRICES_HOME", str(project))
    script = f"import sys\nsys.path.insert(0, {str(UI)!r})\nimport pages\npages.page_shopping()\n"
    app = AppTest.from_string(script, default_timeout=60).run()
    app.text_area[0].input("spaghetti 1 kg\npenne\nnutella").run()
    next(b for b in app.button if b.label == "Caută").click().run()   # one click: interpretation and plan
    assert not app.exception, [e.message for e in app.exception]
    assert app.metric, "no plan shown"
    assert any("🛒" in s.value for s in app.subheader)
    assert any("nutella" in w.value for w in app.warning)   # what was not found is said, not hidden
    next(b for b in app.button if b.label == "Recalculează").click().run()
    assert not app.exception, [e.message for e in app.exception]
    assert any("🛒" in s.value for s in app.subheader)


def test_a_category_proposal_is_confirmed_from_the_review_page(project: Path, monkeypatch) -> None:
    import sqlite3
    db = sqlite3.connect(project / "data" / "test.db")
    product_id = db.execute("select id from products where category_id is not null limit 1").fetchone()[0]
    db.execute("update products set category_id = null where id = ?", (product_id,))
    db.execute("insert into review_items (kind, subject_table, subject_id, payload, status, created_at) values "
               "('category_rule', 'products', ?, ?, 'open', '2026-10-24T00:00:00Z')",
               (product_id, f'{{"key": "category:{product_id}", "name": "Prodotto di prova", "chain": "coop", "suggestion": "pasta"}}'))
    db.commit()
    db.close()
    monkeypatch.setenv("PISAPRICES_HOME", str(project))
    script = f"import sys\nsys.path.insert(0, {str(UI)!r})\nimport pages\npages.runtime.clear()\npages.page_review()\n"
    app = AppTest.from_string(script, default_timeout=60).run()
    assert any("Produse de încadrat" in s.value for s in app.subheader)
    next(b for b in app.button if b.label == "Confirmă").click().run()
    assert not app.exception, [e.message for e in app.exception]
    db = sqlite3.connect(project / "data" / "test.db")
    assert db.execute("select c.slug from products p join categories c on c.id = p.category_id where p.id = ?",
                      (product_id,)).fetchone() == ("pasta",)
    db.close()
