"""Linking promotion items to products, and deciding review items."""

from dataclasses import dataclass

from pisaprices.domain.enums import ReviewKind, ReviewStatus
from pisaprices.ingest.records import ProductRef
from pisaprices.ingest.resolve import Candidate, resolve
from pisaprices.services.clock import iso_utc
from pisaprices.services.collect import Runtime
from pisaprices.storage.repos import classify as classify_repo
from pisaprices.storage.repos import planning as planning_repo
from pisaprices.storage.repos import review as repo


@dataclass
class ResolveReport:
    linked: int = 0
    proposed: int = 0
    unmatched: int = 0


def resolve_pending(runtime: Runtime) -> ResolveReport:
    """Try to link every promotion item still without a product."""
    report = ResolveReport()
    threshold = runtime.config.settings.quality.fuzzy_match_threshold
    now = iso_utc(runtime.clock.now())
    with runtime.sessions.begin() as session:
        candidates: dict[str, list[Candidate]] = {}
        for item in repo.unresolved_items(session):
            ref = ProductRef.model_validate(item["ref"])
            chain = ref.chain.value
            if chain not in candidates:
                candidates[chain] = [Candidate(**row) for row in repo.products_of_chain(session, chain)]
            result = resolve(ref, candidates[chain], threshold)
            if result.product_id is not None:
                repo.link_item(session, item["promotion_id"], item["ref_key"], result.product_id)
                report.linked += 1
            elif result.proposal_id is not None:
                added = repo.add_review(
                    session, ReviewKind.PRODUCT_MATCH, "promotion_items", item["promotion_id"],
                    key=f"{item['promotion_id']}|{item['ref_key']}",
                    payload={"ref_key": item["ref_key"], "ref_name": ref.name, "product_id": result.proposal_id,
                             "score": round(result.score, 1)},
                    created_at=now,
                )
                report.proposed += added
            else:
                report.unmatched += 1
    return report


def open_reviews(runtime: Runtime, kind: ReviewKind | None = None) -> list[dict]:
    with runtime.sessions() as session:
        return repo.open_reviews(session, kind)


def accept(runtime: Runtime, review_id: int) -> None:
    with runtime.sessions.begin() as session:
        item = repo.decide(session, review_id, ReviewStatus.ACCEPTED, iso_utc(runtime.clock.now()))
        if item["kind"] == ReviewKind.PRODUCT_MATCH.value:
            payload = item["payload"]
            repo.link_item(session, item["subject_id"], payload["ref_key"], payload["product_id"])
        elif item["kind"] == ReviewKind.CATEGORY_RULE.value:
            category_id = planning_repo.category_slugs(session).get(item["payload"]["suggestion"])
            if category_id is not None:
                classify_repo.set_category(session, item["subject_id"], category_id)


def reject(runtime: Runtime, review_id: int) -> None:
    with runtime.sessions.begin() as session:
        repo.decide(session, review_id, ReviewStatus.REJECTED, iso_utc(runtime.clock.now()))

