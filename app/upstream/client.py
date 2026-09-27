"""Async Clean APIs client — isolated upstream layer (M1).

Only this module imports httpx for upstream HTTP.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from app.core.masking import mask_api_key
from app.upstream.errors import UpstreamError, map_status_to_error
from app.upstream.headers import RateLimitState, parse_rate_limit_headers


class UpstreamResponse:
    """Container for upstream result + metadata."""

    def __init__(
        self,
        status_code: int,
        body: Any,
        headers: RateLimitState,
        raw_headers: httpx.Headers | dict[str, str],
        latency_ms: int,
    ) -> None:
        self.status_code = status_code
        self.body = body
        self.rate_limit = headers
        self.raw_headers = raw_headers
        self.latency_ms = latency_ms


class CleanApisClient:
    """Minimal async client for https://cleanapis.com/v1.

    - Never logs raw API key.
    - Captures latency, status, rate-limit headers.
    - Maps errors to normalized UpstreamError.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str | None,
        timeout_seconds: float = 600,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.transport = transport
        # Masked for diagnostics only
        self.masked_key = mask_api_key(api_key) if api_key else "****"

    def _headers(self) -> dict[str, str]:
        h: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    async def list_models(self) -> UpstreamResponse:
        url = f"{self.base_url}/models"
        return await self._get(url)

    async def chat_completion(self, payload: dict[str, Any]) -> UpstreamResponse:
        url = f"{self.base_url}/chat/completions"
        return await self._post(url, payload)

    # ---- internal helpers ----

    async def _get(self, url: str) -> UpstreamResponse:
        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout_seconds),
                transport=self.transport,
            ) as client:
                resp = await client.get(url, headers=self._headers())
        except httpx.TimeoutException as exc:
            raise UpstreamError(
                code="UPSTREAM_TIMEOUT",
                message="Upstream request timed out",
                status_code=504,
                upstream_status=None,
            ) from exc
        except httpx.RequestError as exc:
            raise UpstreamError(
                code="UPSTREAM_UNAVAILABLE",
                message="Upstream unavailable",
                status_code=502,
                upstream_status=None,
            ) from exc

        latency_ms = int((time.perf_counter() - start) * 1000)
        rate_limit = parse_rate_limit_headers(resp.headers)
        retry_after = rate_limit.retry_after

        # Try to parse JSON body
        body: Any
        try:
            body = resp.json()
        except Exception:
            body = resp.text[:2000] if resp.text else None

        if resp.status_code >= 400:
            # Never include api key in error
            raise map_status_to_error(resp.status_code, body, retry_after)

        return UpstreamResponse(
            status_code=resp.status_code,
            body=body,
            headers=rate_limit,
            raw_headers=resp.headers,
            latency_ms=latency_ms,
        )

    async def _post(self, url: str, payload: dict[str, Any]) -> UpstreamResponse:
        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout_seconds),
                transport=self.transport,
            ) as client:
                resp = await client.post(url, headers=self._headers(), json=payload)
        except httpx.TimeoutException as exc:
            raise UpstreamError(
                code="UPSTREAM_TIMEOUT",
                message="Upstream request timed out",
                status_code=504,
                upstream_status=None,
            ) from exc
        except httpx.RequestError as exc:
            raise UpstreamError(
                code="UPSTREAM_UNAVAILABLE",
                message="Upstream unavailable",
                status_code=502,
                upstream_status=None,
            ) from exc

        latency_ms = int((time.perf_counter() - start) * 1000)
        rate_limit = parse_rate_limit_headers(resp.headers)
        retry_after = rate_limit.retry_after

        body: Any
        try:
            body = resp.json()
        except Exception:
            body = resp.text[:2000] if resp.text else None

        if resp.status_code >= 400:
            raise map_status_to_error(resp.status_code, body, retry_after)

        return UpstreamResponse(
            status_code=resp.status_code,
            body=body,
            headers=rate_limit,
            raw_headers=resp.headers,
            latency_ms=latency_ms,
        )
