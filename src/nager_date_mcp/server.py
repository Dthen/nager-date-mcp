"""FastMCP server exposing Nager.Date public-holiday endpoints as MCP tools."""

from __future__ import annotations

from datetime import date, datetime

import httpx
from mcp.server.fastmcp import FastMCP

from nager_date_mcp.client import NagerDateClient

mcp = FastMCP(
    "nager-date",
    instructions="Public holidays for 200+ countries via Nager.Date API",
)

# Single shared client instance (module-level).
_client = NagerDateClient()


def _day_of_week(date_str: str) -> str:
    """Compute the weekday name (e.g. 'Monday') from an ISO date string."""
    d = datetime.strptime(date_str, "%Y-%m-%d").date()
    return d.strftime("%A")


def _enrich_holidays(holidays: list[dict] | None) -> list[dict]:
    """Add a 'day_of_week' field to each holiday dict.

    Defensive: a None or empty input (e.g. the API returned 204 No Content
    for an unsupported country) yields an empty list rather than raising.
    """
    if not holidays:
        return []
    for h in holidays:
        if "date" in h:
            h["day_of_week"] = _day_of_week(h["date"])
    return holidays


# ---------------------------------------------------------------------------
# Core tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_holidays(
    country: str,
    year: int | None = None,
    type: str | None = None,
    county: str | None = None,
) -> list[dict] | dict | str:
    """Get all public holidays for a country in a given year.

    Args:
        country: ISO 3166-1 alpha-2 country code (e.g. "US", "GB", "DE").
        year: Year to query (1976–2076). Defaults to the current year.
        type: Filter by holiday type (e.g. "Public", "Bank", "Observance"). Case-insensitive.
        county: Filter by ISO 3166-2 subdivision code (e.g. "GB-ENG", "US-CA").

    Returns a list of holiday objects, each enriched with a day_of_week field.
    When a filter produces a misleadingly empty/partial result, returns a dict
    {"holidays": [...], "note": "..."} explaining why. For a country the API
    has no data for, returns a friendly message string.
    """
    try:
        code = country.strip().upper()
        y = year if year is not None else date.today().year
        holidays = await _client.get_holidays(y, code)

        # The API returns 204 No Content (mapped to None) for valid-format
        # country codes it has no data for (e.g. IN, SA, AE, TH). Handle
        # gracefully instead of crashing.
        if holidays is None:
            return f"No holiday data available for {code}."

        note: str | None = None

        if type is not None:
            type_lower = type.lower()
            filtered = [
                h
                for h in holidays
                if any(type_lower == t.lower() for t in h.get("types", []))
            ]
            if not filtered and holidays:
                note = (
                    f"note: no holidays matched type '{type}' for {code} "
                    f"(this country may use a different type label)"
                )
            holidays = filtered

        if county is not None:
            county_upper = county.strip().upper()
            filtered = [
                h
                for h in holidays
                if h.get("global")
                or not h.get("counties")
                or county_upper in h["counties"]
            ]
            # Warn if the county matched no regional holiday (only globals
            # survived) — a typo'd county otherwise looks like a full answer.
            regional_match = any(
                county_upper in (h.get("counties") or []) for h in filtered
            )
            if not regional_match:
                county_note = (
                    f"note: no regional holidays found for county '{county_upper}'; "
                    f"showing national holidays only"
                )
                note = f"{note}; {county_note}" if note else county_note
            holidays = filtered

        enriched = _enrich_holidays(holidays)
        if note is not None:
            return {"holidays": enriched, "note": note}
        return enriched
    except ValueError as e:
        return f"Error: {e}"
    except httpx.HTTPError as e:
        return f"Error: API request failed — {e}"


