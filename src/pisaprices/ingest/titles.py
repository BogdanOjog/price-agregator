"""What a product title says it is: its head, the words before the first connector.

"Penne farina | di ceci" is pasta, "Tonno | all'olio extravergine" is tuna, "Biscotti | al latte"
are biscuits. Offer titles often start with the brand in capitals ("DELUXE Torta Sbrisolona"),
which is not part of the head."""

import re

# words that end the head of a product name
CONNECTORS = {"di", "del", "della", "dello", "dei", "degli", "delle", "con", "al", "alla", "allo", "ai", "alle", "agli",
              "all", "e", "ed", "in", "a", "da", "per", "gusto", "senza", "su"}
WORD = r"[a-zàèéìòù]+"


def without_brand_prefix(title: str) -> str:
    """'DELUXE Torta Sbrisolona' -> 'Torta Sbrisolona'; 'SELECT & GO Sushi box' -> 'Sushi box'."""
    tokens = title.split()
    while len(tokens) > 1 and (tokens[0] == "&" or (len(tokens[0]) > 1 and tokens[0].isupper())):
        tokens.pop(0)
    return " ".join(tokens)


def head_end(lowered: str) -> int:
    """Index where the head of a lowercase title ends: the start of the first connector."""
    seen_word = False
    for match in re.finditer(WORD, lowered):
        if match.group() in CONNECTORS:
            if seen_word:
                return match.start()
        else:
            seen_word = True
    return len(lowered)


def head(name: str, brand: str | None = None) -> list[str]:
    """The words of the head, without a leading brand."""
    brand_words = set(re.findall(WORD, (brand or "").lower()))
    found: list[str] = []
    for word in re.findall(WORD, name.lower()):
        if word in CONNECTORS:
            if found:
                break
        elif found or word not in brand_words:
            found.append(word)
    return found
