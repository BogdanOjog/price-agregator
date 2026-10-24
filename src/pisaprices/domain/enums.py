"""Closed sets of values shared by every layer. The database CHECK constraints are built from these."""

from enum import StrEnum


class Chain(StrEnum):
    COOP = "coop"
    CONAD = "conad"
    LIDL = "lidl"
    EUROSPIN = "eurospin"


class Program(StrEnum):
    COOP_SOCIO = "coop_socio"
    CONAD_CARTA_INSIEME = "conad_carta_insieme"
    LIDL_PLUS = "lidl_plus"
    EUROSPIN_FAMILY = "eurospin_family"


class Source(StrEnum):
    """One collector per source."""

    COOP_CATALOG = "coop_catalog"
    COOP_SOCI = "coop_soci"
    CONAD_CATALOG = "conad_catalog"
    LIDL_OFFERS = "lidl_offers"
    LIDL_EVERLI = "lidl_everli"
    EUROSPIN_OFFERS = "eurospin_offers"


class Transport(StrEnum):
    WALK = "walk"
    BIKE = "bike"
    BUS = "bus"
    CAR = "car"


class Weekday(StrEnum):
    MON = "mon"
    TUE = "tue"
    WED = "wed"
    THU = "thu"
    FRI = "fri"
    SAT = "sat"
    SUN = "sun"


class SourceMode(StrEnum):
    CATALOG = "catalog"
    PROMO_ONLY = "promo_only"


class Mechanic(StrEnum):
    """The cost formula of a promotion. Card, caps, sottocosto and activation are modifiers."""

    PRICE_CUT = "PRICE_CUT"
    MULTIBUY_N_FOR_M = "MULTIBUY_N_FOR_M"
    NTH_UNIT_DISCOUNT = "NTH_UNIT_DISCOUNT"
    TIERED_ALL_UNITS = "TIERED_ALL_UNITS"
    TIERED_INCREMENTAL = "TIERED_INCREMENTAL"
    BASKET_THRESHOLD = "BASKET_THRESHOLD"
    REWARD = "REWARD"
    UNKNOWN = "UNKNOWN"


class Channel(StrEnum):
    STORE = "store"
    ONLINE = "online"
    BOTH = "both"


class Unit(StrEnum):
    GRAM = "g"
    MILLILITRE = "ml"
    PIECE = "pz"


class RunStatus(StrEnum):
    RUNNING = "running"
    OK = "ok"
    PARTIAL = "partial"
    FAILED = "failed"


class ObservationSource(StrEnum):
    ONLINE = "online"
    FLYER = "flyer"


class ReviewKind(StrEnum):
    PRODUCT_MATCH = "product_match"
    CANONICAL = "canonical"
    UNKNOWN_PROMO = "unknown_promo"
    UNIT_PRICE_MISMATCH = "unit_price_mismatch"
    TIERED_INCREMENTAL = "tiered_incremental"
    CATEGORY_RULE = "category_rule"


class ReviewStatus(StrEnum):
    OPEN = "open"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class MatchMode(StrEnum):
    EXACT_GTIN = "exact_gtin"
    BRAND = "brand"
    CANONICAL = "canonical"
    CATEGORY = "category"
