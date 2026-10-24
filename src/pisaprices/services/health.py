"""Health checks: `pisaprices doctor` and `pisaprices categories check`."""

from dataclasses import dataclass, field
from datetime import datetime

from pisaprices.domain.enums import Source
from pisaprices.ingest.http import PoliteClient
from pisaprices.services.clock import iso_utc
from pisaprices.services.collect import Runtime
from pisaprices.services.conad_session import check_session
from pisaprices.services.runs import ORDER
from pisaprices.services.sources import build_collector
from pisaprices.storage.repos import health as repo

STALE_DAYS = 8   # weekly offers: older than a week and a day means a run was missed


@dataclass
class SourceHealth:
    source: str
    last_run: dict | None
    age_days: float | None
    problems: list[str] = field(default_factory=list)


@dataclass
class DoctorReport:
    sources: list[SourceHealth]
    open_reviews: dict[str, int]
    conad_session: tuple[bool, str] | None

    @property
    def ok(self) -> bool:
        return not any(s.problems for s in self.sources) and (self.conad_session is None or self.conad_session[0])


def doctor(runtime: Runtime, check_conad: bool = True) -> DoctorReport:
    now = runtime.clock.now()
    with runtime.sessions() as session:
        runs = {source.value: repo.last_run(session, source.value) for source in ORDER}
        reviews = repo.open_review_counts(session)
    sources = []
    for name, run in runs.items():
        health = SourceHealth(name, run, None)
        if run is None:
            health.problems.append("never collected")
        else:
            started = datetime.fromisoformat(run["started_at"].replace("Z", "+00:00"))
            health.age_days = round((now - started).total_seconds() / 86400, 1)
            if run["status"] == "failed":
                health.problems.append(f"last run failed: {run['error']}")
            elif run["status"] == "partial":
                health.problems.append(f"last run partial: {run['error']}")
            if health.age_days > STALE_DAYS:
                health.problems.append(f"data is {health.age_days} days old")
        sources.append(health)
    conad = check_session(runtime) if check_conad else None
    return DoctorReport(sources, reviews, conad)


@dataclass
class CategoryCheck:
    source: str
    missing: list[str]          # mapped by a selected category, gone from the site
    unmapped: list[str]         # offered by the site (coarse sources), mapped nowhere
    error: str | None = None


def check_categories(runtime: Runtime) -> list[CategoryCheck]:
    """Compare the keys of the selected categories with what each site offers today."""
    results = []
    for source in ORDER:
        mapped = {key for c in runtime.config.selected_categories() for key in c.sources.get(source, [])}
        try:
            collector = build_collector(source, runtime.config, runtime.root)
        except (ValueError, FileNotFoundError) as exc:
            results.append(CategoryCheck(source.value, [], [], str(exc)))
            continue
        client = PoliteClient(
            source="category_check", run_id=0, raw_root=runtime.raw_root, http=runtime.config.settings.http,
            allowed_hosts=collector.allowed_hosts, forbidden_params=collector.forbidden_params,
            forbidden_paths=collector.forbidden_paths, cookies=collector.cookies(), transport=runtime.transport,
            now=lambda: iso_utc(runtime.clock.now()), **({"sleep": runtime.sleep} if runtime.sleep else {}),
        )
        try:
            with client:
                client.phase = "context"
                offered = collector.site_categories(client)
        except Exception as exc:   # a check reports every failure (ContextError, network, layout), never crashes
            results.append(CategoryCheck(source.value, [], [], f"{type(exc).__name__}: {exc}"))
            continue
        if source in (Source.LIDL_OFFERS, Source.COOP_SOCI) or not offered:
            results.append(CategoryCheck(source.value, [], []))
            continue
        missing = sorted(key for key in mapped if key not in offered and not any(o.startswith(key + "/") for o in offered))
        unmapped = sorted(set(offered) - mapped) if source is Source.EUROSPIN_OFFERS else []
        results.append(CategoryCheck(source.value, missing, unmapped))
    return results
