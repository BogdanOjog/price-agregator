"""T0.2: find terms/legal page links on each source's home page."""

import time
from pathlib import Path
from urllib.parse import urljoin

import httpx
from ruamel.yaml import YAML
from selectolax.lexbor import LexborHTMLParser as HTMLParser

HOMES = [
    "https://prenotalaspesa.coopfirenze.it/",
    "https://www.coopfirenze.it/",
    "https://spesaonline.conad.it/",
    "https://www.conad.it/",
    "https://www.lidl.it/",
    "https://www.eurospin.com/",
]
KEYWORDS = ("condizion", "termini", "legal", "note-legali", "notelegali", "uso del sito", "cgv", "terms")

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    http = YAML(typ="safe").load((ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))["http"]
    headers = {"User-Agent": http["user_agent"]}
    with httpx.Client(headers=headers, timeout=http["timeout_seconds"], follow_redirects=True) as client:
        for home in HOMES:
            try:
                response = client.get(home)
            except httpx.HTTPError as exc:
                print(f"== {home}: error {type(exc).__name__}")
                continue
            print(f"== {home}: {response.status_code}, {len(response.text)} chars")
            seen = set()
            for a in HTMLParser(response.text).css("a[href]"):
                href = a.attributes.get("href") or ""
                text = a.text(strip=True)
                haystack = f"{href} {text}".lower()
                if any(k in haystack for k in KEYWORDS):
                    url = urljoin(str(response.url), href)
                    if url not in seen:
                        seen.add(url)
                        print(f"   {text[:60]!r} -> {url}")
            time.sleep(http["min_delay_seconds"])


if __name__ == "__main__":
    main()
