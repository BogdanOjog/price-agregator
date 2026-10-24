"""Collection runs: collect (network) and reparse (snapshots only), logged in scrape_runs.

A run is all-or-nothing in the database: if one snapshot fails to parse, nothing from the run is
stored, the run is `failed` with the reason, and the snapshots stay on disk for `reparse`."""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from loguru import logger
from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from pisaprices.config.schemas import AppConfig
from pisaprices.domain.enums import Mechanic, ReviewKind, RunStatus, Source
from pisaprices.ingest.base import Collector, ContextError
from pisaprices.ingest.categories import CATALOG_SOURCES
from pisaprices.ingest.http import PoliteClient, RequestRefused
from pisaprices.ingest.records import ParsedBatch
from pisaprices.ingest.snapshots import RawSnapshot, load_snapshots
from pisaprices.services.clock import Clock, iso_utc
from pisaprices.storage.repos import ingest as repo
from pisaprices.storage.repos import review as review_repo

class ParseError(Exception):
    """A snapshot could not be turned into valid records."""


EXPECTED_FAILURES = (ContextError, RequestRefused, httpx.HTTPError, ValidationError, ParseError)


@dataclass
class Runtime:
    config: AppConfig
    root: Path                                  # project root; settings paths are relative to it
    sessions: sessionmaker[Session]
    clock: Clock
    transport: httpx.BaseTransport | None = None   # tests inject a mock transport
    sleep: Callable[[float], None] | None = None

    @property
    def raw_root(self) -> Path:
        return self.root / self.config.settings.paths.raw


@dataclass
class RunResult:
    run_id: int
    source: str
    status: RunStatus
    items: int = 0
    requests: int = 0
    error: str | None = None
    warnings: list[str] = field(default_factory=list)


def source_keys(config: AppConfig, source: Source) -> list[str]:
    """Category keys of `source` across all selected categories (order kept, no duplicates). For the
    offer sources, the keys of their groups too; a catalog never fetches a whole group's page."""
    by_slug = {c.slug: c for c in config.categories.categories}
    keys: dict[str, None] = {}
    for category in config.selected_categories():
        owner = category
        while owner is not None:
            for key in owner.sources.get(source, []):
                keys[key] = None
            owner = by_slug.get(owner.parent) if owner.parent and source not in CATALOG_SOURCES else None
    return list(keys)


def _parse_all(collector: Collector, snapshots: list[RawSnapshot]) -> list[ParsedBatch]:
    batches = []
    for snapshot in snapshots:
        try:
            batches.append(collector.parse(snapshot))
        except Exception as exc:
            raise ParseError(f"cannot parse snapshot {snapshot.path.name} ({snapshot.url}): {exc}") from exc
    return batches


def _persist(session: Session, run_id: int, batches: list[ParsedBatch], now: str) -> None:
    stores, categories = repo.ids_by_slug(session)

    def store_id(slug: str | None) -> int | None:
        if slug is None:
            return None
        if slug not in stores:
            raise ParseError(f"store '{slug}' is not in stores.yaml (run `pisaprices init`?)")
        return stores[slug]

    priced: set[tuple[int, str]] = set()
    for batch in batches:
        for p in batch.products:
            fields = p.model_dump(exclude={"chain", "external_id", "source_category_key", "category_slug"})
            fields["unit"] = p.unit.value if p.unit else None
            if p.category_slug:
                fields["category_id"] = categories.get(p.category_slug)
            # else: keep the category decided earlier from similar products or in review
            repo.upsert_product(session, p.chain.value, p.external_id, fields)
        for price in batch.prices:
            product = repo.product_id(session, price.chain.value, price.external_id)
            if product is None:
                raise ParseError(f"price for unknown product {price.chain}:{price.external_id}")
            if (product, price.store_slug) in priced:   # same product listed in two categories or pages
                continue
            priced.add((product, price.store_slug))
            repo.add_price(
                session, product_id=product, store_id=store_id(price.store_slug), run_id=run_id,
                observed_at=price.observed_at, price_cents=price.price_cents, unit_price=price.unit_price,
                needs_review=price.needs_review, source=price.source.value,
            )
            if price.needs_review:
                review_repo.add_review(
                    session, ReviewKind.UNIT_PRICE_MISMATCH, "products", product,
                    key=f"{price.chain}:{price.external_id}:{price.store_slug}",
                    payload={"price_cents": price.price_cents, "unit_price": price.unit_price}, created_at=now,
                )
        for promo in batch.promotions:
            fields = promo.model_dump(mode="json", exclude={"store_slug", "items", "observed_at"})
            fields["store_id"] = store_id(promo.store_slug)
            promotion = repo.upsert_promotion(session, promo.natural_key(), fields)
            repo.add_sighting(session, promotion, run_id, promo.observed_at)
            for ref in promo.items:
                linked = repo.product_id(session, ref.chain.value, ref.external_id) if ref.external_id else None
                repo.add_promotion_item(session, promotion, ref.key(), ref.model_dump(mode="json"), linked)
            if promo.needs_review:
                kind = (ReviewKind.TIERED_INCREMENTAL if promo.mechanic is Mechanic.TIERED_INCREMENTAL
                        else ReviewKind.UNKNOWN_PROMO)
                review_repo.add_review(session, kind, "promotions", promotion, key=promo.natural_key(),
                                       payload={"raw_text": promo.raw_text}, created_at=now)
        for review in batch.reviews:
            review_repo.add_review(session, review.kind, "source", 0, key=review.key, payload=review.payload, created_at=now)


