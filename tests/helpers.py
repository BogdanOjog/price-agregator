"""Shared test setup: a project root with the test config, a database and a fixed clock."""

import shutil
from datetime import UTC, datetime
from pathlib import Path

import httpx
from alembic import command
from alembic.config import Config
from sqlalchemy.orm import sessionmaker

from pisaprices.config.files import load_config
from pisaprices.ingest.snapshots import RawSnapshot
from pisaprices.services.clock import FixedClock
from pisaprices.services.collect import Runtime
from pisaprices.storage.session import make_engine, sqlite_url
from pisaprices.storage.sync import sync_config

TESTS = Path(__file__).resolve().parent
NOW = datetime(2026, 10, 23, 10, tzinfo=UTC)


def migrate(url: str) -> None:
    """The real migrations (tables, FTS5, current_prices view), as the application runs them."""
    config = Config(str(TESTS.parent / "alembic.ini"))
    config.set_main_option("script_location", str(TESTS.parent / "migrations"))
    config.attributes["db_url"] = url
    config.attributes["skip_logging_config"] = True
    command.upgrade(config, "head")


def make_runtime(tmp_path: Path, transport: httpx.BaseTransport | None = None, now: datetime = NOW) -> Runtime:
    config_dir = tmp_path / "config"
    if not config_dir.exists():
        shutil.copytree(TESTS / "config", config_dir)
    config = load_config(config_dir)
    url = sqlite_url(tmp_path / "db.sqlite")
    migrate(url)
    engine = make_engine(url)
    sessions = sessionmaker(engine, expire_on_commit=False)
    with sessions.begin() as session:
        sync_config(session, config)
    return Runtime(config=config, root=tmp_path, sessions=sessions, clock=FixedClock(now), transport=transport,
                   sleep=lambda _: None)


def snapshot(path: Path, context: dict | None = None, source: str = "test", fetched_at: str = "2026-10-23T10:00:00Z") -> RawSnapshot:
    """A snapshot pointing at an existing file (synthetic or real fixture)."""
    content_type = "application/json" if path.suffix == ".json" else "text/html"
    return RawSnapshot(source, 1, 1, path.stem, f"file://{path.name}", 200, content_type, fetched_at, path, context or {})


def routes(mapping: dict[str, Path | str], log: list[str] | None = None) -> httpx.MockTransport:
    """Mock site: URL path (and query, if given) -> file or text. Unknown URLs fail the test loudly."""

    def handler(request: httpx.Request) -> httpx.Response:
        if log is not None:
            log.append(str(request.url))
        for key, body in mapping.items():
            if (key.startswith("http") and str(request.url).startswith(key)) or request.url.path == key:
                text = body.read_text(encoding="utf-8") if isinstance(body, Path) else body
                kind = "application/json" if text.lstrip().startswith(("{", "[")) else "text/html"
                return httpx.Response(200, text=text, headers={"content-type": kind})
        return httpx.Response(599, text=f"unexpected request {request.url}")

    return httpx.MockTransport(handler)
