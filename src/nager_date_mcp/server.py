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


def _enrich_holidays(holidays: list[dict]) -> list[dict]:
    """Add a 'day_of_week' field to each holiday dict."""
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
) -> list[dict] | str:
    """Get all public holidays for a country in a given year.

    Args:
        country: ISO 3166-1 alpha-2 country code (e.g. "US", "GB", "DE").
        year: Year to query (1976–2076). Defaults to the current year.
        type: Filter by holiday type (e.g. "Public", "Bank", "Observance"). Case-insensitive.
        county: Filter by ISO 3166-2 subdivision code (e.g. "GB-ENG", "US-CA").

    Returns a list of holiday objects, each enriched with a day_of_week field.
    """
    try:
        code = country.strip().upper()
        y = year if year is not None else date.today().year
        holidays = await _client.get_holidays(y, code)

        if type is not None:
            type_lower = type.lower()
            holidays = [
                h
                for h in holidays
                if any(type_lower == t.lower() for t in h.get("types", []))
            ]

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
async def list_countries() -> list[dict] | str:
    """List all countries supported by the Nager.Date holiday API.

    Returns a list of {code, name} objects for 200+ countries.
    Use the code values with other tools like get_holidays.
    """
    try:
        countries = await _client.get_available_countries()
        return [
            {"code": c["countryCode"], "name": c["name"]} for c in countries
        ]
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
