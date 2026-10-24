"""The contract every collector implements (one collector per source)."""

from typing import Protocol

from pisaprices.domain.enums import Chain, Source
from pisaprices.ingest.http import PoliteClient
from pisaprices.ingest.records import ParsedBatch
from pisaprices.ingest.snapshots import RawSnapshot


class ContextError(Exception):
    """The site is not in the expected state (wrong store selected, session expired, page layout
    changed). The run stops; the message tells the user what to do."""


class Collector(Protocol):
    source: Source
    chain: Chain
    allowed_hosts: frozenset[str]
    forbidden_params: tuple[str, ...]   # query parameter prefixes forbidden by robots.txt
    forbidden_paths: tuple[str, ...]    # path prefixes forbidden by robots.txt

    def cookies(self) -> dict[str, str] | None:
        """Session cookies the source needs before the first request (Conad), else None."""

    def verify_context(self, client: PoliteClient) -> None:
        """Put the session in the right state and check it (e.g. the selected store). Raises ContextError."""

    def fetch(self, client: PoliteClient, source_keys: list[str]) -> None:
        """Download what is needed for the selected categories (the client keeps the snapshots). Never parses."""

    def parse(self, snapshot: RawSnapshot) -> ParsedBatch:
        """Turn one snapshot into records. Pure: no network, no database."""

    def site_categories(self, client: PoliteClient) -> dict[str, str]:
        """Category key -> name as the site offers them today (categories check, category setup)."""
