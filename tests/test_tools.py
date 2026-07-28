"""Tests for the MCP server tools: enrichment, filtering, defaults, error handling."""

from unittest.mock import AsyncMock, patch

import pytest

from nager_date_mcp.server import (
    _day_of_week,
    _enrich_holidays,
    get_holidays,
    get_next_holidays,
)

# -- Helper tests -----------------------------------------------------------


def test_day_of_week_known_date():
    # 2025-01-01 is a Wednesday
    assert _day_of_week("2025-01-01") == "Wednesday"
    # 2025-12-25 is a Thursday
    assert _day_of_week("2025-12-25") == "Thursday"
    # 2024-07-04 is a Thursday
    assert _day_of_week("2024-07-04") == "Thursday"


def test_enrich_holidays_adds_day_of_week():
    holidays = [
        {"date": "2025-01-01", "name": "New Year's Day"},
        {"date": "2025-07-04", "name": "Independence Day"},
    ]
    result = _enrich_holidays(holidays)
    assert result[0]["day_of_week"] == "Wednesday"
    assert result[1]["day_of_week"] == "Friday"


def test_enrich_holidays_skips_missing_date():
    holidays = [{"name": "No date field"}]
    result = _enrich_holidays(holidays)
    assert "day_of_week" not in result[0]


# -- get_holidays tests -----------------------------------------------------

MOCK_HOLIDAYS = [
    {
        "date": "2025-01-01",
        "localName": "New Year's Day",
        "name": "New Year's Day",
        "countryCode": "GB",
        "fixed": False,
        "global": False,
        "counties": ["GB-ENG", "GB-NIR", "GB-SCT", "GB-WLS"],
        "launchYear": None,
        "types": ["Public"],
    },
    {
        "date": "2025-04-18",
        "localName": "Good Friday",
        "name": "Good Friday",
        "countryCode": "GB",
        "fixed": False,
        "global": True,
        "counties": None,
        "launchYear": None,
        "types": ["Public"],
    },
    {
        "date": "2025-03-17",
        "localName": "St Patrick's Day",
        "name": "St Patrick's Day",
        "countryCode": "GB",
        "fixed": False,
        "global": False,
        "counties": ["GB-NIR"],
        "launchYear": None,
        "types": ["Public", "Bank"],
    },
    {
        "date": "2025-02-14",
        "localName": "Valentine's Day",
        "name": "Valentine's Day",
        "countryCode": "GB",
        "fixed": True,
        "global": True,
        "counties": None,
        "launchYear": None,
        "types": ["Observance"],
    },
]


async def test_get_holidays_returns_enriched_list():
    with patch(
        "nager_date_mcp.server._client.get_holidays",
        new_callable=AsyncMock,
        return_value=[h.copy() for h in MOCK_HOLIDAYS],
    ):
        result = await get_holidays("GB", year=2025)
    assert isinstance(result, list)
    assert len(result) == 4
    assert result[0]["day_of_week"] == "Wednesday"  # 2025-01-01
    assert result[1]["day_of_week"] == "Friday"  # 2025-04-18


async def test_get_holidays_defaults_year_to_current():
    from datetime import date

    current_year = date.today().year
    mock = AsyncMock(return_value=[])
    with patch("nager_date_mcp.server._client.get_holidays", mock):
        await get_holidays("US")
    mock.assert_awaited_once_with(current_year, "US")


async def test_get_holidays_normalizes_country_code():
    mock = AsyncMock(return_value=[])
    with patch("nager_date_mcp.server._client.get_holidays", mock):
        await get_holidays("  gb  ", year=2025)
    mock.assert_awaited_once_with(2025, "GB")


async def test_get_holidays_filters_by_type():
    with patch(
        "nager_date_mcp.server._client.get_holidays",
        new_callable=AsyncMock,
        return_value=[h.copy() for h in MOCK_HOLIDAYS],
    ):
        result = await get_holidays("GB", year=2025, type="bank")
    # Only St Patrick's Day has "Bank" type
    assert len(result) == 1
    assert result[0]["name"] == "St Patrick's Day"


