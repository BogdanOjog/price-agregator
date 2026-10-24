"""Which collector serves which source, built from the configuration."""

from pathlib import Path

from pisaprices.config.schemas import AppConfig, StoreConfig
from pisaprices.domain.enums import Chain, Source
from pisaprices.ingest.base import Collector
from pisaprices.ingest.categories import CategoryIndex


def _store(config: AppConfig, chain: Chain, *, visitable: bool | None = None) -> StoreConfig:
    stores = [s for s in config.stores.stores if s.chain is chain and (visitable is None or s.visitable == visitable)]
    if len(stores) != 1:
        raise ValueError(f"expected exactly one {chain.value} store (visitable={visitable}) in stores.yaml, found {len(stores)}")
    return stores[0]


def build_collector(source: Source, config: AppConfig, root: Path) -> Collector:
    categories = CategoryIndex(config)
    tolerance = config.settings.quality.unit_price_tolerance
    if source is Source.COOP_CATALOG:
        from pisaprices.ingest.sources.coop_catalog import CoopCatalog
        return CoopCatalog(_store(config, Chain.COOP, visitable=False), categories, tolerance)
    if source is Source.COOP_SOCI:
        from pisaprices.ingest.sources.coop_soci import CoopSoci
        return CoopSoci(_store(config, Chain.COOP, visitable=True))
    if source is Source.CONAD_CATALOG:
        from pisaprices.ingest.sources.conad_catalog import ConadCatalog
        return ConadCatalog(_store(config, Chain.CONAD), categories, tolerance, root / "data" / "conad_session.txt")
    if source is Source.LIDL_OFFERS:
        from pisaprices.ingest.sources.lidl_offers import LidlOffers
        return LidlOffers(_store(config, Chain.LIDL), categories)
    if source is Source.EUROSPIN_OFFERS:
        from pisaprices.ingest.sources.eurospin_offers import EurospinOffers
        return EurospinOffers(_store(config, Chain.EUROSPIN), categories)
    raise ValueError(f"no collector for {source}")
