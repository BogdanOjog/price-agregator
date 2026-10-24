"""Polite recon client: configured User-Agent, per-host delay, every response saved to disk."""

import json
import re
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
from ruamel.yaml import YAML

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "recon" / "out"


class ReconClient:
    def __init__(self, source: str, cookies: dict[str, str] | None = None) -> None:
        settings = YAML(typ="safe").load((ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))
        http = settings["http"]
        self.delay = http["min_delay_seconds"]
        self.dir = OUT / source / "raw"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.client = httpx.Client(
            headers={"User-Agent": http["user_agent"]},
            timeout=http["timeout_seconds"],
            follow_redirects=True,
            cookies=cookies,
        )
        self._last: dict[str, float] = {}
        self.count = len(list(self.dir.glob("*.meta.json")))

    def get(self, url: str, label: str, **kwargs) -> httpx.Response:
        host = urlparse(url).netloc
        wait = self.delay - (time.monotonic() - self._last.get(host, 0.0))
        if wait > 0:
            time.sleep(wait)
        response = self.client.get(url, **kwargs)
        self._last[host] = time.monotonic()
        self.count += 1
        name = f"{self.count:03d}_{re.sub(r'[^A-Za-z0-9_-]+', '_', label)[:60]}"
        ext = "json" if "json" in response.headers.get("content-type", "") else "html"
        (self.dir / f"{name}.{ext}").write_bytes(response.content)
        meta = {
            "url": url,
            "final_url": str(response.url),
            "status": response.status_code,
            "params": kwargs.get("params"),
            "content_type": response.headers.get("content-type"),
            "set_cookie": response.headers.get_list("set-cookie"),
            "cookies_sent": dict(self.client.cookies),
        }
        (self.dir / f"{name}.meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[{self.count}] {response.status_code} {url} -> {name}.{ext} ({len(response.content)} B)")
        return response

    def close(self) -> None:
        self.client.close()
