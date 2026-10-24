from datetime import date
from pathlib import Path

import pytest

from pisaprices.services import lists, naming, quick
from pisaprices.services.conad_session import import_session
from pisaprices.services.runs import collect_sources
from tests.helpers import TESTS, make_runtime, routes
from tests.test_cli import SITE


@pytest.fixture
def runtime(tmp_path: Path):
    import_session(tmp_path, (TESTS / "synthetic" / "conad_catalog" / "copied_curl.txt").read_text(encoding="utf-8"))
    rt = make_runtime(tmp_path, routes(SITE))
    collect_sources(rt)
    return rt


def test_interpret_finds_category_quantity_and_words(runtime) -> None:
    spaghetti, penne, unknown, counted = quick.interpret(runtime, "spaghetti 1 kg\n- penne\nnutella\n3 fusilli")
    assert (spaghetti.category, spaghetti.quantity, spaghetti.words) == ("pasta", "1 kg", ["spaghetti"])
    assert penne.category == "pasta" and penne.quantity.endswith(" g") and "ghicită" in penne.note
    assert unknown.category is None and "nu găsesc" in unknown.note
    assert counted.quantity == "3 pz"


def test_head_of_a_product_name() -> None:
    assert naming.head("Penne farina di ceci bio 250 gr", "TERRANOSTRA VEGAN") == ["penne", "farina"]
    assert naming.head("Biscotti al latte", None) == ["biscotti"]
    assert naming.head("Barilla Spaghetti n.5 500 g", "BARILLA")[0] == "spaghetti"
    assert naming.display_brand("COOP -") == "Coop" and naming.display_brand("DE CECCO") == "De Cecco"


def test_a_brand_restricts_the_plan(runtime) -> None:
    [any_brand] = quick.interpret(runtime, "spaghetti 1 kg")
    assert {"Marca", "Marca Due"} <= set(any_brand.brands), "the brands of the matches are offered"
    chosen = quick.Draft(**{**any_brand.__dict__, "brand": "Marca Due"})
    result = quick.plan_drafts(runtime, [chosen], start=date(2026, 10, 26), window_days=7)
    assert [c.product.brand for by_store in result.matrix.values() for c in by_store.values()] == ["MARCA DUE"]


def test_category_name_is_not_required_in_the_product_name(runtime) -> None:
    [pasta] = quick.interpret(runtime, "pasta spaghetti")
    assert pasta.category == "pasta" and pasta.words == ["spaghetti"]   # "pasta" names the category


def test_plan_drafts_without_saving_and_save_as_list(runtime) -> None:
    drafts = quick.interpret(runtime, "spaghetti 1 kg\npenne 800 g\nnutella")
    result = quick.plan_drafts(runtime, drafts, start=date(2026, 10, 26), window_days=7)
    assert result.simplest is not None and result.best is not None
    assert set(result.matrix) == set(result.needs) and len(result.needs) == 2   # unknown lines are left out
    assert all(name for name in result.store_names.values())
    totals = [r.plan.total_cents for r in result.options]   # each extra store must save something to be shown
    assert result.options[0] is result.simplest and all(a > b for a, b in zip(totals, totals[1:]))
    list_id = quick.save_as_list(runtime, "Prova", drafts)
    assert [i["label"] for i in lists.get_list(runtime, list_id)["items"]] == ["spaghetti 1 kg", "penne 800 g"]


def test_nothing_recognised_is_explained(runtime) -> None:
    with pytest.raises(ValueError, match="niciun produs"):
        quick.plan_drafts(runtime, quick.interpret(runtime, "nutella"))
