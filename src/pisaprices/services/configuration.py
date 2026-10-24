"""Configuration use cases for the CLI and the UI (they may not import the config layer directly)."""

from pathlib import Path

from pisaprices.config import files
from pisaprices.config.files import ConfigError
from pisaprices.config.schemas import AppConfig
from pisaprices.domain.enums import Program

__all__ = ["ConfigError", "categories", "load", "select_category", "set_program", "update_user_store"]


def load(root: Path) -> AppConfig:
    return files.load_config(root / "config")


def categories(root: Path) -> list[dict]:
    config = load(root)
    return [{"slug": c.slug, "name": c.name, "parent": c.parent, "selected": c.selected, "review": c.review,
             "sources": {s.value: keys for s, keys in c.sources.items()}} for c in config.categories.categories]


def select_category(root: Path, slug: str, selected: bool) -> None:
    files.set_category_selected(root / "config", slug, selected)


def set_program(root: Path, program: str, has_card: bool, n_cards: int = 1) -> None:
    files.set_program(root / "config", Program(program), has_card, n_cards)


def update_user_store(root: Path, slug: str, **fields) -> None:
    files.update_user_store(root / "config", slug, **fields)
