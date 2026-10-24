"""T0.3, approved by the user (option 2): one headless Playwright session to observe how
prenotalaspesa selects the Cascina pickup point. Records every demandware.store request.
No login, no cart, no forms other than the delivery mode and the store choice.
Stops on anything unexpected and always writes what it saw."""

import json
import re
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "recon" / "out" / "coop" / "browser"
BASE = "https://prenotalaspesa.coopfirenze.it"
CASCINA = "iestore34446"
NECESSARY_ONLY = re.compile(r"rifiuta|solo (i )?necessari|continua senza accettare|reject", re.I)
PICKUP = re.compile(r"ritiro in negozio", re.I)
GUEST = re.compile(r"dai solo un.occhiata", re.I)


def visible_texts(page: Page, selector: str) -> list[str]:
    return [t.strip() for t in page.locator(selector).all_inner_texts() if t.strip()][:60]


def run(page: Page, report: dict) -> None:
    page.goto(f"{BASE}/stores", wait_until="domcontentloaded")
    page.wait_for_timeout(4000)

    reject = page.get_by_role("button", name=NECESSARY_ONLY)
    if reject.count() and reject.first.is_visible():
        reject.first.click()
        report["steps"].append("cookie banner: clicked necessary-only/reject")
        page.wait_for_timeout(1500)
    else:
        report["steps"].append("cookie banner: no necessary-only button visible, nothing clicked")

    modal = page.locator("#landingModal")
    report["landing_modal_visible"] = modal.is_visible()
    if modal.is_visible():
        report["landing_modal_text"] = modal.inner_text()[:2000]
        browse = modal.get_by_text(GUEST)
        if not browse.count():
            page.screenshot(path=str(OUT / "landing_modal.png"))
            report["steps"].append("STOP: no guest option in landing modal")
            return
        browse.first.click()
        report["steps"].append("landing modal: clicked guest browsing ('Dai solo un'occhiata')")
        page.wait_for_timeout(3000)

    report["modals_after_guest"] = [m[:1500] for m in page.locator(".modal.show").all_inner_texts()]
    # the "Seleziona" button whose closest card mentions pickup and not home delivery
    pickup_index = page.evaluate(
        """() => {
            const buttons = [...document.querySelectorAll('.modal.show button, .modal.show a')]
                .filter(b => b.offsetParent && b.innerText.trim().toLowerCase() === 'seleziona');
            return buttons.findIndex(b => {
                let node = b;
                for (let i = 0; i < 4 && node; i++, node = node.parentElement) {
                    const text = node.innerText.toLowerCase();
                    if (text.includes('ritiro in negozio') && !text.includes('consegna a casa')) return true;
                }
                return false;
            });
        }"""
    )
    report["pickup_button_index"] = pickup_index
    if pickup_index >= 0:
        page.locator(".modal.show").locator("button:visible, a:visible").filter(has_text=re.compile(r"^\s*Seleziona\s*$")).nth(pickup_index).click()
        report["steps"].append("clicked 'Seleziona' on the pickup card")
        page.wait_for_timeout(3000)
        page.screenshot(path=str(OUT / "after_pickup_mode.png"))
        report["modals_after_pickup"] = [m[:1500] for m in page.locator(".modal.show").all_inner_texts()]
    else:
        report["steps"].append("no 'ritiro in negozio' choice shown; continuing on the stores page")

    select = page.locator(f'button[data-storeid="{CASCINA}"][data-action*="Stores-SelectStore"]:visible')
    report["cascina_select_visible"] = select.count()
    if not select.count():
        report["visible_buttons"] = visible_texts(page, "button:visible")
        report["open_modals"] = [m[:1500] for m in page.locator(".modal.show").all_inner_texts()]
        page.screenshot(path=str(OUT / "no_cascina_button.png"))
        report["steps"].append("STOP: no visible Cascina select button")
        return
    select.first.scroll_into_view_if_needed()
    select.first.click()
    report["steps"].append("clicked Cascina 'Seleziona negozio'")
    page.wait_for_timeout(5000)
    report["url_after_select"] = page.url
    report["open_modals_after_select"] = [m[:1500] for m in page.locator(".modal.show").all_inner_texts()]
    page.screenshot(path=str(OUT / "after_select.png"))

    page.goto(f"{BASE}/alimenti-in-scatola-e-confezionati/pasta-e-riso/pasta-di-semola/", wait_until="domcontentloaded")
    page.wait_for_timeout(3000)
    (OUT / "cat_pasta_after_select.html").write_text(page.content(), encoding="utf-8")
    header = page.locator(".cc-header__delivery")
    report["header_delivery_text"] = header.first.inner_text()[:300] if header.count() else None
    report["steps"].append("loaded pasta category after selection")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    log: list[dict] = []
    report: dict = {"steps": []}

    def on_response(response) -> None:
        request = response.request
        if "demandware.store" not in request.url:
            return
        entry = {
            "method": request.method,
            "url": request.url,
            "post_data": request.post_data,
            "request_headers": {k: v for k, v in request.headers.items() if k.lower() in ("content-type", "x-requested-with", "accept")},
            "status": response.status,
            "content_type": response.headers.get("content-type"),
        }
        if re.search(r"SelectStore|Delivery|CheckSelectedStore|CurrentStore", request.url):
            try:
                entry["body"] = response.text()[:5000]
            except Exception as exc:  # body may be gone after navigation
                entry["body_error"] = str(exc)
        log.append(entry)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(locale="it-IT")
        page = context.new_page()
        page.set_default_timeout(15000)
        page.on("response", on_response)
        try:
            run(page, report)
        except Exception as exc:
            report["error"] = f"{type(exc).__name__}: {str(exc)[:800]}"
            page.screenshot(path=str(OUT / "error.png"))
        finally:
            report["cookies"] = [{"name": c["name"], "domain": c["domain"]} for c in context.cookies()]
            browser.close()

    (OUT / "network.json").write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8")
    (OUT / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False)[:7000])
    print("---- store/delivery requests:")
    for e in log:
        if re.search(r"SelectStore|Delivery|CheckSelectedStore|CurrentStore", e["url"]):
            print(json.dumps({k: (v[:700] if isinstance(v, str) else v) for k, v in e.items()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
