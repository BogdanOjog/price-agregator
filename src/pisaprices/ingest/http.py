"""The only way collectors talk to the network.

Every request: host on the source's allow-list, no forbidden parameter or path (robots.txt),
at least `min_delay_seconds` after the previous request to the same host, the configured
User-Agent, retries with exponential backoff, counted, and saved to disk before anyone parses it."""

import time
from collections.abc import Callable, Iterable
from pathlib import Path

import httpx

from pisaprices.config.schemas import HttpSettings
from pisaprices.ingest.snapshots import RawSnapshot, save_snapshot

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class RequestRefused(Exception):
    """The request would break a rule (host not allowed, robots.txt); nothing was sent."""


class PoliteClient:
    def __init__(
        self,
        *,
        source: str,
        run_id: int,
        raw_root: Path,
        http: HttpSettings,
        allowed_hosts: Iterable[str],
        now: Callable[[], str],
        forbidden_params: Iterable[str] = (),
        forbidden_paths: Iterable[str] = (),
        cookies: dict[str, str] | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.source = source
        self.run_id = run_id
        self.raw_root = raw_root
        self.settings = http
        self.allowed_hosts = frozenset(allowed_hosts)
        self.forbidden_params = tuple(forbidden_params)
        self.forbidden_paths = tuple(forbidden_paths)
        self.now = now
        self.sleep = sleep
        self.monotonic = monotonic
        self.requests = 0
        self.phase = "data"   # collect() sets "context" while the collector checks the session
        self.snapshots: list[RawSnapshot] = []
        self._last_request: dict[str, float] = {}
        self._client = httpx.Client(
            headers={"User-Agent": http.user_agent},
            timeout=http.timeout_seconds,
            follow_redirects=True,
            cookies=cookies,
            transport=transport,
        )

    def __enter__(self) -> "PoliteClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _refuse_if_forbidden(self, url: httpx.URL) -> None:
        if url.host not in self.allowed_hosts:
            raise RequestRefused(f"{self.source}: host {url.host} is not allowed ({url})")
        for name in url.params:
            if name.startswith(self.forbidden_params):
                raise RequestRefused(f"{self.source}: parameter '{name}' is forbidden by robots.txt ({url})")
        if url.path.startswith(self.forbidden_paths):
            raise RequestRefused(f"{self.source}: path {url.path} is forbidden by robots.txt")

    def _wait_turn(self, host: str) -> None:
        last = self._last_request.get(host)
        if last is not None:
            remaining = self.settings.min_delay_seconds - (self.monotonic() - last)
            if remaining > 0:
                self.sleep(remaining)

    def get(self, url: str, label: str, *, params=None, headers=None, context: dict | None = None
            ) -> tuple[httpx.Response, RawSnapshot]:
        request = self._client.build_request("GET", url, params=params, headers=headers)
        self._refuse_if_forbidden(request.url)
        host = request.url.host

        response, error = None, None
        attempts = max(1, self.settings.max_retries)
        for attempt in range(1, attempts + 1):
            self._wait_turn(host)
            try:
                response, error = self._client.send(request), None
            except httpx.TransportError as exc:
                response, error = None, exc
            self._last_request[host] = self.monotonic()
            self.requests += 1
            if response is not None and response.status_code not in RETRYABLE_STATUS:
                break
            if attempt < attempts:
                self.sleep(self.settings.min_delay_seconds * 2 ** (attempt - 1))
        if response is None:
            raise error

        for hop in [*response.history, response]:
            self._refuse_if_forbidden(hop.url)

        snapshot = save_snapshot(
            self.raw_root, self.source, self.run_id, len(self.snapshots) + 1, label, str(response.url),
            response.status_code, response.headers.get("content-type"), response.content, self.now(), context,
            self.phase,
        )
        self.snapshots.append(snapshot)
        return response, snapshot
