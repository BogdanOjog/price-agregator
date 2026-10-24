"""Opening the application: project root, configuration, database (migrated), config synced."""

import os
from dataclasses import replace
from pathlib import Path

import httpx
from alembic import command
from alembic.config import Config
from sqlalchemy.orm import sessionmaker

from pisaprices.config.files import load_config
from pisaprices.config.schemas import AppConfig
from pisaprices.services.clock import Clock, SystemClock
from pisaprices.services.collect import Runtime
from pisaprices.storage.session import make_engine, sqlite_url
from pisaprices.storage.sync import SyncReport, sync_config


def project_root(start: Path | None = None) -> Path:
    """PISAPRICES_HOME, or the nearest folder (upwards) holding alembic.ini and config/."""
    if home := os.environ.get("PISAPRICES_HOME"):
        return Path(home).resolve()
    here = (start or Path.cwd()).resolve()
    for folder in (here, *here.parents):
        if (folder / "alembic.ini").exists() and (folder / "config").is_dir():
            return folder
    raise FileNotFoundError("project root not found: run inside the project folder or set PISAPRICES_HOME")


def database_url(root: Path, config: AppConfig) -> str:
    return sqlite_url(root / config.settings.paths.db)


def migrate(root: Path, config: AppConfig) -> None:
    (root / config.settings.paths.db).parent.mkdir(parents=True, exist_ok=True)
    alembic = Config(str(root / "alembic.ini"))
    alembic.set_main_option("script_location", str(root / "migrations"))
    alembic.attributes["db_url"] = database_url(root, config)
    alembic.attributes["skip_logging_config"] = True
    command.upgrade(alembic, "head")


def open_runtime(root: Path | None = None, *, clock: Clock | None = None,
                 transport: httpx.BaseTransport | None = None) -> tuple[Runtime, SyncReport]:
    root = root or project_root()
    config = load_config(root / "config")
    migrate(root, config)
    engine = make_engine(database_url(root, config))
    sessions = sessionmaker(engine, expire_on_commit=False)
    with sessions.begin() as session:
        report = sync_config(session, config)
    return Runtime(config=config, root=root, sessions=sessions, clock=clock or SystemClock(), transport=transport), report


def with_only_categories(runtime: Runtime, slugs: list[str]) -> Runtime:
    """A runtime whose selection is limited to `slugs` (for one-off runs); the YAML is not touched."""
    known = {c.slug for c in runtime.config.categories.categories}
    unknown = set(slugs) - known
    if unknown:
        raise ValueError(f"unknown categories: {sorted(unknown)}")
    categories = [c.model_copy(update={"selected": c.slug in slugs and c.selected}) for c in runtime.config.categories.categories]
    if not any(c.selected for c in categories):
        raise ValueError(f"none of {slugs} is selected in categories.yaml")
    config = runtime.config.model_copy(update={"categories": runtime.config.categories.model_copy(update={"categories": categories})})
    return replace(runtime, config=config)
