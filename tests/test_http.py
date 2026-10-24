from pathlib import Path

import httpx
import pytest
import respx

from pisaprices.config.schemas import HttpSettings
from pisaprices.ingest.http import PoliteClient, RequestRefused
from pisaprices.ingest.snapshots import load_snapshots

HTTP = HttpSettings(min_delay_seconds=3, timeout_seconds=5, max_retries=3, user_agent="pisa-prices-tests")


class FakeTime:
    def __init__(self) -> None:
        self.t = 100.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


@pytest.fixture
def clock() -> FakeTime:
    return FakeTime()


@pytest.fixture
def client(tmp_path: Path, clock: FakeTime) -> PoliteClient:
    client = PoliteClient(
        source="test_source", run_id=7, raw_root=tmp_path, http=HTTP, allowed_hosts={"shop.test"},
        forbidden_params=("pmin", "prefn", "prefv"), forbidden_paths=("/static/private/",),
        now=lambda: "2026-10-23T10:00:00Z", sleep=clock.sleep, monotonic=clock.monotonic,
    )
    yield client
    client.close()


@respx.mock
def test_waits_min_delay_between_requests_to_same_host(client: PoliteClient, clock: FakeTime) -> None:
    respx.get("https://shop.test/a").respond(200, text="a")
    respx.get("https://shop.test/b").respond(200, text="b")
    client.get("https://shop.test/a", "a")
    clock.t += 1  # one second of parsing
    client.get("https://shop.test/b", "b")
    assert clock.sleeps == [2.0]
    assert client.requests == 2


@respx.mock
def test_retries_with_exponential_backoff_then_succeeds(client: PoliteClient, clock: FakeTime) -> None:
    route = respx.get("https://shop.test/flaky").mock(
        side_effect=[httpx.Response(503), httpx.Response(429), httpx.Response(200, text="ok")]
    )
    response, snapshot = client.get("https://shop.test/flaky", "flaky")
    assert response.status_code == 200 and snapshot.status == 200
    assert route.call_count == 3 and client.requests == 3
    assert clock.sleeps == [3, 6]  # backoff 3·2^0, 3·2^1; the min delay is already covered


@respx.mock
def test_gives_up_after_max_attempts_and_returns_last_response(client: PoliteClient) -> None:
    respx.get("https://shop.test/down").respond(503)
    response, _ = client.get("https://shop.test/down", "down")
    assert response.status_code == 503 and client.requests == 3


@respx.mock
def test_network_errors_are_retried_then_raised(client: PoliteClient) -> None:
    respx.get("https://shop.test/x").mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(httpx.ConnectError):
        client.get("https://shop.test/x", "x")
    assert client.requests == 3


@respx.mock
def test_client_errors_are_not_retried(client: PoliteClient) -> None:
    route = respx.get("https://shop.test/missing").respond(404)
    client.get("https://shop.test/missing", "missing")
    assert route.call_count == 1


@pytest.mark.parametrize(
    "url, params, reason",
    [
        ("https://other.test/page", None, "not allowed"),
        ("https://shop.test/grid", {"cgid": "pasta", "pmin": "0,01"}, "'pmin' is forbidden"),
        ("https://shop.test/grid?prefn1=excludeForDelivery", None, "'prefn1' is forbidden"),
        ("https://shop.test/static/private/app.js", None, "path"),
    ],
)
@respx.mock
def test_forbidden_requests_are_refused_before_sending(client: PoliteClient, url: str, params, reason: str) -> None:
    route = respx.route().respond(200)
    with pytest.raises(RequestRefused, match=reason):
        client.get(url, "refused", params=params)
    assert route.call_count == 0 and client.requests == 0


@respx.mock
def test_redirect_to_other_host_is_refused(client: PoliteClient) -> None:
    respx.get("https://shop.test/go").respond(302, headers={"Location": "https://other.test/landing"})
    respx.get("https://other.test/landing").respond(200)
    with pytest.raises(RequestRefused, match="other.test"):
        client.get("https://shop.test/go", "go")


@respx.mock
def test_snapshot_is_saved_with_metadata_and_user_agent(client: PoliteClient, tmp_path: Path) -> None:
    route = respx.get("https://shop.test/api").respond(200, json={"price": 1.33})
    client.get("https://shop.test/api", "price api", params={"cgid": "pasta"}, context={"key": "pasta"})
    assert route.calls.last.request.headers["user-agent"] == "pisa-prices-tests"

    [snapshot] = load_snapshots(tmp_path, "test_source", 7)
    assert snapshot.json() == {"price": 1.33}
    assert snapshot.path.name == "0001_price_api.json"
    assert snapshot.url == "https://shop.test/api?cgid=pasta"
    assert snapshot.context == {"key": "pasta"} and snapshot.fetched_at == "2026-10-23T10:00:00Z"
