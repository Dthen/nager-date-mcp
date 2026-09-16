"""HTTP client for the Nager.Date API with an in-memory TTL cache."""

from __future__ import annotations

import copy
import time
from typing import Any

from nager_date_mcp.transport import SeamResponse, fetch_raw

BASE_URL = "https://date.nager.at/api/v3"

# Common TTL presets (seconds).
TTL_HOUR = 3600
TTL_DAY = 24 * 3600
TTL_WEEK = 7 * 24 * 3600

# Maximum number of entries retained in the in-memory cache. When the cache
# exceeds this size after an insert, the oldest entries (by timestamp) are
# evicted so a long-running server cannot grow the cache without bound.
MAX_CACHE_SIZE = 256


def _normalize_country_code(code: str) -> str:
    """Normalize an ISO 3166-1 alpha-2 country code to uppercase."""
    if not isinstance(code, str) or not code.strip():
        raise ValueError("Country code must be a non-empty string")
    return code.strip().upper()


def _extract_error_message(response: SeamResponse) -> str:
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
    """Thin wrapper around the public Nager.Date REST API.

    Sync over the stdlib transport seam (``transport.fetch_raw``): the
    server runs a single-threaded stdio loop, so there is nothing to
    overlap (PLAN D5 — the legacy coroutines are dead code, deleted not
    ported). Transport errors (URLError/HTTPError) propagate uncaught
    here by design: the legacy client had no transport-level ``except``
    (R2 fact 1) and the friendly fold lives at the server handler layer
    (R2 fact 2).

    Responses are cached in-memory keyed by request path, with a
    per-call configurable TTL. The cache is a plain dict mapping
    ``cache_key -> (timestamp, data)``.
    """

    def __init__(
        self,
        base_url: str = BASE_URL,
        default_ttl: int = TTL_HOUR,
    ) -> None:
        self.base_url = base_url
        self.default_ttl = default_ttl
        # cache_key -> (timestamp, data)
        self._cache: dict[str, tuple[float, Any]] = {}

    def clear_cache(self) -> None:
        self._cache.clear()

    def get(self, path: str, cache_ttl: int | None = None) -> Any:
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
                    # Return a deep copy so callers cannot mutate the cached
                    # data (e.g. server-side enrichment adding fields in place).
                    return copy.deepcopy(data)

        # Literal `base_url + path` concat — urljoin would drop the /api/v3
        # prefix (seam fact, _chain.md; transport.fetch_raw docstring).
        response = fetch_raw(self.base_url + path)
        data = self._handle_response(response)

        if ttl > 0:
            self._cache[cache_key] = (now, data)
            self._evict_if_needed()
        # Return a copy of freshly-fetched data too, for a consistent contract.
        return copy.deepcopy(data)

    def _evict_if_needed(self) -> None:
        """Drop the oldest cache entries when the cache exceeds MAX_CACHE_SIZE."""
        if len(self._cache) <= MAX_CACHE_SIZE:
            return
        # Order keys by timestamp (oldest first) and remove the excess.
        ordered = sorted(self._cache, key=lambda k: self._cache[k][0])
        excess = len(self._cache) - MAX_CACHE_SIZE
        for key in ordered[:excess]:
            del self._cache[key]

    @staticmethod
    def _handle_response(response: SeamResponse) -> Any:
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

    def get_holidays(self, year: int, country_code: str) -> list[dict]:
        code = _normalize_country_code(country_code)
        return self.get(f"/PublicHolidays/{year}/{code}", cache_ttl=TTL_DAY)

    def get_next_holidays(self, country_code: str) -> list[dict]:
        code = _normalize_country_code(country_code)
        return self.get(f"/NextPublicHolidays/{code}", cache_ttl=TTL_HOUR)

    def get_worldwide_holidays(self) -> list[dict]:
        return self.get("/NextPublicHolidaysWorldwide", cache_ttl=TTL_HOUR)

    def get_country_info(self, country_code: str) -> dict:
        code = _normalize_country_code(country_code)
        return self.get(f"/CountryInfo/{code}", cache_ttl=TTL_WEEK)

    def get_available_countries(self) -> list[dict]:
        return self.get("/AvailableCountries", cache_ttl=TTL_WEEK)

    def get_long_weekends(self, year: int, country_code: str) -> list[dict]:
        code = _normalize_country_code(country_code)
        return self.get(f"/LongWeekend/{year}/{code}", cache_ttl=TTL_DAY)

    def is_today_holiday(
        self,
        country_code: str,
        county_code: str | None = None,
        offset: int | None = None,
    ) -> bool | None:
        code = _normalize_country_code(country_code)
        params: dict[str, Any] = {}
        if county_code is not None:
            params["countyCode"] = county_code
        if offset is not None:
            params["offset"] = offset
        query = "&".join(f"{k}={v}" for k, v in params.items())
        path = f"/IsTodayPublicHoliday/{code}" + (f"?{query}" if query else "")
        # This endpoint returns 200 with an EMPTY body when today is a holiday,
        # and 204 (no content) otherwise. Do NOT parse the body — decide purely
        # on the status code. (cache_ttl=0: holiday status is volatile.)
        #
        # The 204 is ambiguous: the API returns it BOTH for an unsupported
        # country (no data, e.g. IN/SA/AE/TH) AND for a supported country where
        # today simply isn't a holiday. The client cannot tell these apart, so
        # it reports the raw signal and lets the caller disambiguate:
        #   - True  -> 200, today IS a holiday
        #   - None  -> 204, ambiguous (no-data OR not-a-holiday)
        # A genuine 404/400/5xx still raises via _handle_response, so callers
        # never see those collapsed into None.
        response = fetch_raw(self.base_url + path)
        if response.status_code == 200:
            return True
        if response.status_code == 204:
            return None
        # Any other status (404, 400, 5xx, ...) goes through the normal mapper.
        self._handle_response(response)
        return None  # unreachable; _handle_response raises for non-2xx
