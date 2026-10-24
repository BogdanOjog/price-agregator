"""Reading and writing config/*.yaml.

Writes keep comments and key order (ruamel round-trip), validate the whole resulting
configuration before touching the disk, and replace the file atomically."""

import io
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

from pisaprices.config.schemas import AppConfig, CategoriesFile, Settings, StoresFile, UserConfig
from pisaprices.domain.enums import Program

FILES = {"settings": "settings.yaml", "stores": "stores.yaml", "user": "user.yaml", "categories": "categories.yaml"}
MODELS = {"settings": Settings, "stores": StoresFile, "user": UserConfig, "categories": CategoriesFile}


class ConfigError(Exception):
    """A config file is missing, malformed or inconsistent. The message names the file and the field."""


def _round_trip() -> YAML:
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 4096
    yaml.indent(mapping=2, sequence=4, offset=2)
    return yaml


def _read(path: Path) -> Any:
    if not path.exists():
        hint = " (copy config/user.example.yaml)" if path.name == "user.yaml" else ""
        raise ConfigError(f"{path}: file not found{hint}")
    try:
        return YAML(typ="safe").load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ConfigError(f"{path}: invalid YAML: {exc}") from exc


def _describe(error: ValidationError) -> str:
    lines = []
    for item in error.errors():
        where = ".".join(str(part) for part in item["loc"]) or "(file)"
        lines.append(f"  {where}: {item['msg']}")
    return "\n".join(lines)


def _validate(config_dir: Path, raw: dict[str, Any]) -> AppConfig:
    parts = {}
    for key, model in MODELS.items():
        try:
            parts[key] = model.model_validate(raw[key])
        except ValidationError as exc:
            raise ConfigError(f"{config_dir / FILES[key]}:\n{_describe(exc)}") from exc
    try:
        return AppConfig(**parts)
    except ValidationError as exc:
        raise ConfigError(f"{config_dir}: files are inconsistent:\n{_describe(exc)}") from exc


def load_config(config_dir: Path) -> AppConfig:
    return _validate(config_dir, {key: _read(config_dir / name) for key, name in FILES.items()})


def update_file(config_dir: Path, key: str, mutate: Callable[[CommentedMap], None]) -> AppConfig:
    """Apply `mutate` to one file's round-trip document; write it only if the whole config stays valid."""
    path = config_dir / FILES[key]
    yaml = _round_trip()
    document = yaml.load(path.read_text(encoding="utf-8"))
    mutate(document)
    buffer = io.StringIO()
    yaml.dump(document, buffer)
    text = buffer.getvalue()

    raw = {k: _read(config_dir / name) for k, name in FILES.items() if k != key}
    raw[key] = YAML(typ="safe").load(text)
    config = _validate(config_dir, raw)

    handle, temp = tempfile.mkstemp(dir=config_dir, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as file:
            file.write(text)
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise
    return config


def set_category_selected(config_dir: Path, slug: str, selected: bool) -> AppConfig:
    def mutate(document: CommentedMap) -> None:
        for category in document["categories"]:
            if category["slug"] == slug:
                category["selected"] = selected
                return
        raise ConfigError(f"categories.yaml: no category '{slug}'")

    return update_file(config_dir, "categories", mutate)


def set_program(config_dir: Path, program: Program, has_card: bool, n_cards: int = 1) -> AppConfig:
    def mutate(document: CommentedMap) -> None:
        entry = document["programs"].setdefault(program.value, CommentedMap())
        entry["has_card"] = has_card
        entry["n_cards"] = n_cards

    return update_file(config_dir, "user", mutate)


def update_user_store(config_dir: Path, slug: str, **fields: Any) -> AppConfig:
    """Set travel fields of one store in user.yaml (e.g. travel_minutes_one_way=12, on_route_days=["tue"])."""

    def mutate(document: CommentedMap) -> None:
        stores = document["stores"]
        if slug not in stores:
            raise ConfigError(f"user.yaml: no store '{slug}'")
        for name, value in fields.items():
            stores[slug][name] = value

    return update_file(config_dir, "user", mutate)