async def test_get_holidays_filters_by_type_case_insensitive():
    with patch(
        "nager_date_mcp.server._client.get_holidays",
        new_callable=AsyncMock,
        return_value=[h.copy() for h in MOCK_HOLIDAYS],
    ):
        result = await get_holidays("GB", year=2025, type="OBSERVANCE")
    assert len(result) == 1
    assert result[0]["name"] == "Valentine's Day"


async def test_get_holidays_filters_by_county():
    with patch(
        "nager_date_mcp.server._client.get_holidays",
        new_callable=AsyncMock,
        return_value=[h.copy() for h in MOCK_HOLIDAYS],
    ):
        result = await get_holidays("GB", year=2025, county="GB-NIR")
    # New Year's Day (has GB-NIR) and St Patrick's Day (only GB-NIR)
    assert len(result) == 2
    names = {h["name"] for h in result}
    assert "New Year's Day" in names
    assert "St Patrick's Day" in names


async def test_get_holidays_county_excludes_global_null_counties():
    """Holidays with counties=null (global) are excluded when county filter is set."""
    with patch(
        "nager_date_mcp.server._client.get_holidays",
        new_callable=AsyncMock,
        return_value=[h.copy() for h in MOCK_HOLIDAYS],
    ):
        result = await get_holidays("GB", year=2025, county="GB-ENG")
    # Good Friday has counties=None so excluded; Valentine's too
    names = {h["name"] for h in result}
    assert "Good Friday" not in names
    assert "New Year's Day" in names


async def test_get_holidays_invalid_country_returns_error_string():
    with patch(
        "nager_date_mcp.server._client.get_holidays",
        new_callable=AsyncMock,
        side_effect=ValueError("Country not found: ZZ"),
    ):
        result = await get_holidays("ZZ", year=2025)
    assert isinstance(result, str)
    assert "Country not found: ZZ" in result


async def test_get_holidays_year_out_of_range_returns_error():
    with patch(
        "nager_date_mcp.server._client.get_holidays",
        new_callable=AsyncMock,
        side_effect=ValueError("Bad request: The specified year '1800' is not supported."),
    ):
        result = await get_holidays("US", year=1800)
    assert isinstance(result, str)
    assert "1800" in result


# -- get_next_holidays tests ------------------------------------------------


async def test_get_next_holidays_enriches_with_day_of_week():
    upcoming = [
        {"date": "2026-08-15", "name": "Assumption Day", "countryCode": "FR",
         "counties": None, "types": ["Public"]},
    ]
    with patch(
        "nager_date_mcp.server._client.get_next_holidays",
        new_callable=AsyncMock,
        return_value=upcoming,
    ):
        result = await get_next_holidays("FR")
    assert result[0]["day_of_week"] == "Saturday"  # 2026-08-15


async def test_get_next_holidays_county_filter():
    upcoming = [
        {"date": "2026-08-01", "name": "Regional Day", "countryCode": "GB",
         "counties": ["GB-SCT"], "types": ["Public"]},
        {"date": "2026-08-15", "name": "National Day", "countryCode": "GB",
         "counties": None, "types": ["Public"]},
    ]
    with patch(
        "nager_date_mcp.server._client.get_next_holidays",
        new_callable=AsyncMock,
        return_value=upcoming,
    ):
        result = await get_next_holidays("GB", county="GB-SCT")
    assert len(result) == 1
    assert result[0]["name"] == "Regional Day"


async def test_get_next_holidays_invalid_country():
    with patch(
        "nager_date_mcp.server._client.get_next_holidays",
        new_callable=AsyncMock,
        side_effect=ValueError("Country not found: XX"),
    ):
        result = await get_next_holidays("XX")
    assert isinstance(result, str)
    assert "Country not found" in result
