from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
REAL_FIXTURES = TESTS / "fixtures"
SYNTHETIC = TESTS / "synthetic"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "real_fixtures: needs local pages saved from the stores (not in git)")


@pytest.fixture
def real_fixture():
    """Path to a local real fixture; skips the test when it is missing (fresh clone, CI)."""

    def get(relative: str) -> Path:
        path = REAL_FIXTURES / relative
        if not path.exists():
            pytest.skip(f"real fixture missing: {relative} (run `pisaprices fixtures capture`)")
        return path

    return get


@pytest.fixture
def synthetic_fixture():
    def get(relative: str) -> Path:
        return SYNTHETIC / relative

    return get
