"""T0.6: draft config/categories.yaml from the recon category trees. One-off: rerunning it
overwrites the hand-edited mappings in config/categories.yaml.

Backbone: the Conad tree (2 levels, stable numeric codes). Every own category maps 1:1 to its
Conad code. Coop mappings are fuzzy proposals; Lidl and Eurospin only expose coarse categories,
so they map onto top-level categories only. Everything is marked for review and unselected."""

import json
import re
import unicodedata
from pathlib import Path

from rapidfuzz import fuzz, process

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "recon" / "out"
COOP_THRESHOLD = 85

# coarse source categories -> Conad top-level codes (hand-mapped from the labels seen in recon)
EUROSPIN = {
    "/0001": ["01"],                          # Frutta e verdura
    "/0002": ["02", "03"],                    # Carne e pesce
    "/0003": ["02", "04", "12"],              # Gastronomia, salumi e formaggi
    "/0004": ["06", "09"],                    # Pane e pasticceria
    "/0005": ["04"],                          # Latticini e uova
    "/0006": ["05", "06", "07", "08", "10"],  # Dispensa
    "/0007": ["17", "18"],                    # Bevande
    "/0008": ["11"],                          # Surgelati e gelati
    "/0009": ["14"],                          # Igiene e cura personale
    "/0011": ["13"],                          # Casa
    "/0012": ["16"],                          # Mondo animali
    "/0102": ["22"],                          # Fai da te
}
EUROSPIN_UNMAPPED = {"/0000": "Specialità"}
LIDL = {
    "Cibo e quasi cibo/Frutta e verdura": ["01"],
    "Cibo e quasi cibo/Carne e pollame": ["02"],
    "Cibo e quasi cibo/Pesce e frutti di mare": ["03"],
    "Cibo e quasi cibo/Formaggi, latticini e uova": ["04"],
    "Cibo e quasi cibo/Dolciumi e snack": ["06", "09"],
    "Cibo e quasi cibo/Panificio": ["09"],
    "Cibo e quasi cibo/Cibo congelato (Cibo congelato)": ["11"],
    "Cibo e quasi cibo/Bilancio": ["13"],
    "Cucina e casa/Pulizia della casa": ["13"],
    "Cibo e quasi cibo/Prodotti per la cura della persona": ["14"],
    "Cibo e quasi cibo/Articoli per animali domestici": ["16"],
    "Vino, birra e liquori": ["17"],
}


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def main() -> None:
    conad = json.loads((OUT / "conad" / "categories.json").read_text(encoding="utf-8"))
    coop = json.loads((OUT / "coop" / "categories.json").read_text(encoding="utf-8"))
    coop_by_name = {c["key"]: c["name"] for c in coop}

    slugs: dict[str, str] = {}
    # top-level first, so a child that repeats its parent's name gets the prefixed slug
    for c in sorted(conad, key=lambda c: (c["parent"] is not None, c["code"])):
        slug = slugify(c["name"])
        if slug in slugs.values():
            slug = f"{slugs[c['parent']]}__{slug}"
        slugs[c["code"]] = slug

    lines = [
        "# Draft from T0.6 (recon/draft_categories.py). Backbone: the Conad tree.",
        "# coop_catalog mappings are fuzzy proposals; lidl_offers and eurospin_offers only expose",
        "# coarse categories and map onto top-level categories. Review before selecting anything.",
        f"# Eurospin categories without mapping: {EUROSPIN_UNMAPPED}",
        "categories:",
    ]
    for c in sorted(conad, key=lambda c: c["code"]):
        code = c["code"]
        # whole-name similarity; token_set_ratio scores "Pasta" vs "Pasta fresca" as 100.
        # On ties prefer the shallower Coop category.
        matches = process.extract(c["name"], coop_by_name, scorer=fuzz.ratio, processor=str.lower, limit=5)
        good = [m for m in matches if m[1] >= COOP_THRESHOLD]
        coop_keys = [min(good, key=lambda m: (-m[1], m[2].count("/")))[2]] if good else []
        sources = {
            "conad_catalog": [code],
            "coop_catalog": coop_keys,
            "lidl_offers": [k for k, v in LIDL.items() if code in v],
            "eurospin_offers": [k for k, v in EUROSPIN.items() if code in v],
        }
        lines += [
            f"  - slug: {slugs[code]}",
            f"    name: {json.dumps(c['name'], ensure_ascii=False)}",
            f"    parent: {slugs[c['parent']] if c['parent'] else 'null'}",
            "    selected: false",
            "    review: true",
            "    sources:",
        ]
        for source, keys in sources.items():
            lines.append(f"      {source}: {json.dumps(keys, ensure_ascii=False)}")
    (ROOT / "config" / "categories.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    mapped_coop = sum(1 for line in lines if line.startswith("      coop_catalog:") and line.strip() != "coop_catalog: []")
    print(f"{len(conad)} categories; {mapped_coop} with a coop proposal")


if __name__ == "__main__":
    main()