def _judge(session: Session, source: str, run_id: int, items: int, ratio: float) -> tuple[RunStatus, list[str]]:
    if items == 0:
        return RunStatus.PARTIAL, ["no products parsed"]
    previous = repo.previous_items(session, source, run_id)
    if previous and items < ratio * previous:
        return RunStatus.PARTIAL, [f"only {items} products, previous run had {previous} (anomaly_drop_ratio {ratio})"]
    return RunStatus.OK, []


def _store(runtime: Runtime, source: str, run_id: int, batches: list[ParsedBatch], replace: bool) -> tuple[RunStatus, int, list[str]]:
    items = sum(batch.items for batch in batches)
    with runtime.sessions.begin() as session:
        if replace:
            repo.delete_run_rows(session, run_id)
        _persist(session, run_id, batches, iso_utc(runtime.clock.now()))
        status, warnings = _judge(session, source, run_id, items, runtime.config.settings.quality.anomaly_drop_ratio)
    return status, items, warnings


def _finish(runtime: Runtime, result: RunResult) -> RunResult:
    message = "; ".join(filter(None, [result.error, *result.warnings])) or None
    with runtime.sessions.begin() as session:
        repo.finish_run(session, result.run_id, status=result.status, finished_at=iso_utc(runtime.clock.now()),
                        items=result.items, requests=result.requests or None, error=message)
    log = logger.warning if result.status is not RunStatus.OK else logger.info
    log("{} run {}: {} ({} items, {} requests){}", result.source, result.run_id, result.status.value,
        result.items, result.requests, f" — {message}" if message else "")
    return result


def collect(runtime: Runtime, collector: Collector) -> RunResult:
    source = collector.source.value
    with runtime.sessions.begin() as session:
        run_id = repo.start_run(session, source, iso_utc(runtime.clock.now()))
    result = RunResult(run_id=run_id, source=source, status=RunStatus.FAILED)

    client = PoliteClient(
        source=source, run_id=run_id, raw_root=runtime.raw_root, http=runtime.config.settings.http,
        allowed_hosts=collector.allowed_hosts, forbidden_params=collector.forbidden_params,
        forbidden_paths=collector.forbidden_paths, cookies=collector.cookies(), transport=runtime.transport,
        now=lambda: iso_utc(runtime.clock.now()), **({"sleep": runtime.sleep} if runtime.sleep else {}),
    )
    try:
        with client:
            client.phase = "context"
            collector.verify_context(client)
            client.phase = "data"
            collector.fetch(client, source_keys(runtime.config, collector.source))
        # the same snapshots reparse will read back: only the data phase
        batches = _parse_all(collector, [s for s in client.snapshots if s.phase == "data"])
        result.status, result.items, result.warnings = _store(runtime, source, run_id, batches, replace=False)
    except EXPECTED_FAILURES as exc:
        result.error = f"{type(exc).__name__}: {exc}"
    except Exception as exc:  # unexpected: still recorded, never silent
        logger.exception("{} run {} crashed", source, run_id)
        result.error = f"unexpected {type(exc).__name__}: {exc}"
    finally:
        result.requests = client.requests
    return _finish(runtime, result)


def reparse(runtime: Runtime, collector: Collector, run_id: int) -> RunResult:
    """Rebuild a run's rows from its snapshots. No network: no client is ever created here."""
    source = collector.source.value
    with runtime.sessions.begin() as session:
        info = repo.run_info(session, run_id)
    if info is None or info["source"] != source:
        raise ValueError(f"run {run_id} of source {source} does not exist")
    result = RunResult(run_id=run_id, source=source, status=RunStatus.FAILED)
    try:
        batches = _parse_all(collector, load_snapshots(runtime.raw_root, source, run_id))
        result.status, result.items, result.warnings = _store(runtime, source, run_id, batches, replace=True)
    except (*EXPECTED_FAILURES, FileNotFoundError) as exc:
        result.error = f"{type(exc).__name__}: {exc}"
    return _finish(runtime, result)
