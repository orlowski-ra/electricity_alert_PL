"""HTTP client shared by all providers.

- Every call returns an ``ApiResult``; no exception ever leaves this module.
- 5xx responses are retried for every host; 404 only for PGE hosts (``gkpge.pl``).

Why 404 is retried for PGE: PGE runs behind a load balancer with one broken node that answers
"404 page not found" to ~15–30% of requests. The node is pinned to the connection and, between
connections, to a cookie. So for PGE we (1) close the connection after every request, so a retry
lands on a new one, and (2) remember cookies only from successful responses and forget them after
a retryable error, which keeps us on a healthy node.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx

from .jsonutil import BadFormat, loads
from .models import ApiResult, FormatError, HttpError, NetworkError, Success

DEFAULT_USER_AGENT = "ElectricityAlertPL (CLI)"
PGE_DOMAIN = "gkpge.pl"


def is_pge_host(host: str) -> bool:
    return host == PGE_DOMAIN or host.endswith("." + PGE_DOMAIN)


class HttpClient:
    def __init__(
        self,
        transport: httpx.BaseTransport | None = None,
        timeout_seconds: float = 15.0,
        max_retries: int = 5,
        retry_delay_seconds: float = 0.2,
        user_agent: str = DEFAULT_USER_AGENT,
        retry_404: Callable[[str], bool] = is_pge_host,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = httpx.Client(
            transport=transport,
            timeout=timeout_seconds,
            headers={"User-Agent": user_agent},
            follow_redirects=True,
        )
        self._max_retries = max_retries
        self._delay = retry_delay_seconds
        self._retry_404 = retry_404
        self._sleep = sleep
        self._healthy_cookies: dict[str, str] = {}

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> HttpClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _should_retry(self, host: str, code: int) -> bool:
        return 500 <= code <= 599 or (code == 404 and self._retry_404(host))

    def request_text(self, method: str, url: str, **kwargs: Any) -> ApiResult[str]:
        """Sends a request; returns the body text on 2xx, otherwise an error result."""
        host = httpx.URL(url).host
        sticky = self._retry_404(host)
        attempt = 0
        while True:
            headers: dict[str, str] = {}
            if sticky:
                headers["Connection"] = "close"
                if host in self._healthy_cookies:
                    headers["Cookie"] = self._healthy_cookies[host]
            # Cookies are handled by hand (kept only from healthy responses), not by the client jar.
            self._client.cookies.clear()
            try:
                response = self._client.request(method, url, headers=headers, **kwargs)
            except httpx.HTTPError:
                return NetworkError()

            code = response.status_code
            retry = self._should_retry(host, code)
            if sticky:
                if response.is_success:
                    fresh = "; ".join(f"{c.name}={c.value}" for c in response.cookies.jar)
                    if fresh:
                        self._healthy_cookies[host] = fresh
                elif retry:
                    self._healthy_cookies.pop(host, None)

            if retry and attempt < self._max_retries:
                attempt += 1
                if self._delay > 0:
                    self._sleep(self._delay * attempt)
                continue
            if not response.is_success:
                return HttpError(code)
            try:
                return Success(response.text)
            except UnicodeDecodeError:
                return FormatError()

    def get_text(self, url: str, params: dict[str, Any] | None = None) -> ApiResult[str]:
        return self.request_text("GET", url, params=_without_none(params))

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> ApiResult[Any]:
        return decode_json(self.get_text(url, params))

    def post_json(self, url: str, body: Any) -> ApiResult[Any]:
        return decode_json(self.request_text("POST", url, json=body))


def _without_none(params: dict[str, Any] | None) -> dict[str, Any] | None:
    if params is None:
        return None
    return {k: v for k, v in params.items() if v is not None}


def decode_json(result: ApiResult[str]) -> ApiResult[Any]:
    if not isinstance(result, Success):
        return result
    try:
        return Success(loads(result.data))
    except BadFormat:
        return FormatError()


def map_success(result: ApiResult[Any], transform: Callable[[Any], Any]) -> ApiResult[Any]:
    """Applies ``transform`` to the data of a Success; a BadFormat while mapping -> FormatError."""
    if not isinstance(result, Success):
        return result
    try:
        return Success(transform(result.data))
    except BadFormat:
        return FormatError()
