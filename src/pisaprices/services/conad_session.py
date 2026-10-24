"""The Conad session: imported from a "Copy as cURL" text the user copies in a browser, checked
without collecting. The store selection itself is never automated (anti-bot protection)."""

import shlex
from pathlib import Path

from pisaprices.config.schemas import StoreConfig
from pisaprices.domain.enums import Chain
from pisaprices.ingest.base import ContextError
from pisaprices.ingest.http import PoliteClient
from pisaprices.ingest.sources.conad_catalog import SESSION_COOKIES, ConadCatalog
from pisaprices.services.clock import iso_utc
from pisaprices.services.collect import Runtime

SESSION_FILE = Path("data") / "conad_session.txt"


def cookies_from_curl(text: str) -> dict[str, str]:
    """Cookies of a cURL command (-b '...' or -H 'cookie: ...'), as copied from browser dev tools."""
    try:
        tokens = shlex.split(text.replace("\\\n", " ").replace("^", ""), posix=True)
    except ValueError as exc:
        raise ValueError(f"not a cURL command: {exc}") from exc
    raw = ""
    for i, token in enumerate(tokens[:-1]):
        nxt = tokens[i + 1]
        if token in ("-b", "--cookie"):
            raw = nxt
        elif token in ("-H", "--header") and nxt.lower().startswith("cookie:"):
            raw = nxt.split(":", 1)[1]
    if not raw:
        raise ValueError("no cookies in the cURL text: copy the request of a page (Doc), not of an image or script")
    cookies = {}
    for part in raw.split(";"):
        if "=" in part:
            name, value = part.strip().split("=", 1)
            cookies[name.strip()] = value.strip().strip('"')
    return cookies


def import_session(root: Path, curl_text: str) -> Path:
    """Keep only the session cookies and save them where the Conad collector reads them."""
    cookies = {k: v for k, v in cookies_from_curl(curl_text).items() if k in SESSION_COOKIES}
    if "ecAccess" not in cookies:
        raise ValueError("the copied request has no 'ecAccess' cookie: choose 'Ordina e Ritira' and the store first, "
                         "then open a category page and copy that request")
    path = root / SESSION_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("; ".join(f"{k}={v}" for k, v in cookies.items()) + "\n", encoding="utf-8")
    return path


def conad_store(runtime: Runtime) -> StoreConfig:
    return next(s for s in runtime.config.stores.stores if s.chain is Chain.CONAD)


def check_session(runtime: Runtime) -> tuple[bool, str]:
    """One request: is the saved session still on the configured store?"""
    store = conad_store(runtime)
    try:
        collector = ConadCatalog(store, None, 0, runtime.root / SESSION_FILE)
    except (FileNotFoundError, ValueError) as exc:
        return False, str(exc)
    client = PoliteClient(
        source="conad_session_check", run_id=0, raw_root=runtime.raw_root, http=runtime.config.settings.http,
        allowed_hosts=collector.allowed_hosts, cookies=collector.cookies(), transport=runtime.transport,
        now=lambda: iso_utc(runtime.clock.now()), **({"sleep": runtime.sleep} if runtime.sleep else {}),
    )
    with client:
        client.phase = "context"
        try:
            collector.verify_context(client)
        except ContextError as exc:
            return False, str(exc)
    return True, f"Conad session is on store {store.external_id}"

