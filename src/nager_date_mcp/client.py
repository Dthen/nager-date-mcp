"""Async HTTP client for the Nager.Date API with an in-memory TTL cache."""

from __future__ import annotations

import time
from typing import Any

import httpx

BASE_URL = "https://date.nager.at/api/v3"

# Common TTL presets (seconds).
TTL_HOUR = 3600
TTL_DAY = 24 * 3600
TTL_WEEK = 7 * 24 * 3600


def _normalize_country_code(code: str) -> str:
    """Normalize an ISO 3166-1 alpha-2 country code to uppercase."""
    if not isinstance(code, str) or not code.strip():
        raise ValueError("Country code must be a non-empty string")
    return code.strip().upper()


def _extract_error_message(response: httpx.Response) -> str:
    """Extract a human-readable message from an API error response body.

    The Nager.Date API returns errors like:
        {"errors": {"year": ["The specified year '1975' is not supported."]}}
    This helper flattens that structure into a single clean message.
    Falls back to the raw response text if parsing fails.
    """
    raw = response.text.strip()
    if not raw:
        return "Bad request"
    try:
        body = response.json()
    except (ValueError, TypeError):
        return raw
    # Handle {"errors": {"field": ["msg1", "msg2"], ...}} shape.
    if isinstance(body, dict) and isinstance(body.get("errors"), dict):
        messages: list[str] = []
        for field_errors in body["errors"].values():
            if isinstance(field_errors, list):
                messages.extend(str(m) for m in field_errors)
            else:
                messages.append(str(field_errors))
        if messages:
            return "; ".join(messages)
    # Handle {"title": "..."} or {"message": "..."} shapes.
    if isinstance(body, dict):
        for key in ("message", "title", "detail"):
            if isinstance(body.get(key), str) and body[key].strip():
                return body[key].strip()
    return raw


class NagerDateClient:
    """Thin async wrapper around the public Nager.Date REST API.

    Responses are cached in-memory keyed by request path, with a
    per-call configurable TTL. The cache is a plain dict mapping
    ``cache_key -> (timestamp, data)``.
    """

    def __init__(
        self,
        base_url: str = BASE_URL,
        client: httpx.AsyncClient | None = None,
        default_ttl: int = TTL_HOUR,
    ) -> None:
        self.base_url = base_url
        self.default_ttl = default_ttl
        self._client = client or httpx.AsyncClient(base_url=base_url)
        self._owns_client = client is None
        # cache_key -> (timestamp, data)
        self._cache: dict[str, tuple[float, Any]] = {}

    async def __aenter__(self) -> "NagerDateClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def clear_cache(self) -> None:
        self._cache.clear()

    async def get(self, path: str, cache_ttl: int | None = None) -> Any:
        """GET ``path`` (relative to base URL), returning cached data when fresh.

        ``cache_ttl`` overrides the instance default for this call. Pass
        ``cache_ttl=0`` to bypass the cache entirely.
        """
        ttl = self.default_ttl if cache_ttl is None else cache_ttl
        cache_key = path
        now = time.monotonic()

        if ttl > 0:
            cached = self._cache.get(cache_key)
            if cached is not None:
                ts, data = cached
                if now - ts < ttl:
                    return data

        response = await self._client.get(path)
        data = self._handle_response(response)

        if ttl > 0:
            self._cache[cache_key] = (now, data)
        return data

    @staticmethod
    def _handle_response(response: httpx.Response) -> Any:
        """Map HTTP status codes to return values / exceptions."""
        if response.status_code == 204:
            return None
        if response.status_code == 404:
            # Surface a country code from the URL when present.
            segments = [s for s in response.request.url.path.split("/") if s]
            code = next(
                (s for s in segments if len(s) == 2 and s.isalpha()), None
            )
            if code:
                raise ValueError(f"Country not found: {code}")
            raise ValueError(f"Not found: {response.request.url.path}")
        if response.status_code == 400:
            detail = _extract_error_message(response)
            raise ValueError(f"Bad request: {detail}")
        response.raise_for_status()
        return response.json()

    # -- Endpoint wrappers -------------------------------------------------

    async def get_holidays(self, year: int, country_code: str) -> list[dict]:
        code = _normalize_country_code(country_code)
        return await self.get(f"/PublicHolidays/{year}/{code}", cache_ttl=TTL_DAY)

    async def get_next_holidays(self, country_code: str) -> list[dict]:
        code = _normalize_country_code(country_code)
        return await self.get(f"/NextPublicHolidays/{code}", cache_ttl=TTL_HOUR)

    async def get_worldwide_holidays(self) -> list[dict]:
        return await self.get("/NextPublicHolidaysWorldwide", cache_ttl=TTL_HOUR)

    async def get_country_info(self, country_code: str) -> dict:
        code = _normalize_country_code(country_code)
        return await self.get(f"/CountryInfo/{code}", cache_ttl=TTL_WEEK)

    async def get_available_countries(self) -> list[dict]:
        return await self.get("/AvailableCountries", cache_ttl=TTL_WEEK)

    async def get_long_weekends(self, year: int, country_code: str) -> list[dict]:
        code = _normalize_country_code(country_code)
        return await self.get(f"/LongWeekend/{year}/{code}", cache_ttl=TTL_DAY)

    async def is_today_holiday(
        self,
        country_code: str,
        county_code: str | None = None,
        offset: int | None = None,
    ) -> bool:
        code = _normalize_country_code(country_code)
        params: dict[str, Any] = {}
        if county_code is not None:
            params["countyCode"] = county_code
        if offset is not None:
            params["offset"] = offset
        query = "&".join(f"{k}={v}" for k, v in params.items())
        path = f"/IsTodayPublicHoliday/{code}" + (f"?{query}" if query else "")
        # This endpoint returns 200 with an EMPTY body when today is a holiday,
        # and 204 (no content) when it is not. Do NOT parse the body — decide
        # purely on the status code. (cache_ttl=0: holiday status is volatile.)
        response = await self._client.get(path)
        if response.status_code == 200:
            return True
        if response.status_code == 204:
            return False
        # Any other status (404, 400, 5xx, ...) goes through the normal mapper.
        self._handle_response(response)
        return False  # unreachable; _handle_response raises for non-2xx
