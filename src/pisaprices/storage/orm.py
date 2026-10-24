"""SQLAlchemy models: the source of truth for the schema (Alembic migrations follow them).

Money is stored in integer cents. Validity dates are ISO dates (Europe/Rome, both ends
included); timestamps are ISO UTC strings. FTS5, its triggers and the current_prices view are
created by the migration, not by these models."""

from enum import StrEnum

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from pisaprices.domain.enums import (
    Chain,
    Channel,
    MatchMode,
    Mechanic,
    ObservationSource,
    Program,
    ReviewKind,
    ReviewStatus,
    RunStatus,
    SourceMode,
    Unit,
)

PISA_CAP_MIN = "56121"
PISA_CAP_MAX = "56128"


def one_of(column: str, values: type[StrEnum], nullable: bool = False) -> str:
    allowed = ", ".join(f"'{v.value}'" for v in values)
    check = f"{column} IN ({allowed})"
    return f"{column} IS NULL OR {check}" if nullable else check


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_name)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


class Store(Base):
    """Synced from config/stores.yaml. Personal values (minutes, days, tickets) stay in user.yaml."""

    __tablename__ = "stores"
    __table_args__ = (
        CheckConstraint(one_of("chain", Chain), name="chain"),
        CheckConstraint(one_of("source_mode", SourceMode), name="source_mode"),
        CheckConstraint(
            f"(cap BETWEEN '{PISA_CAP_MIN}' AND '{PISA_CAP_MAX}') OR outside_scope_approved = 1",
            name="cap_in_pisa",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String, unique=True)
    chain: Mapped[str] = mapped_column(String)
    name: Mapped[str] = mapped_column(String)
    address: Mapped[str | None] = mapped_column(String)
    external_id: Mapped[str | None] = mapped_column(String)
    cap: Mapped[str] = mapped_column(String)
    outside_scope_approved: Mapped[bool] = mapped_column(Boolean, default=False)
    source_mode: Mapped[str] = mapped_column(String)
    visitable: Mapped[bool] = mapped_column(Boolean, default=True)
    price_source_store_id: Mapped[int | None] = mapped_column(ForeignKey("stores.id"))


class Category(Base):
    """Synced from config/categories.yaml. `selected` stays in the YAML."""

    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String, unique=True)
    name: Mapped[str] = mapped_column(String)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"))


