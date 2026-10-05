import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

# Make `src` importable when running `pytest module-3-job-matching-salary/tests/` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

MOCKS = Path(__file__).parent / "mocks"
NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)


def load_mock(name: str) -> dict:
    return json.loads((MOCKS / name).read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def offline(monkeypatch, request):
    """Fake provider keys, and no real network: module-level httpx calls fail loudly.
    Tests pass an httpx.Client with a MockTransport instead. Live tests opt out with @pytest.mark.live."""
    if request.node.get_closest_marker("live"):
        return
    monkeypatch.setenv("ADZUNA_APP_ID", "test-id")
    monkeypatch.setenv("ADZUNA_APP_KEY", "test-key")
    monkeypatch.setenv("RAPIDAPI_KEY", "test-rapid")
    monkeypatch.setenv("LIVE_CACHE_TTL_HOURS", "6")

    def blocked(*_a, **_k):
        raise AssertionError("real network call in an offline test")
    monkeypatch.setattr(httpx, "get", blocked)
    monkeypatch.setattr(httpx, "post", blocked)


@pytest.fixture
def store(tmp_path):
    from src.engines.job_store import JobStore
    return JobStore(tmp_path / "jobs.sqlite")


class FakeHttp:
    """Routes requests by host to handlers; records every request."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.handlers: dict[str, object] = {}

    def on(self, host: str, handler) -> "FakeHttp":
        self.handlers[host] = handler
        return self

    def client(self) -> httpx.Client:
        def route(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            handler = self.handlers.get(request.url.host)
            if handler is None:
                raise httpx.ConnectError("no route", request=request)
            return handler(request)
        return httpx.Client(transport=httpx.MockTransport(route))

    def calls(self, host: str) -> int:
        return sum(1 for r in self.requests if r.url.host == host)


def json_response(payload, status=200):
    return lambda request: httpx.Response(status, json=payload)


def raises(exc_type):
    def handler(request):
        raise exc_type("simulated", request=request)
    return handler


def m2_extract_handler(request: httpx.Request) -> httpx.Response:
    """Stand-in for module 2's /skills/extract: a few keyword hits, like the real dictionary pass."""
    text = json.loads(request.content)["text"].lower()
    known = {"python": "python", "sql": "sql", "machine learning": "machine_learning", "aws": "aws",
             "power bi": "power_bi", "excel": "excel", "pytorch": "pytorch", "kubernetes": "kubernetes",
             "data analysis": "data_analysis", "statistical": "statistics"}
    return httpx.Response(200, json={"skills": [{"id": v} for k, v in known.items() if k in text], "warnings": []})


@pytest.fixture
def fake_http():
    return FakeHttp()


def pytest_configure(config):
    config.addinivalue_line("markers", "live: calls a real provider; skipped when its key is missing")
