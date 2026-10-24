"""Running several sources: each one independently, then linking promotions to products."""

from dataclasses import dataclass

from loguru import logger

from pisaprices.domain.enums import RunStatus, Source
from pisaprices.services.collect import RunResult, Runtime, collect, reparse
from pisaprices.services.classify import classify_products, place_open_products
from pisaprices.services.review import ResolveReport, resolve_pending
from pisaprices.services.sources import build_collector

# catalogs first, so promotions from the other sources can link to products already stored
ORDER = [Source.COOP_CATALOG, Source.COOP_SOCI, Source.CONAD_CATALOG, Source.LIDL_EVERLI, Source.LIDL_OFFERS,
         Source.EUROSPIN_OFFERS]


@dataclass
class CollectReport:
    runs: list[RunResult]
    resolve: ResolveReport

    @property
    def ok(self) -> bool:
        return all(run.status is not RunStatus.FAILED for run in self.runs)


def collect_sources(runtime: Runtime, sources: list[str] | None = None) -> CollectReport:
    """`sources` are source names (e.g. "coop_catalog"); None = all, in ORDER."""
    results = []
    for source in [Source(name) for name in sources] if sources else ORDER:
        try:
            collector = build_collector(source, runtime.config, runtime.root)
        except (ValueError, FileNotFoundError) as exc:   # e.g. missing Conad session file
            logger.warning("{}: not collected: {}", source.value, exc)
            results.append(RunResult(run_id=0, source=source.value, status=RunStatus.FAILED, error=str(exc)))
            continue
        results.append(collect(runtime, collector))
    place_open_products(runtime)
    classify_products(runtime)
    return CollectReport(results, resolve_pending(runtime))


def reparse_run(runtime: Runtime, source: str, run_id: int) -> CollectReport:
    result = reparse(runtime, build_collector(Source(source), runtime.config, runtime.root), run_id)
    place_open_products(runtime)
    classify_products(runtime)
    return CollectReport([result], resolve_pending(runtime))
