import shutil
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from pisaprices.config.files import ConfigError, load_config, set_category_selected, set_program, update_user_store
from pisaprices.domain.enums import Program
from pisaprices.storage.orm import Base, Category, Store
from pisaprices.storage.session import make_engine, sqlite_url
from pisaprices.storage.sync import sync_config

TEST_CONFIG = Path(__file__).resolve().parent / "config"


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    target = tmp_path / "config"
    shutil.copytree(TEST_CONFIG, target)
    return target


def edit(config_dir: Path, name: str, old: str, new: str) -> None:
    path = config_dir / name
    text = path.read_text(encoding="utf-8")
    assert old in text, f"test setup: '{old}' not in {name}"
    path.write_text(text.replace(old, new), encoding="utf-8")


def test_test_config_is_valid(config_dir: Path) -> None:
    config = load_config(config_dir)
    assert [c.slug for c in config.selected_categories()] == ["milk", "pasta"]


def test_project_config_is_valid() -> None:
    project = TEST_CONFIG.parents[1] / "config"
    if not (project / "user.yaml").exists():
        pytest.skip("personal config/user.yaml not present (fresh clone)")
    load_config(project)


def test_user_example_is_valid_for_project_stores(tmp_path: Path) -> None:
    """A fresh clone copies user.example.yaml to user.yaml; it must load as is."""
    project = TEST_CONFIG.parents[1] / "config"
    target = tmp_path / "config"
    target.mkdir()
    for name in ("settings.yaml", "stores.yaml", "categories.yaml"):
        shutil.copy(project / name, target / name)
    shutil.copy(project / "user.example.yaml", target / "user.yaml")
    load_config(target)


@pytest.mark.parametrize(
    "name, old, new, message",
    [
        ("stores.yaml", 'cap: "56121"', 'cap: "50100"', "outside Comune di Pisa"),
        ("stores.yaml", "price_source: coop_source", "price_source: conad_a", "same chain"),
        ("stores.yaml", "price_source: coop_source", "price_source: nowhere", "does not exist"),
        ("categories.yaml", "parent: dairy\n    selected: true", "parent: drinks\n    selected: true", "parent 'drinks' does not exist"),
        ("categories.yaml", "selected: true\n    review: false", "selected: true\n    review: true", "unreviewed"),
        ("categories.yaml", "conad_catalog: [\"0404\"]", "esselunga_catalog: [\"0404\"]", "esselunga_catalog"),
        ("user.yaml", "coop_socio:", "carta_fedelta:", "carta_fedelta"),
        ("user.yaml", "transport_cost_eur: 3.0", "transport_cost_eur: null", "transport_cost_eur"),
        ("user.yaml", "  coop_a:\n", "  coop_b:\n", "'coop_b' is not in stores.yaml"),
        ("user.yaml", "on_route_days: [tue]", "on_route_days: [tuesday]", "on_route_days"),
        ("settings.yaml", "anomaly_drop_ratio: 0.7", "anomaly_drop_ratio: 1.7", "anomaly_drop_ratio"),
    ],
)
def test_invalid_config_names_file_and_field(config_dir: Path, name: str, old: str, new: str, message: str) -> None:
    edit(config_dir, name, old, new)
    with pytest.raises(ConfigError) as error:
        load_config(config_dir)
    assert message in str(error.value)
    assert str(config_dir) in str(error.value)


def test_visitable_store_needs_travel_data(config_dir: Path) -> None:
    edit(config_dir, "user.yaml", "  coop_a:\n    travel_minutes_one_way: 5   # walking\n    transport: walk\n", "")
    with pytest.raises(ConfigError, match="travel data missing for visitable stores \\['coop_a'\\]"):
        load_config(config_dir)


def test_missing_user_file_points_to_example(config_dir: Path) -> None:
    (config_dir / "user.yaml").unlink()
    with pytest.raises(ConfigError, match="user.example.yaml"):
        load_config(config_dir)


def test_write_keeps_comments_and_validates(config_dir: Path) -> None:
    update_user_store(config_dir, "coop_a", travel_minutes_one_way=7)
    text = (config_dir / "user.yaml").read_text(encoding="utf-8")
    assert "# Test user: not the personal config." in text
    assert "travel_minutes_one_way: 7   # walking" in text
    assert load_config(config_dir).user.stores["coop_a"].travel_minutes_one_way == 7


def test_invalid_write_leaves_file_untouched(config_dir: Path) -> None:
    before = (config_dir / "user.yaml").read_bytes()
    with pytest.raises(ConfigError):
        update_user_store(config_dir, "coop_a", travel_minutes_one_way=-1)
    assert (config_dir / "user.yaml").read_bytes() == before
    assert not list(config_dir.glob(".*.tmp"))


def test_selecting_unreviewed_category_is_refused(config_dir: Path) -> None:
    with pytest.raises(ConfigError, match="unreviewed"):
        set_category_selected(config_dir, "eggs", True)
    set_category_selected(config_dir, "milk", False)
    assert "milk" not in [c.slug for c in load_config(config_dir).selected_categories()]


def test_set_program_adds_missing_program(config_dir: Path) -> None:
    config = set_program(config_dir, Program.EUROSPIN_FAMILY, has_card=True, n_cards=2)
    assert config.user.programs[Program.EUROSPIN_FAMILY].n_cards == 2


def test_sync_upserts_by_slug_and_reports_orphans(config_dir: Path, tmp_path: Path) -> None:
    engine = make_engine(sqlite_url(tmp_path / "sync.db"))
    Base.metadata.create_all(engine)
    config = load_config(config_dir)

    with Session(engine) as session:
        report = sync_config(session, config)
        session.commit()
        assert "store:coop_a" in report.created and "category:milk" in report.created
        coop_a = session.scalar(select(Store).where(Store.slug == "coop_a"))
        source = session.scalar(select(Store).where(Store.slug == "coop_source"))
        assert coop_a.price_source_store_id == source.id
        milk = session.scalar(select(Category).where(Category.slug == "milk"))
        dairy = session.scalar(select(Category).where(Category.slug == "dairy"))
        assert milk.parent_id == dairy.id

    with Session(engine) as session:
        again = sync_config(session, config)
        assert again.created == again.updated == again.orphans == []

    edit(config_dir, "stores.yaml", "name: Conad A", "name: Conad A renamed")
    edit(config_dir, "categories.yaml", "  - slug: eggs\n    name: Eggs\n    parent: dairy\n    selected: false\n    review: true\n    sources: {}\n", "")
    with Session(engine) as session:
        report = sync_config(session, load_config(config_dir))
        assert report.updated == ["store:conad_a"]
        assert report.orphans == ["category:eggs"]
    engine.dispose()
