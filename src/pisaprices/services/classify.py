"""After each run: placing the products whose category the site left open, then assigning tier,
flags and canonical product to every categorised product."""

from collections import Counter
from dataclasses import dataclass

from pisaprices.domain.enums import ReviewKind, Source
from pisaprices.ingest.categories import CATALOG_SOURCES, CategoryIndex
from pisaprices.ingest.classify import classify, load_tier_rules
from pisaprices.services import naming
from pisaprices.services.clock import iso_utc
from pisaprices.services.collect import Runtime
from pisaprices.storage.repos import classify as repo
from pisaprices.storage.repos import planning as planning_repo
from pisaprices.storage.repos import review as review_repo

MIN_VOTES = 3      # products named alike needed to decide
MIN_SHARE = 0.6    # share of them that must agree on one category


def _decisive(votes) -> bool:
    if not votes:
        return False
    _, count = votes.most_common(1)[0]
    return count >= MIN_VOTES and count / sum(votes.values()) >= MIN_SHARE


def _queries(title_words: list[str], head_words: list[str]) -> list[list[str]]:
    """Most specific first: the whole title, the first word with each other word ("filetto maiale"),
    then the head shrinking ("yogurt greco" -> "yogurt")."""
    first = title_words[:1]
    queries = [title_words, *(first + [w] for w in title_words[1:])]
    queries += [head_words[:n] for n in range(len(head_words), 0, -1)]
    unique: list[list[str]] = []
    for query in queries:
        if query and query not in unique:
            unique.append(query)
    return unique


def _votes(session, categories: naming.Categories, product: dict, allowed: set[str] | None):
    """The categories of the products named alike (only `allowed` ones, when the site narrowed them):
    the first decisive query, else the one with most votes."""
    title = naming.without_brand_prefix(product["name"])
    head_words = naming.words(" ".join(naming.head(title, product["brand"])))
    title_words = naming.words(title)
    if head_words and title_words[:1] != head_words[:1]:
        title_words = head_words[:1] + [w for w in title_words if w != head_words[0]]
    best = None
    for query in _queries(title_words, head_words):
        rows, _ = naming.named_alike(session, query)
        votes = categories.votes([r for r in rows if r["id"] != product["id"]])
        if allowed is not None:
            votes = Counter({slug: n for slug, n in votes.items() if slug in allowed})
        if _decisive(votes):
            return votes
        if votes and (best is None or sum(votes.values()) > sum(best.values())):
            best = votes
    return best


@dataclass
class PlaceReport:
    placed: int = 0
    proposed: int = 0


def place_open_products(runtime: Runtime) -> PlaceReport:
    """A product without category (an offer from a coarse site category, or a title no rule
    decided) takes the category of the products named alike ("Torta Sbrisolona" -> the category
    of the other "Torta ..." products) when they clearly agree; otherwise the best guess goes to
    review. Products nothing resembles stay out (not food we collect), and so do products no current
    run listed (they carry no site category)."""
    report = PlaceReport()
    selected = {c.slug for c in runtime.config.selected_categories()}
    now = iso_utc(runtime.clock.now())
    index = CategoryIndex(runtime.config)
    with runtime.sessions.begin() as session:
        categories = naming.Categories(session, selected)
        ids = planning_repo.category_slugs(session)
        review_repo.drop_settled_category_proposals(session)
        decided = review_repo.keys_of_kind(session, ReviewKind.CATEGORY_RULE)
        for product in repo.products_without_category(session):
            key = f"category:{product['id']}"
            if key in decided or not product["source_key"]:   # no site category: not seen by a current collector
                continue
            if not index.is_ours(Source(product["source"]), product["source_key"], product["name"]):
                continue   # excluded by the rules (stored by an earlier run)
            # a catalog category ("Formaggi") already narrowed the choice; offer categories are too noisy for that
            allowed = None
            if product["source"] in CATALOG_SOURCES and product["source_key"]:
                allowed = [c.slug for c in index.candidates(Source(product["source"]), product["source_key"])]
            votes = _votes(session, categories, product, set(allowed) if allowed is not None else None)
            if not votes and not allowed:
                continue
            best = votes.most_common(1)[0][0] if votes else None   # nothing named alike: the person chooses
            # among the 2-4 close candidates of a catalog category the most named-alike one is enough
            if _decisive(votes) or (allowed and votes):
                repo.set_category(session, product["id"], ids[best])
                report.placed += 1
            else:
                report.proposed += review_repo.add_review(
                    session, ReviewKind.CATEGORY_RULE, "products", product["id"], key=key,
                    payload={"name": product["name"], "chain": product["chain"], "suggestion": best,
                             "votes": dict(votes.most_common(3)) if votes else {}, "candidates": allowed},
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
