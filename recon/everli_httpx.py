"""Everli (lidl.everli.com, robots.txt "Allow: /"): can the browser flow be reproduced with httpx?
Home (session cookie) -> /api/config with a public address (sets the address in the session) ->
/api/products for one category. Verifies the store id, like the Conad pointOfService check."""

import json
from urllib.parse import urlencode

from politehttp import ReconClient

BASE = "https://lidl.everli.com"
# the store's own public address (Lidl Ghezzano); the browser recon sent these fields
ADDRESS = {"brand": 50, "postal_code": "56010", "address": "Via Lorenzo il Magnifico", "street_number": "24",
           "city": "San Giuliano Terme", "country": "Italia", "lat": 43.7220276, "lng": 10.4281323}


def main() -> None:
    client = ReconClient("everli")
    home = client.get(f"{BASE}/", "httpx_home")
    print("home", home.status_code, "cookies:", sorted(client.client.cookies.keys()))
    config = client.get(f"{BASE}/api/config?{urlencode(ADDRESS)}", "httpx_config", headers={"Accept": "application/json"})
    store = (config.json().get("config") or {}).get("store") or {} if config.status_code == 200 else {}
    print("config", config.status_code, "store:", store.get("id"), store.get("address"), store.get("city"))
    products = client.get(f"{BASE}/api/products?tags=12&take=1000", "httpx_products_12", headers={"Accept": "application/json"})
    data = products.json() if products.headers.get("content-type", "").startswith("application/json") else {}
    print("products", products.status_code, len(data.get("data", [])), "links:", data.get("links"))
    if data.get("data"):
        print(json.dumps({k: data["data"][0].get(k) for k in ("name", "price", "store_id", "gdo_id")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
