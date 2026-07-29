"""Tests for the MCP server tools: enrichment, filtering, defaults, error handling."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from nager_date_mcp.client import NagerDateClient
from nager_date_mcp.server import (
    _day_of_week,
    _enrich_holidays,
    get_country_info,
    get_holidays,
    get_long_weekends,
    get_next_holidays,
    get_worldwide_holidays,
    is_today_holiday,
    list_countries,
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
    # New Year's Day (lists GB-NIR), St Patrick's Day (GB-NIR only), plus the two
    # global holidays (Good Friday, Valentine's) which apply to every county.
    assert len(result) == 4
    names = {h["name"] for h in result}
    assert "New Year's Day" in names
    assert "St Patrick's Day" in names
    assert "Good Friday" in names
    assert "Valentine's Day" in names


async def test_get_holidays_county_includes_global_null_counties():
    """Global holidays (counties=null) ARE included when a county filter is set."""
    with patch(
        "nager_date_mcp.server._client.get_holidays",
        new_callable=AsyncMock,
        return_value=[h.copy() for h in MOCK_HOLIDAYS],
    ):
        result = await get_holidays("GB", year=2025, county="GB-ENG")
    # Good Friday (global, counties=None) and Valentine's (global) are included,
    # plus New Year's Day which lists GB-ENG. St Patrick's (GB-NIR only) excluded.
    names = {h["name"] for h in result}
    assert "Good Friday" in names
    assert "Valentine's Day" in names
    assert "New Year's Day" in names
    assert "St Patrick's Day" not in names


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


async def test_get_holidays_network_error_returns_friendly_string():
    """httpx network errors (ConnectError/timeout) must not propagate."""
    with patch(
        "nager_date_mcp.server._client.get_holidays",
        new_callable=AsyncMock,
        side_effect=httpx.ConnectError("connection refused"),
    ):
        result = await get_holidays("US", year=2025)
    assert isinstance(result, str)
    assert result.startswith("Error: API request failed")


async def test_is_today_holiday_timeout_returns_friendly_string():
    with patch(
        "nager_date_mcp.server._client.is_today_holiday",
        new_callable=AsyncMock,
        side_effect=httpx.TimeoutException("timed out"),
    ):
        result = await is_today_holiday("PE")
    assert isinstance(result, str)
    assert result.startswith("Error: API request failed")


async def test_list_countries_server_error_returns_friendly_string():
    with patch(
        "nager_date_mcp.server._client.get_available_countries",
        new_callable=AsyncMock,
        side_effect=httpx.HTTPStatusError(
            "500 Server Error", request=httpx.Request("GET", "https://x"), response=httpx.Response(500)
        ),
    ):
        result = await list_countries()
    assert isinstance(result, str)
    assert result.startswith("Error: API request failed")


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
    # Regional (GB-SCT) AND the national/global one (counties=None) are included.
    assert len(result) == 2
    names = {h["name"] for h in result}
    assert names == {"Regional Day", "National Day"}


async def test_get_next_holidays_invalid_country():
    with patch(
        "nager_date_mcp.server._client.get_next_holidays",
        new_callable=AsyncMock,
        side_effect=ValueError("Country not found: XX"),
    ):
        result = await get_next_holidays("XX")
    assert isinstance(result, str)
    assert "Country not found" in result


# -- get_long_weekends tests ------------------------------------------------

MOCK_LONG_WEEKENDS = [
    {"startDate": "2025-01-18", "endDate": "2025-01-20", "dayCount": 3,
     "needBridgeDay": False, "bridgeDays": []},
    {"startDate": "2025-06-19", "endDate": "2025-06-22", "dayCount": 4,
     "needBridgeDay": True, "bridgeDays": ["2025-06-20"]},
]


async def test_get_long_weekends_returns_list():
    with patch(
        "nager_date_mcp.server._client.get_long_weekends",
        new_callable=AsyncMock,
        return_value=MOCK_LONG_WEEKENDS,
    ):
        result = await get_long_weekends("US", year=2025)
    assert len(result) == 2
    assert result[0]["dayCount"] == 3
    assert result[1]["needBridgeDay"] is True


async def test_get_long_weekends_defaults_year():
    from datetime import date

    current_year = date.today().year
    mock = AsyncMock(return_value=[])
    with patch("nager_date_mcp.server._client.get_long_weekends", mock):
        await get_long_weekends("US")
    mock.assert_awaited_once_with(current_year, "US")


async def test_get_long_weekends_invalid_country():
    with patch(
        "nager_date_mcp.server._client.get_long_weekends",
        new_callable=AsyncMock,
        side_effect=ValueError("Country not found: ZZ"),
    ):
        result = await get_long_weekends("ZZ")
    assert isinstance(result, str)
    assert "Error" in result


# -- is_today_holiday tests -------------------------------------------------


async def test_is_today_holiday_true():
    with patch(
        "nager_date_mcp.server._client.is_today_holiday",
        new_callable=AsyncMock,
        return_value=True,
    ):
        result = await is_today_holiday("PE")
    assert result == {"is_holiday": True, "country": "PE"}


async def test_is_today_holiday_false():
    with patch(
        "nager_date_mcp.server._client.is_today_holiday",
        new_callable=AsyncMock,
        return_value=False,
    ):
        result = await is_today_holiday("GB")
    assert result == {"is_holiday": False, "country": "GB"}


async def test_is_today_holiday_normalizes_code():
    mock = AsyncMock(return_value=False)
    with patch("nager_date_mcp.server._client.is_today_holiday", mock):
        result = await is_today_holiday("  us  ")
    assert result["country"] == "US"
    mock.assert_awaited_once_with("US", county_code=None, offset=None)


async def test_is_today_holiday_passes_county():
    mock = AsyncMock(return_value=False)
    with patch("nager_date_mcp.server._client.is_today_holiday", mock):
        await is_today_holiday("GB", county="GB-ENG")
    mock.assert_awaited_once_with("GB", county_code="GB-ENG", offset=None)


async def test_is_today_holiday_invalid_country():
    with patch(
        "nager_date_mcp.server._client.is_today_holiday",
        new_callable=AsyncMock,
        side_effect=ValueError("Country not found: QQ"),
    ):
        result = await is_today_holiday("QQ")
    assert isinstance(result, str)
    assert "Error" in result


# -- list_countries tests ---------------------------------------------------


async def test_list_countries_maps_fields():
    raw = [
        {"countryCode": "AD", "name": "Andorra"},
        {"countryCode": "US", "name": "United States"},
    ]
    with patch(
        "nager_date_mcp.server._client.get_available_countries",
        new_callable=AsyncMock,
        return_value=raw,
    ):
        result = await list_countries()
    assert result == [
        {"code": "AD", "name": "Andorra"},
        {"code": "US", "name": "United States"},
    ]


# -- get_country_info tests -------------------------------------------------


async def test_get_country_info_returns_dict():
    info = {
        "commonName": "United Kingdom",
        "officialName": "United Kingdom of Great Britain and Northern Ireland",
        "countryCode": "GB",
        "region": "Europe",
        "borders": [{"commonName": "Ireland", "countryCode": "IE"}],
    }
    with patch(
        "nager_date_mcp.server._client.get_country_info",
        new_callable=AsyncMock,
        return_value=info,
    ):
        result = await get_country_info("gb")
    assert result["commonName"] == "United Kingdom"
    assert result["region"] == "Europe"


async def test_get_country_info_invalid_country():
    with patch(
        "nager_date_mcp.server._client.get_country_info",
        new_callable=AsyncMock,
        side_effect=ValueError("Country not found: ZZ"),
    ):
        result = await get_country_info("ZZ")
    assert isinstance(result, str)
    assert "Country not found" in result


# -- get_worldwide_holidays tests -------------------------------------------


async def test_get_worldwide_holidays_enriches():
    holidays = [
        {"date": "2026-07-28", "name": "Independence Day", "countryCode": "PE",
         "types": ["Public"]},
    ]
    with patch(
        "nager_date_mcp.server._client.get_worldwide_holidays",
        new_callable=AsyncMock,
        return_value=holidays,
    ):
        result = await get_worldwide_holidays()
    assert result[0]["day_of_week"] == "Tuesday"  # 2026-07-28


async def test_get_worldwide_holidays_error():
    with patch(
        "nager_date_mcp.server._client.get_worldwide_holidays",
        new_callable=AsyncMock,
        side_effect=ValueError("Something went wrong"),
    ):
        result = await get_worldwide_holidays()
    assert isinstance(result, str)
    assert "Error" in result


# -- empty-list handling ----------------------------------------------------


def _mock_transport_client(body: bytes) -> "NagerDateClient":
    """Build a real NagerDateClient backed by an httpx.MockTransport."""
    transport = httpx.MockTransport(lambda req: httpx.Response(200, content=body))
    http = httpx.AsyncClient(
        base_url="https://date.nager.at/api/v3", transport=transport
    )
    return NagerDateClient(client=http)


def _mock_204_client() -> "NagerDateClient":
    """Build a real NagerDateClient whose transport always returns 204 No Content.

    The Nager.Date API returns 204 (not 404) for valid-format country codes it
    has no data for (IN, SA, AE, TH, ...). The client maps that to None.
    """
    transport = httpx.MockTransport(lambda req: httpx.Response(204, content=b""))
    http = httpx.AsyncClient(
        base_url="https://date.nager.at/api/v3", transport=transport
    )
    return NagerDateClient(client=http)


# -- FIX 1: 204 -> None must not crash --------------------------------------


async def test_get_holidays_204_returns_friendly_message():
    """A 204 (None) for an unsupported country must not raise TypeError."""
    with patch("nager_date_mcp.server._client", _mock_204_client()):
        result = await get_holidays("IN", year=2026)
    assert isinstance(result, str)
    assert "No holiday data available for IN" in result


async def test_get_next_holidays_204_returns_friendly_message():
    """A 204 (None) for an unsupported country must not raise TypeError."""
    with patch("nager_date_mcp.server._client", _mock_204_client()):
        result = await get_next_holidays("SA")
    assert isinstance(result, str)
    assert "No holiday data available for SA" in result


def test_enrich_holidays_none_returns_empty():
    assert _enrich_holidays(None) == []
    assert _enrich_holidays([]) == []


# -- FIX 2: type filter empty -> helpful note -------------------------------


async def test_get_holidays_type_no_match_adds_note():
    """A type filter that matches nothing (but country has data) adds a note."""
    with patch(
        "nager_date_mcp.server._client.get_holidays",
        new_callable=AsyncMock,
        return_value=[h.copy() for h in MOCK_HOLIDAYS],
    ):
        result = await get_holidays("GB", year=2025, type="school")
    # No MOCK holiday has type "School" -> dict with empty holidays + note.
    assert isinstance(result, dict)
    assert result["holidays"] == []
    assert "no holidays matched type 'school'" in result["note"]
    assert "GB" in result["note"]


async def test_get_holidays_type_match_no_note():
    """When the type filter matches, no note is added (plain list)."""
    with patch(
        "nager_date_mcp.server._client.get_holidays",
        new_callable=AsyncMock,
        return_value=[h.copy() for h in MOCK_HOLIDAYS],
    ):
        result = await get_holidays("GB", year=2025, type="public")
    assert isinstance(result, list)
    assert len(result) >= 1


# -- FIX 3: county filter no regional match -> note -------------------------


async def test_get_holidays_county_no_regional_match_adds_note():
    """A county that matches no regional holiday adds a 'national only' note."""
    with patch(
        "nager_date_mcp.server._client.get_holidays",
        new_callable=AsyncMock,
        return_value=[h.copy() for h in MOCK_HOLIDAYS],
    ):
        result = await get_holidays("GB", year=2025, county="ZZ-NOPE")
    assert isinstance(result, dict)
    # Only the two global holidays survive (Good Friday, Valentine's).
    names = {h["name"] for h in result["holidays"]}
    assert names == {"Good Friday", "Valentine's Day"}
    assert "no regional holidays found for county 'ZZ-NOPE'" in result["note"]
    assert "national holidays only" in result["note"]


async def test_get_holidays_county_regional_match_no_note():
    """When the county matches a regional holiday, no note is added."""
    with patch(
        "nager_date_mcp.server._client.get_holidays",
        new_callable=AsyncMock,
        return_value=[h.copy() for h in MOCK_HOLIDAYS],
    ):
        result = await get_holidays("GB", year=2025, county="GB-NIR")
    # GB-NIR is a real regional match -> plain list, no note.
    assert isinstance(result, list)


# -- FIX 4: list_countries query filter -------------------------------------


async def test_list_countries_query_filters():
    raw = [
        {"countryCode": "GB", "name": "United Kingdom"},
        {"countryCode": "US", "name": "United States"},
        {"countryCode": "FR", "name": "France"},
    ]
    with patch(
        "nager_date_mcp.server._client.get_available_countries",
        new_callable=AsyncMock,
        return_value=raw,
    ):
        result = await list_countries(query="united")
    assert result == [
        {"code": "GB", "name": "United Kingdom"},
        {"code": "US", "name": "United States"},
    ]


async def test_list_countries_query_matches_code():
    raw = [
        {"countryCode": "GB", "name": "United Kingdom"},
        {"countryCode": "US", "name": "United States"},
    ]
    with patch(
        "nager_date_mcp.server._client.get_available_countries",
        new_callable=AsyncMock,
        return_value=raw,
    ):
        result = await list_countries(query="gb")
    assert result == [{"code": "GB", "name": "United Kingdom"}]


async def test_list_countries_no_query_returns_all():
    raw = [
        {"countryCode": "GB", "name": "United Kingdom"},
        {"countryCode": "FR", "name": "France"},
    ]
    with patch(
        "nager_date_mcp.server._client.get_available_countries",
        new_callable=AsyncMock,
        return_value=raw,
    ):
        result = await list_countries()
    assert len(result) == 2


async def test_get_worldwide_holidays_empty_list_returns_empty():
    """An empty API result must flow through _enrich_holidays([]) -> [] gracefully."""
    with patch("nager_date_mcp.server._client", _mock_transport_client(b"[]")):
        result = await get_worldwide_holidays()
    assert result == []


async def test_get_next_holidays_empty_list_returns_empty():
    """An empty API result must flow through _enrich_holidays([]) -> [] gracefully."""
    with patch("nager_date_mcp.server._client", _mock_transport_client(b"[]")):
        result = await get_next_holidays("GB")
    assert result == []
