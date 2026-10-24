"""Shopping in plain words: "pasta 1 kg", "2 l latte", "uova 10" -> list items the planner understands.

Each line becomes: the words to look for in product names, a quantity, and the category most of
the matching products belong to (among the selected categories). A product matches only when the
line's first word names it (is in the head of its name, before "di", "con", "al"...): "ceci" does not
match "Penne farina di ceci". Without a quantity, the most common pack of the matching products is
used. The guess is shown to the user, who can correct it, and can ask for a brand."""

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date

from pisaprices.ingest.units import parse_quantity
from pisaprices.services import lists, naming
from pisaprices.services.collect import Runtime
from pisaprices.services.naming import display_brand  # noqa: F401  (used by the UI)
from pisaprices.services.plan import PlanResult, plan_items

QUALITY = {None: "oricare", 1: "economic", 2: "standard", 3: "premium"}


@dataclass
class Draft:
    line: str
    label: str
    words: list[str]
    quantity: str                   # "1 kg", "2 l", "10 pz" (editable text)
    category: str | None            # own category slug
    min_tier: int | None = None
    flags: list[str] = field(default_factory=list)
    matches: int = 0                # products found for these words
    note: str = ""
    brand: str | None = None        # asked by the user; None = any brand
    brands: list[str] = field(default_factory=list)   # the most common brands among the matches

    def item(self) -> dict:
        """The list item for lists.item_values / planning."""
        item = {"label": self.label, "category": self.category, "quantity": self.quantity,
                "contains": self.words or None, "min_tier": self.min_tier, "flags": self.flags or None, "overbuy": 1}
        if self.brand:
            item |= {"match": "brand", "brand": self.brand}
        return item


def _format(amount: float, unit: str) -> str:
    if unit == "g" and amount >= 1000 and amount % 1000 == 0:
        return f"{amount / 1000:g} kg"
    if unit == "ml" and amount >= 1000 and amount % 1000 == 0:
        return f"{amount / 1000:g} l"
    return f"{amount:g} {unit}"


def _bare_count(line: str) -> int | None:
    match = re.fullmatch(r"\s*(\d+)\s+[^\d]+|[^\d]+?\s+(\d+)\s*", line)
    if not match:
        return None
    return int(match.group(1) or match.group(2))


def interpret(runtime: Runtime, text: str) -> list[Draft]:
    selected = {c.slug for c in runtime.config.selected_categories()}
    category_tokens = {t for slug in selected for t in re.findall(r"[a-z]+", slug)}
    drafts = []
    with runtime.sessions() as session:
        located = naming.Categories(session, selected)
        for raw in text.splitlines():
            line = raw.strip(" -•*\t")
            if not line:
                continue
            words = naming.words(line)
            if not words:
                continue
            rows, ingredient = naming.named_alike(session, words)
            if not rows:   # "pasta spaghetti": a word may only name the category ("Barilla Spaghetti n.5")
                naming_words = [w for w in words if w in category_tokens]
                if naming_words and len(naming_words) < len(words):
                    rows, _ = naming.named_alike(session, [w for w in words if w not in naming_words])
            votes = located.votes(rows)
            category = votes.most_common(1)[0][0] if votes else None
            in_category = [r for r in rows if located.slug(r["category_id"]) == category] if category else []
            if category:   # "pasta", "latte", "olio": the category already says it, keep only the other words
                tokens = set(re.findall(r"[a-z]+", category))
                words = [w for w in words if w not in tokens]

            quantity, note = None, ""
            parsed = parse_quantity(line)
            if parsed and not parsed.variable_weight:
                quantity = _format(parsed.amount, parsed.unit.value)
            elif (count := _bare_count(line)) is not None:
                quantity = f"{count} pz"
            elif in_category:
                packs = Counter((r["net_qty"], r["unit"]) for r in in_category if r["net_qty"] and r["unit"])
                if packs:
                    (amount, unit), _ = packs.most_common(1)[0]
                    quantity, note = _format(amount, unit), "cantitate ghicită (ambalajul obișnuit)"
            if category is None:
                note = (f"„{words[0]}” apare doar ca ingredient; categoria lui nu e colectată" if ingredient and not rows
                        else "nu găsesc produsul în categoriile colectate")
            brands = Counter(naming.display_brand(r["brand"]) for r in in_category if naming.display_brand(r["brand"]))
            drafts.append(Draft(line=line, label=line[:40], words=words, quantity=quantity or "1 pz",
                                category=category, matches=len(in_category), note=note,
                                brands=[b for b, _ in brands.most_common(6)]))
    return drafts


def plan_drafts(runtime: Runtime, drafts: list[Draft], start: date | None = None,
                window_days: int | None = None) -> PlanResult:
    """Plan the interpreted lines without saving a list."""
    rows = []
    with runtime.sessions() as session:
        for index, d in enumerate(d for d in drafts if d.category):
            rows.append({"id": index + 1, **lists.item_values(session, d.item())})
    if not rows:
        raise ValueError("niciun produs recunoscut în categoriile colectate")
    return plan_items(runtime, "Cumpărături", rows, start, window_days)


def save_as_list(runtime: Runtime, name: str, drafts: list[Draft]) -> int:
    list_id = lists.create_list(runtime, name)
    for d in drafts:
        if d.category:
            lists.add_item(runtime, list_id, d.item())
    return list_id
