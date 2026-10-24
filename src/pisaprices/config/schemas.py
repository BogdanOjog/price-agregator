"""Pydantic models of the files in config/. Each model validates one file; AppConfig checks the
references between files (store slugs, category parents, price sources)."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from pisaprices.domain.enums import Chain, Program, Source, SourceMode, Transport, Weekday

PISA_CAPS = range(56121, 56129)

Slug = Annotated[str, StringConstraints(pattern=r"^[a-z0-9_]+$")]
Positive = Annotated[float, Field(gt=0)]
NonNegative = Annotated[float, Field(ge=0)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# settings.yaml ---------------------------------------------------------------------------------

class HttpSettings(Strict):
    min_delay_seconds: Positive
    timeout_seconds: Positive
    max_retries: Annotated[int, Field(ge=0)]
    user_agent: Annotated[str, StringConstraints(min_length=1)]


class QualitySettings(Strict):
    unit_price_tolerance: NonNegative
    anomaly_drop_ratio: Annotated[float, Field(gt=0, le=1)]
    fuzzy_match_threshold: Annotated[float, Field(ge=0, le=100)]


class OptimizerSettings(Strict):
    planning_window_days: Annotated[int, Field(ge=1)]
    max_stores_k: Annotated[int, Field(ge=1)]
    max_price_age_days: Annotated[int, Field(ge=1)]
    expiry_warning_days: Annotated[int, Field(ge=0)]
    future_rewards_value: NonNegative


class PathSettings(Strict):
    raw: str
    db: str


class Settings(Strict):
    http: HttpSettings
    quality: QualitySettings
    optimizer: OptimizerSettings
    paths: PathSettings


# stores.yaml -----------------------------------------------------------------------------------

class StoreConfig(Strict):
    slug: Slug
    chain: Chain
    name: str
    address: str | None = None
    cap: Annotated[str, StringConstraints(pattern=r"^\d{5}$")]
    external_id: str | None = None
    source_mode: SourceMode
    visitable: bool = True
    price_source: Slug | None = None
    outside_scope_approved: bool = False

    @model_validator(mode="after")
    def _in_pisa(self) -> "StoreConfig":
        if int(self.cap) not in PISA_CAPS and not self.outside_scope_approved:
            raise ValueError(f"CAP {self.cap} is outside Comune di Pisa (56121-56128) and not approved")
        if self.outside_scope_approved and self.visitable:
            raise ValueError("a store outside Pisa may only be a data source (visitable: false)")
        return self


class StoresFile(Strict):
    stores: list[StoreConfig]

    @model_validator(mode="after")
    def _references(self) -> "StoresFile":
        by_slug = {}
        for store in self.stores:
            if store.slug in by_slug:
                raise ValueError(f"duplicate store slug '{store.slug}'")
            by_slug[store.slug] = store
        for store in self.stores:
            if store.price_source is None:
                continue
            source = by_slug.get(store.price_source)
            if source is None:
                raise ValueError(f"store '{store.slug}': price_source '{store.price_source}' does not exist")
            if source.slug == store.slug or source.chain != store.chain:
                raise ValueError(f"store '{store.slug}': price_source must be another store of the same chain")
        return self


# user.yaml -------------------------------------------------------------------------------------

class ProgramConfig(Strict):
    has_card: bool
    n_cards: Annotated[int, Field(ge=1)] = 1


class UserStoreConfig(Strict):
    travel_minutes_one_way: NonNegative
    transport: Transport
    transport_cost_eur: NonNegative | None = None
    allowed_days: list[Weekday] | None = None   # None = any day
    on_route_days: list[Weekday] = []
    detour_minutes: NonNegative = 0

    @model_validator(mode="after")
    def _ticket(self) -> "UserStoreConfig":
        if self.transport is Transport.BUS and self.transport_cost_eur is None:
            raise ValueError("transport is bus: fill in transport_cost_eur (round-trip ticket)")
        return self

    @field_validator("on_route_days", mode="before")
    @classmethod
    def _none_is_empty(cls, value):
        return [] if value is None else value


class UserConfig(Strict):
    programs: dict[Program, ProgramConfig]
    stores: dict[Slug, UserStoreConfig]


# categories.yaml -------------------------------------------------------------------------------

class TitleRules(Strict):
    include: list[str] = []
    exclude: list[str] = []


class CategoryConfig(Strict):
    slug: Slug
    name: str
    parent: Slug | None = None
    selected: bool = False
    review: bool = True
    sources: dict[Source, list[str]] = {}
    title_rules: TitleRules | None = None

    @model_validator(mode="after")
    def _selected_is_reviewed(self) -> "CategoryConfig":
        if self.selected and self.review:
            raise ValueError(f"category '{self.slug}' is selected but its mapping is unreviewed (review: true)")
        return self


class CategoriesFile(Strict):
    categories: list[CategoryConfig]

    @model_validator(mode="after")
    def _tree(self) -> "CategoriesFile":
        by_slug = {}
        for category in self.categories:
            if category.slug in by_slug:
                raise ValueError(f"duplicate category slug '{category.slug}'")
            by_slug[category.slug] = category
        for category in self.categories:
            seen = {category.slug}
            parent = category.parent
            while parent is not None:
                if parent not in by_slug:
                    raise ValueError(f"category '{category.slug}': parent '{parent}' does not exist")
                if parent in seen:
                    raise ValueError(f"category '{category.slug}': parent cycle through '{parent}'")
                seen.add(parent)
                parent = by_slug[parent].parent
        return self


# all files together ----------------------------------------------------------------------------

class AppConfig(Strict):
    settings: Settings
    stores: StoresFile
    user: UserConfig
    categories: CategoriesFile

    @model_validator(mode="after")
    def _user_stores(self) -> "AppConfig":
        stores = {s.slug: s for s in self.stores.stores}
        for slug in self.user.stores:
            if slug not in stores:
                raise ValueError(f"user.yaml: store '{slug}' is not in stores.yaml")
            if not stores[slug].visitable:
                raise ValueError(f"user.yaml: store '{slug}' is a data source only and cannot be visited")
        missing = [s.slug for s in self.stores.stores if s.visitable and s.slug not in self.user.stores]
        if missing:
            raise ValueError(f"user.yaml: travel data missing for visitable stores {missing}")
        return self

    def selected_categories(self) -> list[CategoryConfig]:
        return [c for c in self.categories.categories if c.selected]
