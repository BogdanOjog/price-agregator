"""What a product is, from its name: the head of the name, the products named alike, the category they share.

Used to read a shopping list typed in plain words (services/quick.py) and to place the offers whose
site category is too coarse to decide (services/classify.py)."""

import re
from collections import Counter

from pisaprices.ingest.titles import head, without_brand_prefix  # noqa: F401  (re-exported)
from pisaprices.storage.repos import catalog as catalog_repo
from pisaprices.storage.repos import planning as planning_repo

STOPWORDS = {"di", "da", "del", "della", "al", "alla", "e", "con", "per", "in", "il", "la", "le", "lo", "gli", "i",
             "un", "una", "de", "si", "cu", "x", "pz", "pezzi", "kg", "g", "gr", "l", "lt", "ml", "cl", "litri", "litro"}


def display_brand(brand: str | None) -> str:
    """'DE CECCO' -> 'De Cecco', 'COOP -' -> 'Coop'."""
    return (brand or "").strip(" -").title()


def words(text: str) -> list[str]:
    """The searchable words of a text: no quantities, no stopwords."""
    text = re.sub(r"\d+(?:[.,]\d+)?\s*(?:kg|gr|g|ml|cl|lt|l|pz|pezzi|x)?\b", " ", text.lower())
    return [w for w in re.findall(r"[a-zàèéìòù']+", text) if w not in STOPWORDS and len(w) > 1]


def named_alike(session, query: list[str]) -> tuple[list[dict], bool]:
    """Products whose name has every word (at a word start) and the first one in its head;
    the flag says some products have the words only further on ("... di ceci")."""
    if not query:
        return [], False
    rows = [r for r in catalog_repo.search_products(session, " ".join(query), limit=200)
            if all(re.search(r"(?<!\w)" + re.escape(w), r["name"].lower()) for w in query)]
    named = [r for r in rows if any(h.startswith(query[0]) for h in head(r["name"], r["brand"]))]
    return named, bool(rows) and not named


class Categories:
    """Own category ids -> the selected category they belong to (a product of a sub-category
    counts for its selected parent)."""

    def __init__(self, session, selected: set[str]) -> None:
        self.selected = selected
        self._slug_of = {v: k for k, v in planning_repo.category_slugs(session).items()}
        self._parents = planning_repo.category_parents(session)

    def slug(self, category_id: int | None) -> str | None:
        while category_id is not None:
            if self._slug_of.get(category_id) in self.selected:
                return self._slug_of[category_id]
            category_id = self._parents.get(category_id)
        return None

    def votes(self, rows: list[dict]) -> Counter:
        return Counter(slug for slug in (self.slug(r["category_id"]) for r in rows) if slug)
