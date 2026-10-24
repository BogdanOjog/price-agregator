"""After each run: placing the products whose category the site left open, then assigning tier,
flags and canonical product to every categorised product."""

from dataclasses import dataclass

from pisaprices.domain.enums import ReviewKind
from pisaprices.ingest.classify import classify, load_tier_rules
from pisaprices.services import naming
from pisaprices.services.clock import iso_utc
from pisaprices.services.collect import Runtime
from pisaprices.storage.repos import classify as repo
from pisaprices.storage.repos import planning as planning_repo
from pisaprices.storage.repos import review as review_repo

MIN_VOTES = 3      # products named alike needed to decide
MIN_SHARE = 0.6    # share of them that must agree on one category


@dataclass
class PlaceReport:
    placed: int = 0
    proposed: int = 0


def place_open_products(runtime: Runtime) -> PlaceReport:
    """A product without category (an offer from a coarse site category, or a title no rule
    decided) takes the category of the products named alike ("Torta Sbrisolona" -> the category
    of the other "Torta ..." products) when they clearly agree; otherwise the best guess goes to
    review. Products nothing resembles stay out (not food we collect)."""
    report = PlaceReport()
    selected = {c.slug for c in runtime.config.selected_categories()}
    now = iso_utc(runtime.clock.now())
    with runtime.sessions.begin() as session:
        categories = naming.Categories(session, selected)
        ids = planning_repo.category_slugs(session)
        decided = review_repo.keys_of_kind(session, ReviewKind.CATEGORY_RULE)
        for product in repo.products_without_category(session):
            key = f"category:{product['id']}"
            if key in decided:
                continue
            title = naming.without_brand_prefix(product["name"])
            query = naming.words(" ".join(naming.head(title, product["brand"])))
            votes = None
            for n in range(len(query), 0, -1):   # "Yogurt greco autentico" -> "yogurt greco" -> "yogurt"
                rows, _ = naming.named_alike(session, query[:n])
                votes = categories.votes([r for r in rows if r["id"] != product["id"]])
                if sum(votes.values()) >= MIN_VOTES:
                    break
            if not votes:
                continue
            (best, count), total = votes.most_common(1)[0], sum(votes.values())
            if count >= MIN_VOTES and count / total >= MIN_SHARE:
                repo.set_category(session, product["id"], ids[best])
                report.placed += 1
            else:
                report.proposed += review_repo.add_review(
                    session, ReviewKind.CATEGORY_RULE, "products", product["id"], key=key,
                    payload={"name": product["name"], "chain": product["chain"], "suggestion": best,
                             "votes": dict(votes.most_common(3))},
                    created_at=now,
                )
    return report


def classify_products(runtime: Runtime) -> int:
    """Returns how many products changed canonical product."""
    rules = load_tier_rules(runtime.root / "config" / "tier_rules.yaml")
    changed = 0
    with runtime.sessions.begin() as session:
        for p in repo.products_with_category(session):
            result = classify(rules, p["chain"], p["brand"], p["name"], p["net_qty"], p["unit"], p["variable_weight"])
            canonical = repo.canonical_id(session, p["category_id"], result.format, result.tier, list(result.flags))
            if canonical != p["canonical_id"]:
                repo.set_canonical(session, p["id"], canonical)
                changed += 1
    return changed
