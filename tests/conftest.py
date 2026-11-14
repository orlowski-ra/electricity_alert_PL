from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import httpx
import pytest

from electricity_alert.http import HttpClient
from electricity_alert.models import TimeWindow

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def fixture_json(name: str) -> Any:
    return json.loads(fixture_text(name))


def window_on(day: date) -> TimeWindow:
    return TimeWindow(datetime.combine(day, time(0, 0)), datetime.combine(day, time(23, 59, 59)))


class FakeServer:
    """Records requests and answers them with queued or routed responses."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.queue: list[httpx.Response] = []
        self.route: Callable[[httpx.Request], httpx.Response] | None = None

    def enqueue(self, body: str | bytes = "", status: int = 200, headers: dict[str, str] | None = None) -> None:
        self.queue.append(httpx.Response(status, content=body, headers=headers))

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.route is not None:
            return self.route(request)
        if not self.queue:
            return httpx.Response(500, content=b"no response queued")
        return self.queue.pop(0)

    def client(self, **kwargs: Any) -> HttpClient:
        kwargs.setdefault("retry_delay_seconds", 0)
        return HttpClient(transport=httpx.MockTransport(self.handle), **kwargs)


@pytest.fixture
def server() -> FakeServer:
    return FakeServer()
