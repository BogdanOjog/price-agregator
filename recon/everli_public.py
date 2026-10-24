"""Everli recon, public pages only: robots.txt of both hosts, the Lidl Pisa city page and the Lidl
storefront home. Nothing under /supermercato/ or /s? (disallowed by robots.txt)."""

import re

from politehttp import ReconClient

PAGES = [
    ("https://it.everli.com/robots.txt", "robots_it"),
    ("https://lidl.everli.com/robots.txt", "robots_lidl"),
    ("https://it.everli.com/it/spesa/city/lidl-pisa", "city_lidl_pisa"),
    ("https://lidl.everli.com/", "lidl_home"),
]


def main() -> None:
    client = ReconClient("everli")
    for url, label in PAGES:
        response = client.get(url, label)
        text = response.text
        print(f"== {label}: {response.status_code} {len(text)} bytes, final {response.url}")
        if label.startswith("robots"):
            print(text[:1500])
            continue
        hosts = sorted(set(re.findall(r"https?://([a-z0-9.-]*everli[a-z0-9.-]*)", text)))
        apis = sorted(set(re.findall(r"[\"'](/(?:api|graphql|v\d)[^\"'?#]{0,80})", text)))[:30]
        print("hosts:", hosts)
        print("api-like paths:", apis)
        print("prices in page:", len(re.findall(r"\d+,\d{2}\s*€|€\s*\d+,\d{2}", text)),
              "| __NEXT_DATA__:", "__NEXT_DATA__" in text, "| nuxt:", "__NUXT__" in text)


if __name__ == "__main__":
    main()
