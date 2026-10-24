"""Adding a category from the UI: map it on every site, preview the rules, save it reviewed.

The Conad code is known (the own taxonomy is the Conad tree). The Coop category is chosen from
the site's own list. Lidl and Eurospin only have coarse categories, so the title rules decide;
the preview applies them to the pages already saved by the last run (no request), except an
Eurospin category never downloaded yet, which costs one request when the user asks for it."""

from collections import Counter
from dataclasses import dataclass

from ruamel.yaml.comments import CommentedMap, CommentedSeq
from sqlalchemy import select

from pisaprices.config.files import update_file
from pisaprices.domain.enums import RunStatus, Source
from pisaprices.ingest.categories import title_matches
from pisaprices.ingest.http import PoliteClient
from pisaprices.ingest.snapshots import load_snapshots
from pisaprices.ingest.sources.eurospin_offers import LISTING, OFFER_TYPES
from pisaprices.services.clock import iso_utc
from pisaprices.services.collect import Runtime
from pisaprices.services.sources import build_collector
from pisaprices.storage.orm import ScrapeRun
from pisaprices.storage.repos import site_categories as repo

LISTED_SOURCES = (Source.COOP_CATALOG, Source.CONAD_CATALOG, Source.EUROSPIN_OFFERS)


@dataclass
class CategoryDraft:
    slug: str
    name: str
    selected: bool
    reviewed: bool
    sources: dict[str, list[str]]
    include: list[str]
    exclude: list[str]


def draft(runtime: Runtime, slug: str) -> CategoryDraft:
    category = next((c for c in runtime.config.categories.categories if c.slug == slug), None)
    if category is None:
        raise ValueError(f"unknown category '{slug}'")
    rules = category.title_rules
    return CategoryDraft(slug, category.name, category.selected, not category.review,
                         {s.value: list(keys) for s, keys in category.sources.items()},
                         list(rules.include) if rules else [], list(rules.exclude) if rules else [])


def _client(runtime: Runtime, collector, source: str) -> PoliteClient:
    return PoliteClient(
        source=source, run_id=0, raw_root=runtime.raw_root, http=runtime.config.settings.http,
        allowed_hosts=collector.allowed_hosts, forbidden_params=collector.forbidden_params,
        forbidden_paths=collector.forbidden_paths, cookies=collector.cookies(), transport=runtime.transport,
        now=lambda: iso_utc(runtime.clock.now()), **({"sleep": runtime.sleep} if runtime.sleep else {}),
    )


def refresh_site_categories(runtime: Runtime) -> dict[str, int | str]:
    """One request per site: the category lists offered today, stored for the setup screen."""
    result: dict[str, int | str] = {}
    for source in LISTED_SOURCES:
        try:
            collector = build_collector(source, runtime.config, runtime.root)
            with _client(runtime, collector, "category_setup") as client:
                client.phase = "context"
                offered = collector.site_categories(client)
        except Exception as exc:   # e.g. Conad session expired: the other sites still refresh
            result[source.value] = f"{type(exc).__name__}: {exc}"
            continue
        with runtime.sessions.begin() as session:
            result[source.value] = repo.upsert(session, source.value, offered, iso_utc(runtime.clock.now()))
    return result


def site_categories(runtime: Runtime, source: str) -> list[dict]:
    with runtime.sessions() as session:
        return repo.listed(session, source)


def _last_run_snapshots(runtime: Runtime, source: Source):
    with runtime.sessions() as session:
        run_id = session.scalar(select(ScrapeRun.id).where(
            ScrapeRun.source == source.value, ScrapeRun.status.in_([RunStatus.OK.value, RunStatus.PARTIAL.value]))
            .order_by(ScrapeRun.id.desc()).limit(1))
    if run_id is None:
        return []
    try:
        return load_snapshots(runtime.raw_root, source.value, run_id)
    except FileNotFoundError:
        return []


def lidl_coarse_categories(runtime: Runtime) -> list[tuple[str, int]]:
    """Coarse Lidl categories seen in the last run's pages, with how many products each had."""
    collector = build_collector(Source.LIDL_OFFERS, runtime.config, runtime.root)
    seen = {pair for snap in _last_run_snapshots(runtime, Source.LIDL_OFFERS) for pair in collector.promo_titles(snap)}
    counts = Counter(coarse for coarse, _ in seen)   # a product can be on several weekly pages
    return sorted(counts.items())


@dataclass
class PreviewLine:
    title: str
    coarse: str
    included: bool


def preview(runtime: Runtime, source: str, coarse_keys: list[str], include: list[str], exclude: list[str],
            allow_fetch: bool = False) -> tuple[list[PreviewLine], list[str]]:
    """Products of the coarse categories and whether the rules take them. Returns lines and the
    coarse keys that could not be previewed (never downloaded; pass allow_fetch for one request)."""
    src = Source(source)
    collector = build_collector(src, runtime.config, runtime.root)
    titles: list[tuple[str, str]] = []
    for snap in _last_run_snapshots(runtime, src):
        titles += collector.promo_titles(snap)
    wanted = [k for k in coarse_keys if k]
    seen = {coarse for coarse, _ in titles}
    missing = [k for k in wanted if not any(c == k or c.startswith(k + "/") for c in seen)]
    if missing and allow_fetch and src is Source.EUROSPIN_OFFERS:
        with _client(runtime, collector, "category_setup") as client:
            for key in missing:
                params = [("by_category", key), *(("by_typeOffer", t) for t in OFFER_TYPES)]
                _, snap = client.get(LISTING, f"preview {key}", params=params, context={"key": key})
                titles += collector.promo_titles(snap)
        missing = []
    lines = [PreviewLine(title, coarse, title_matches(include, exclude, title))
             for coarse, title in titles if any(coarse == k or coarse.startswith(k + "/") for k in wanted)]
    unique = {(line.title, line.coarse): line for line in lines}
    return sorted(unique.values(), key=lambda l: (not l.included, l.title.lower())), missing


def _flow(items: list[str]) -> CommentedSeq:
    seq = CommentedSeq(items)
    seq.fa.set_flow_style()
    return seq


def save(runtime: Runtime, slug: str, *, coop: list[str], lidl: list[str], eurospin: list[str],
         include: list[str], exclude: list[str], select_it: bool = True) -> None:
    """Store the mapping as reviewed (and selected). The whole configuration is validated first."""

    def mutate(document: CommentedMap) -> None:
        for category in document["categories"]:
            if category["slug"] != slug:
                continue
            sources = category.setdefault("sources", CommentedMap())
            sources["coop_catalog"] = _flow(coop)
            sources["lidl_offers"] = _flow(lidl)
            sources["eurospin_offers"] = _flow(eurospin)
            rules = CommentedMap()
            rules["include"] = _flow([w.strip() for w in include if w.strip()])
            rules["exclude"] = _flow([w.strip() for w in exclude if w.strip()])
            category["title_rules"] = rules
            category["review"] = False
            category["selected"] = select_it
            return
        raise ValueError(f"unknown category '{slug}'")

    update_file(runtime.root / "config", "categories", mutate)