@mcp.tool()
async def get_next_holidays(
    country: str,
    county: str | None = None,
) -> list[dict] | str:
    """Get the upcoming public holidays for a country (from today forward).

    Args:
        country: ISO 3166-1 alpha-2 country code (e.g. "US", "GB", "JP").
        county: Optional ISO 3166-2 subdivision code to filter regional holidays.

    Returns a list of upcoming holiday objects, each enriched with day_of_week.
    """
    try:
        code = country.strip().upper()
        holidays = await _client.get_next_holidays(code)

        # 204 No Content (mapped to None) for unsupported countries.
        if holidays is None:
            return f"No holiday data available for {code}."

        if county is not None:
            county_upper = county.strip().upper()
            holidays = [
                h
                for h in holidays
                if h.get("global")
                or not h.get("counties")
                or county_upper in h["counties"]
            ]

        return _enrich_holidays(holidays)
    except ValueError as e:
        return f"Error: {e}"
    except httpx.HTTPError as e:
        return f"Error: API request failed — {e}"


# ---------------------------------------------------------------------------
# Planning tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_long_weekends(
    country: str,
    year: int | None = None,
) -> list[dict] | str:
    """Get all long weekends for a country in a given year.

    A long weekend occurs when a holiday falls adjacent to a weekend,
    creating an extended break. Includes bridge-day information.

    Args:
        country: ISO 3166-1 alpha-2 country code (e.g. "US", "GB").
        year: Year to query (1976–2076). Defaults to the current year.

    Returns a list of long weekend objects with startDate, endDate,
    dayCount, needBridgeDay, and bridgeDays fields.
    """
    try:
        code = country.strip().upper()
        y = year if year is not None else date.today().year
        return await _client.get_long_weekends(y, code)
    except ValueError as e:
        return f"Error: {e}"
    except httpx.HTTPError as e:
        return f"Error: API request failed — {e}"


@mcp.tool()
async def is_today_holiday(
    country: str,
    county: str | None = None,
    offset: int | None = None,
) -> dict | str:
    """Check whether today is a public holiday in a given country.

    Args:
        country: ISO 3166-1 alpha-2 country code (e.g. "US", "PE").
        county: Optional ISO 3166-2 subdivision code for regional check.
        offset: UTC timezone offset for determining 'today', e.g. -5 for
            US Eastern. Defaults to UTC.

    Returns {"is_holiday": true/false, "country": "<CODE>"}.
    """
    try:
        code = country.strip().upper()
        result = await _client.is_today_holiday(code, county_code=county, offset=offset)
        return {"is_holiday": result, "country": code}
    except ValueError as e:
        return f"Error: {e}"
    except httpx.HTTPError as e:
        return f"Error: API request failed — {e}"


# ---------------------------------------------------------------------------
# Discovery tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def list_countries(query: str | None = None) -> list[dict] | str:
    """List countries supported by the Nager.Date holiday API.

    Args:
        query: Optional case-insensitive substring to filter by country name
            or code (e.g. "united", "US"). Omit to return all 200+ countries.

    Returns a list of {code, name} objects. Use the code values with other
    tools like get_holidays.
    """
    try:
        countries = await _client.get_available_countries()
        mapped = [{"code": c["countryCode"], "name": c["name"]} for c in countries]
        if query is not None:
            q = query.strip().lower()
            if q:
                mapped = [
                    c
                    for c in mapped
                    if q in c["name"].lower() or q in c["code"].lower()
                ]
        return mapped
    except ValueError as e:
        return f"Error: {e}"
    except httpx.HTTPError as e:
        return f"Error: API request failed — {e}"


@mcp.tool()
async def get_country_info(country: str) -> dict | str:
    """Get detailed information about a country including its region and borders.

    Args:
        country: ISO 3166-1 alpha-2 country code (e.g. "GB", "DE").

    Returns country metadata: commonName, officialName, region, and
    a list of bordering countries.
    """
    try:
        code = country.strip().upper()
        return await _client.get_country_info(code)
    except ValueError as e:
        return f"Error: {e}"
    except httpx.HTTPError as e:
        return f"Error: API request failed — {e}"


@mcp.tool()
async def get_worldwide_holidays() -> list[dict] | str:
    """Get all public holidays happening today worldwide.

    Returns a list of holiday objects from all countries that have
    a public holiday today, each enriched with day_of_week.
    """
    try:
        holidays = await _client.get_worldwide_holidays()
        return _enrich_holidays(holidays)
    except ValueError as e:
        return f"Error: {e}"
    except httpx.HTTPError as e:
        return f"Error: API request failed — {e}"


def main() -> None:
    """Entry point for running the server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
