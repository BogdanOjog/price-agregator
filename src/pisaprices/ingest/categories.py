"""Site categories -> own categories, from categories.yaml (selected categories only).

A site key maps to an own category, or to an own group (a parent): then every selected category
under the group is a candidate. Keys match exactly or as a path prefix ("Cibo e quasi cibo/Bevande"
covers "Cibo e quasi cibo/Bevande/Acqua"). Among several candidates the title decides:

1. a catalog key with a single candidate: the site already decided;
2. the first candidate (file order) with an `include` word in the head of the title (before "di",
   "al"..., see ingest/titles.py) and no `exclude` word anywhere;
3. the single candidate without `include` words (a catch-all, still subject to its `exclude`);
4. otherwise None: the product is ours but its category is open (decided later from similar
   products, see services/classify.py)."""

import re

from pisaprices.config.schemas import AppConfig, CategoryConfig
from pisaprices.domain.enums import Source
from pisaprices.ingest.titles import head_end, without_brand_prefix

CATALOG_SOURCES = {Source.COOP_CATALOG, Source.CONAD_CATALOG, Source.LIDL_EVERLI}


def _word_start(rule: str, title: str) -> int | None:
    """Where the rule first matches at the start of a word: "crema" matches "crema di", not "scremato"."""
    match = re.search(r"(?<!\w)" + re.escape(rule.lower()), title)
    return match.start() if match else None


def _covers(mapped: str, key: str) -> bool:
    return key == mapped or key.startswith(mapped + "/")


class CategoryIndex:
    def __init__(self, config: AppConfig) -> None:
        self._by_slug = {c.slug: c for c in config.categories.categories}
        self._selected: list[CategoryConfig] = config.selected_categories()

    def _lineage(self, category: CategoryConfig | None):
        while category is not None:
            yield category
            category = self._by_slug.get(category.parent) if category.parent else None

    def candidates(self, source: Source, key: str | None) -> list[CategoryConfig]:
        """Selected categories that the site key maps to, directly or through a parent."""
        if not key:
            return []
        return [c for c in self._selected
                if any(_covers(mapped, key) for owner in self._lineage(c) for mapped in owner.sources.get(source, []))]

    def is_ours(self, source: Source, key: str | None, title: str | None = None) -> bool:
        """The site key maps to a selected category, and (given a title) not every candidate excludes
        it: "Candeline per torta" in Coop's baking ingredients is not food. Exclusions are decisions,
        so such products are dropped without review."""
        candidates = self.candidates(source, key)
        if title is None or not candidates:
            return bool(candidates)
        return any(not (c.title_rules and c.title_rules.exclude) or title_matches([], c.title_rules.exclude, title)
                   for c in candidates)

    def classify(self, source: Source, key: str | None, title: str) -> str | None:
        candidates = self.candidates(source, key)
        if len(candidates) == 1 and source in CATALOG_SOURCES:
            return candidates[0].slug
        for category in candidates:
            rules = category.title_rules
            if rules and rules.include and title_matches(rules.include, rules.exclude, title):
                return category.slug
        catch_all = [c for c in candidates if not (c.title_rules and c.title_rules.include)]
        if len(catch_all) == 1 and title_matches([], catch_all[0].title_rules.exclude if catch_all[0].title_rules else [], title):
            return catch_all[0].slug
        return None


def title_matches(include: list[str], exclude: list[str], title: str) -> bool:
    """A title belongs to a category: an `include` word in its head (or no include list) and no
    `exclude` word anywhere. "Tonno all'olio extravergine" does not match `include: [extravergine]`."""
    lowered = without_brand_prefix(title).lower()
    end = head_end(lowered)
    included = not include or any((start := _word_start(w, lowered)) is not None and start < end for w in include)
    return included and not any(_word_start(w, lowered) is not None for w in exclude)
