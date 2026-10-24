"""Shopping lists: stored in the database, imported/exported as YAML with stable keys.

lista.yaml:
    name: Spesa della settimana
    items:
      - label: Spaghetti
        category: pasta_di_semola          # match: category (default when only a category is given)
        quantity: 1 kg
        min_tier: 2
        overbuy: 1
      - label: Uova bio
        match: canonical
        canonical: {category: uova, format: 6 pz, tier: 2, flags: [bio]}
        quantity: 6 pz
      - {label: Latte, match: brand, category: latte, brand: Milbona, quantity: 2 l}
      - {label: Pizzoccheri, match: exact_gtin, gtin: "8003170062443", quantity: 500 g}
      - {label: Olio EVO, category: olio_aceto_e_altri_condimenti, contains: [extravergine], quantity: 1 l}

`contains`: words the product name must contain (a category such as "Olio, aceto e altri
condimenti" holds oils and vinegars).
"""

import io
import json

from ruamel.yaml import YAML

from pisaprices.domain.enums import MatchMode
from pisaprices.ingest.units import parse_quantity
from pisaprices.services.clock import iso_utc
from pisaprices.services.collect import Runtime
from pisaprices.storage.repos import planning as repo


class ListError(ValueError):
    pass


def _quantity(text: str, label: str) -> tuple[float, str]:
    quantity = parse_quantity(str(text))
    if quantity is None or quantity.variable_weight:
        raise ListError(f"'{label}': quantity '{text}' not understood (use e.g. '500 g', '1 kg', '2 l', '6 pz')")
    return quantity.amount, quantity.unit.value


def _words(value) -> list[str] | None:
    if not value:
        return None
    words = [value] if isinstance(value, str) else list(value)
    return [str(w).strip() for w in words if str(w).strip()] or None


def _canonical_id(session, spec: dict, label: str) -> int:
    categories = repo.category_slugs(session)
    if spec.get("category") not in categories:
        raise ListError(f"'{label}': unknown category '{spec.get('category')}'")
    flags = sorted(spec.get("flags") or [])
    for row in repo.canonical_rows(session):
        row_flags = sorted(json.loads(row["flags"]) if isinstance(row["flags"], str) else (row["flags"] or []))
        if (row["category_id"], row["format"], row["tier"], row_flags) == (categories[spec["category"]], spec.get("format"), spec.get("tier"), flags):
            return row["id"]
    raise ListError(f"'{label}': no product class {spec} yet (collect first, or use match: category)")


def item_values(session, item: dict) -> dict:
    label = item.get("label") or "?"
    mode = MatchMode(item.get("match") or ("canonical" if "canonical" in item else "category"))
    qty, unit = _quantity(item.get("quantity", ""), label)
    values = {"label": label, "match_mode": mode.value, "qty_needed": qty, "qty_unit": unit,
              "overbuy_max_units": int(item.get("overbuy", 0)), "min_tier": item.get("min_tier"),
              "required_flags": list(item.get("flags") or []) or None,
              "name_contains": _words(item.get("contains")), "gtin": None, "brand": None,
              "canonical_id": None, "category_id": None}
    categories = repo.category_slugs(session)
    if item.get("category"):
        if item["category"] not in categories:
            raise ListError(f"'{label}': unknown category '{item['category']}'")
        values["category_id"] = categories[item["category"]]
    if mode is MatchMode.EXACT_GTIN:
        values["gtin"] = str(item.get("gtin") or "") or None
        if not values["gtin"]:
            raise ListError(f"'{label}': match exact_gtin needs gtin")
    elif mode is MatchMode.BRAND:
        values["brand"] = item.get("brand")
        if not values["brand"]:
            raise ListError(f"'{label}': match brand needs brand")
    elif mode is MatchMode.CANONICAL:
        values["canonical_id"] = _canonical_id(session, item.get("canonical") or {}, label)
    elif values["category_id"] is None:
        raise ListError(f"'{label}': match category needs category")
    return values


def import_yaml(runtime: Runtime, text: str) -> int:
    data = YAML(typ="safe").load(text) or {}
    if not isinstance(data.get("items"), list) or not data["items"]:
        raise ListError("the list has no items")
    with runtime.sessions.begin() as session:
        list_id = repo.create_list(session, data.get("name") or "lista", iso_utc(runtime.clock.now()))
        for item in data["items"]:
            repo.add_item(session, list_id, item_values(session, item))
    return list_id


def add_item(runtime: Runtime, list_id: int, item: dict) -> int:
    with runtime.sessions.begin() as session:
        if repo.get_list(session, list_id) is None:
            raise ListError(f"no list {list_id}")
        return repo.add_item(session, list_id, item_values(session, item))


def create_list(runtime: Runtime, name: str) -> int:
    with runtime.sessions.begin() as session:
        return repo.create_list(session, name, iso_utc(runtime.clock.now()))


def remove_item(runtime: Runtime, item_id: int) -> None:
    with runtime.sessions.begin() as session:
        repo.delete_item(session, item_id)


def all_lists(runtime: Runtime) -> list[dict]:
    with runtime.sessions() as session:
        return repo.lists(session)


def get_list(runtime: Runtime, list_id: int) -> dict:
    with runtime.sessions() as session:
        found = repo.get_list(session, list_id)
    if found is None:
        raise ListError(f"no list {list_id}")
    return found


def _format_qty(qty: float, unit: str) -> str:
    if unit == "g" and qty >= 1000 and qty % 1000 == 0:
        return f"{qty / 1000:g} kg"
    if unit == "ml" and qty >= 1000 and qty % 1000 == 0:
        return f"{qty / 1000:g} l"
    return f"{qty:g} {unit}"


def export_yaml(runtime: Runtime, list_id: int) -> str:
    found = get_list(runtime, list_id)
    with runtime.sessions() as session:
        slug_of = {v: k for k, v in repo.category_slugs(session).items()}
        canonical = {r["id"]: r for r in repo.canonical_rows(session)}
    items = []
    for i in found["items"]:
        item = {"label": i["label"], "match": i["match_mode"], "quantity": _format_qty(i["qty_needed"], i["qty_unit"])}
        if i["category_id"]:
            item["category"] = slug_of[i["category_id"]]
        if i["gtin"]:
            item["gtin"] = i["gtin"]
        if i["brand"]:
            item["brand"] = i["brand"]
        if i["canonical_id"]:
            row = canonical[i["canonical_id"]]
            flags = json.loads(row["flags"]) if isinstance(row["flags"], str) else (row["flags"] or [])
            item["canonical"] = {"category": slug_of[row["category_id"]], "format": row["format"], "tier": row["tier"], "flags": flags}
        if i["min_tier"]:
            item["min_tier"] = i["min_tier"]
        if i["required_flags"]:
            item["flags"] = list(i["required_flags"])
        if i["name_contains"]:
            item["contains"] = list(i["name_contains"])
        if i["overbuy_max_units"]:
            item["overbuy"] = i["overbuy_max_units"]
        items.append(item)
    buffer = io.StringIO()
    yaml = YAML()
    yaml.default_flow_style = False
    yaml.dump({"name": found["name"], "items": items}, buffer)
    return buffer.getvalue()
