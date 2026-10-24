"""Everli recon in a real browser (lidl.everli.com allows everything in robots.txt): load the Lidl
storefront, optionally enter a public Pisa address, and record every XHR/fetch request and JSON
response. No login, no cart, no CAPTCHA solving: if a challenge appears, stop.

usage: python browser_everli_lidl.py [address]"""

import json
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parent / "out" / "everli" / "browser"
UA = "pisa-prices/0.1 (uz personal)"


def main(address: str | None) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    calls = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(user_agent=UA, viewport={"width": 1280, "height": 900}, locale="it-IT")

        def on_response(response):
            request = response.request
            if request.resource_type not in ("xhr", "fetch"):
                return
            entry = {"method": request.method, "url": response.url, "status": response.status,
                     "content_type": response.headers.get("content-type", ""), "post": request.post_data}
            if "json" in entry["content_type"]:
                try:
                    body = response.json()
                    name = f"{len(calls):03d}_{re.sub(r'[^A-Za-z0-9]+', '_', response.url.split('?')[0])[-60:]}.json"
                    (OUT / name).write_text(json.dumps(body, ensure_ascii=False, indent=1), encoding="utf-8")
                    entry["saved"] = name
                except Exception:
                    pass
            calls.append(entry)

        page.on("response", on_response)
        page.goto("https://lidl.everli.com/", wait_until="networkidle", timeout=60000)
        page.screenshot(path=str(OUT / "01_home.png"), full_page=True)
        if address:
            # cookie banner "X": technical cookies only
            page.wait_for_selector("#onetrust-banner-sdk", state="visible", timeout=20000)
            for selector in ("#onetrust-close-btn-handler", ".onetrust-close-btn-handler", "#onetrust-banner-sdk button[aria-label]"):
                close = page.locator(selector)
                if close.count() and close.first.is_visible():
                    print("closing cookie banner with", selector)
                    close.first.click()
                    break
            page.wait_for_selector(".onetrust-pc-dark-filter", state="hidden", timeout=10000)
            box = page.get_by_placeholder("Inserisci il tuo indirizzo")
            box.click()
            box.press_sequentially(address, delay=60)
            page.wait_for_timeout(3000)
            page.screenshot(path=str(OUT / "02_address_typed.png"), full_page=True)
            suggestion = page.locator("[role=option], .pac-item, li").filter(has_text="Pisa").first
            if suggestion.count():
                suggestion.click()
                page.wait_for_timeout(2000)
            start = page.get_by_role("button", name="Inizia ad acquistare")
            if start.count() and start.is_enabled():
                start.click()
                page.wait_for_load_state("networkidle", timeout=60000)
                page.wait_for_timeout(3000)
            page.screenshot(path=str(OUT / "03_after_address.png"), full_page=True)
            print("url after address:", page.url)
            if len(sys.argv) > 2:   # open one category by its visible name and scroll to load more
                page.get_by_text(sys.argv[2], exact=True).first.click()
                page.wait_for_load_state("networkidle", timeout=60000)
                for _ in range(4):
                    page.mouse.wheel(0, 4000)
                    page.wait_for_timeout(2500)
                page.screenshot(path=str(OUT / "04_category.png"))
                print("url of category:", page.url)
        (OUT / "calls.json").write_text(json.dumps(calls, ensure_ascii=False, indent=1), encoding="utf-8")
        browser.close()
    for c in calls:
        print(c["status"], c["method"], c["url"][:150], c.get("saved", ""))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