class SourceCategory(Base):
    """A category as the site names it. The mapping to own categories lives in categories.yaml:
    a coarse site category (Eurospin "Dispensa") can feed several own categories."""

    __tablename__ = "source_categories"
    __table_args__ = (UniqueConstraint("source", "source_key", name="source_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String)
    source_key: Mapped[str] = mapped_column(String)
    name: Mapped[str | None] = mapped_column(String)
    last_fetched_at: Mapped[str | None] = mapped_column(String)


class CanonicalProduct(Base):
    __tablename__ = "canonical_products"
    __table_args__ = (CheckConstraint("tier IS NULL OR tier IN (1, 2, 3)", name="tier"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"))
    format: Mapped[str | None] = mapped_column(String)
    tier: Mapped[int | None] = mapped_column(Integer)
    flags: Mapped[list | None] = mapped_column(JSON)


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("chain", "external_id", name="chain_external_id"),
        CheckConstraint(one_of("chain", Chain), name="chain"),
        CheckConstraint(one_of("unit", Unit, nullable=True), name="unit"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    chain: Mapped[str] = mapped_column(String)
    external_id: Mapped[str] = mapped_column(String)
    ean: Mapped[str | None] = mapped_column(String, index=True)
    name: Mapped[str] = mapped_column(String)
    brand: Mapped[str | None] = mapped_column(String)
    net_qty: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String)
    variable_weight: Mapped[bool] = mapped_column(Boolean, default=False)
    source_category_id: Mapped[int | None] = mapped_column(ForeignKey("source_categories.id"))
    # own category, assigned at ingest from categories.yaml (source mapping + title_rules)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"), index=True)
    canonical_id: Mapped[int | None] = mapped_column(ForeignKey("canonical_products.id"))
    successor_of: Mapped[int | None] = mapped_column(ForeignKey("products.id"))
    product_url: Mapped[str | None] = mapped_column(String)
    image_url: Mapped[str | None] = mapped_column(String)


class ScrapeRun(Base):
    __tablename__ = "scrape_runs"
    __table_args__ = (CheckConstraint(one_of("status", RunStatus), name="status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String)
    started_at: Mapped[str] = mapped_column(String)
    finished_at: Mapped[str | None] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default=RunStatus.RUNNING.value)
    items: Mapped[int | None] = mapped_column(Integer)
    requests: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)


class PriceObservation(Base):
    """The regular price seen; every deviation from it lives in `promotions`. Append-only."""

    __tablename__ = "price_observations"
    __table_args__ = (
        UniqueConstraint("product_id", "store_id", "run_id", name="product_store_run"),
        CheckConstraint(one_of("source", ObservationSource), name="source"),
        CheckConstraint("price_cents >= 0", name="price_non_negative"),
        Index("ix_obs_product_store", "product_id", "store_id", "observed_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"))
    run_id: Mapped[int] = mapped_column(ForeignKey("scrape_runs.id"))
    observed_at: Mapped[str] = mapped_column(String)
    price_cents: Mapped[int] = mapped_column(Integer)
    unit_price: Mapped[float | None] = mapped_column(Float)  # €/kg, €/l or €/pz, display only
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String)


class Promotion(Base):
    """One unique promotion (natural_key); each run that sees it adds a PromotionSighting."""

    __tablename__ = "promotions"
    __table_args__ = (
        CheckConstraint(one_of("chain", Chain), name="chain"),
        CheckConstraint(one_of("mechanic", Mechanic), name="mechanic"),
        CheckConstraint(one_of("program", Program, nullable=True), name="program"),
        CheckConstraint(one_of("channel", Channel), name="channel"),
        CheckConstraint("valid_from IS NULL OR valid_until IS NULL OR valid_from <= valid_until", name="validity"),
        Index("ix_promo_validity", "chain", "store_id", "valid_from", "valid_until"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    natural_key: Mapped[str] = mapped_column(String, unique=True)
    chain: Mapped[str] = mapped_column(String)
    store_id: Mapped[int | None] = mapped_column(ForeignKey("stores.id"))  # NULL = chain-wide
    mechanic: Mapped[str] = mapped_column(String)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    # modifiers
    program: Mapped[str | None] = mapped_column(String)
    channel: Mapped[str] = mapped_column(String, default=Channel.STORE.value)
    min_qty: Mapped[int | None] = mapped_column(Integer)
    max_qty_per_card: Mapped[int | None] = mapped_column(Integer)
    max_qty_per_receipt: Mapped[int | None] = mapped_column(Integer)
    is_sottocosto: Mapped[bool] = mapped_column(Boolean, default=False)
    stock_qty: Mapped[int | None] = mapped_column(Integer)
    requires_activation: Mapped[bool] = mapped_column(Boolean, default=False)
    stackable: Mapped[bool] = mapped_column(Boolean, default=False)
    excludes_already_promoted: Mapped[bool] = mapped_column(Boolean, default=False)
    valid_from: Mapped[str | None] = mapped_column(String)
    valid_until: Mapped[str | None] = mapped_column(String)
    prior_price_cents: Mapped[int | None] = mapped_column(Integer)
    raw_text: Mapped[str] = mapped_column(Text)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)


class PromotionSighting(Base):
    __tablename__ = "promotion_sightings"

    promotion_id: Mapped[int] = mapped_column(ForeignKey("promotions.id"), primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("scrape_runs.id"), primary_key=True)
    seen_at: Mapped[str] = mapped_column(String)


class PromotionItem(Base):
    """A product covered by a promotion. product_id stays NULL until `resolve` links the reference."""

    __tablename__ = "promotion_items"

    promotion_id: Mapped[int] = mapped_column(ForeignKey("promotions.id"), primary_key=True)
    ref_key: Mapped[str] = mapped_column(String, primary_key=True)
    ref: Mapped[dict] = mapped_column(JSON)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"), index=True)


class ReviewItem(Base):
    __tablename__ = "review_items"
    __table_args__ = (
        CheckConstraint(one_of("kind", ReviewKind), name="kind"),
        CheckConstraint(one_of("status", ReviewStatus), name="status"),
        Index("ix_review_open", "status", "kind"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String)
    subject_table: Mapped[str] = mapped_column(String)
    subject_id: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String, default=ReviewStatus.OPEN.value)
    created_at: Mapped[str] = mapped_column(String)
    decided_at: Mapped[str | None] = mapped_column(String)


class ShoppingList(Base):
    __tablename__ = "shopping_lists"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String)
    created_at: Mapped[str] = mapped_column(String)


class ListItem(Base):
    __tablename__ = "list_items"
    __table_args__ = (
        CheckConstraint(one_of("match_mode", MatchMode), name="match_mode"),
        CheckConstraint(one_of("qty_unit", Unit), name="qty_unit"),
        CheckConstraint("min_tier IS NULL OR min_tier IN (1, 2, 3)", name="min_tier"),
        CheckConstraint("qty_needed > 0", name="qty_positive"),
        CheckConstraint("overbuy_max_units >= 0", name="overbuy_non_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    list_id: Mapped[int] = mapped_column(ForeignKey("shopping_lists.id"))
    label: Mapped[str] = mapped_column(String)
    match_mode: Mapped[str] = mapped_column(String)
    gtin: Mapped[str | None] = mapped_column(String)
    brand: Mapped[str | None] = mapped_column(String)
    canonical_id: Mapped[int | None] = mapped_column(ForeignKey("canonical_products.id"))
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"))
    min_tier: Mapped[int | None] = mapped_column(Integer)
    required_flags: Mapped[list | None] = mapped_column(JSON)
    name_contains: Mapped[list | None] = mapped_column(JSON)   # words the product name must contain
    qty_needed: Mapped[float] = mapped_column(Float)
    qty_unit: Mapped[str] = mapped_column(String)
    overbuy_max_units: Mapped[int] = mapped_column(Integer, default=0)
