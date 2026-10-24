"""T0.2: download robots.txt for every source host, politely, with the configured User-Agent."""

import time
from pathlib import Path

import httpx
from ruamel.yaml import YAML

HOSTS = [
    "prenotalaspesa.coopfirenze.it",
    "www.coopfirenze.it",
    "spesaonline.conad.it",
    "www.conad.it",
    "www.lidl.it",
    "www.eurospin.com",
]

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "recon" / "out" / "robots"


def main() -> None:
    settings = YAML(typ="safe").load((ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))
    http = settings["http"]
    OUT.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": http["user_agent"]}
    with httpx.Client(headers=headers, timeout=http["timeout_seconds"], follow_redirects=True) as client:
        for host in HOSTS:
            url = f"https://{host}/robots.txt"
            try:
                response = client.get(url)
                body = response.text
                status = f"{response.status_code} final_url={response.url}"
            except httpx.HTTPError as exc:
                body = ""
                status = f"error {type(exc).__name__}: {exc}"
            (OUT / f"{host}.txt").write_text(f"# {url}\n# status: {status}\n{body}", encoding="utf-8")
            print(f"{host}: {status} ({len(body)} chars)")
            time.sleep(http["min_delay_seconds"])


if __name__ == "__main__":
    main()
