"""Tests for the MCP server tools: enrichment, filtering, defaults, error handling.

T09 sync port of the legacy 51-test suite: the tool functions are plain sync
module functions now (T07), so async mocks and awaits become
``patch(..., return_value=...)`` / direct calls. The ONLY assertion deltas are
transport-shaped exception swaps (legacy connect error ->
urllib.error.URLError, legacy timeout -> TimeoutError, legacy status error ->
urllib.error.HTTPError); every test name and every other assertion is
preserved 1:1 (51 -> 51). Mocks patch the module-level
``nager_date_mcp.server._client`` singleton's methods at the use site,
matching T07.

CLI-pilot P1 characterization additions (test-only, current behaviour pinned
before extraction): the per-operation county asymmetry, the combined
filter-note text/order, the worldwide-204 empty-success class, support-lookup
failure folds, and the wire-level dispatch/content rendering for malformed
source shapes. Nothing above this paragraph was rewritten; the protocol group
drives `server.main()` in-process over in-memory stdio with the real handler
path and only the low-level `client.fetch_raw` seam replaced — no sockets.
"""

import io
import json
import sys
import urllib.error
from contextlib import ExitStack, redirect_stdout
from unittest.mock import Mock, patch

import pytest

from nager_date_mcp.client import NagerDateClient
from nager_date_mcp import server as server_module
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
from nager_date_mcp.transport import SeamResponse

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


def test_get_holidays_returns_enriched_list():
    with patch(
        "nager_date_mcp.server._client.get_holidays",
        return_value=[h.copy() for h in MOCK_HOLIDAYS],
    ):
        result = get_holidays("GB", year=2025)
    assert isinstance(result, list)
    assert len(result) == 4
    assert result[0]["day_of_week"] == "Wednesday"  # 2025-01-01
    assert result[1]["day_of_week"] == "Friday"  # 2025-04-18


def test_get_holidays_defaults_year_to_current():
    from datetime import date

    current_year = date.today().year
    mock = Mock(return_value=[])
    with patch("nager_date_mcp.server._client.get_holidays", mock):
        get_holidays("US")
    mock.assert_called_once_with(current_year, "US")


def test_get_holidays_normalizes_country_code():
    mock = Mock(return_value=[])
    with patch("nager_date_mcp.server._client.get_holidays", mock):
        get_holidays("  gb  ", year=2025)
    mock.assert_called_once_with(2025, "GB")


def test_get_holidays_filters_by_type():
    with patch(
        "nager_date_mcp.server._client.get_holidays",
        return_value=[h.copy() for h in MOCK_HOLIDAYS],
    ):
        result = get_holidays("GB", year=2025, type="bank")
    # Only St Patrick's Day has "Bank" type
    assert len(result) == 1
    assert result[0]["name"] == "St Patrick's Day"


def test_get_holidays_filters_by_type_case_insensitive():
    with patch(
        "nager_date_mcp.server._client.get_holidays",
        return_value=[h.copy() for h in MOCK_HOLIDAYS],
    ):
        result = get_holidays("GB", year=2025, type="OBSERVANCE")
    assert len(result) == 1
    assert result[0]["name"] == "Valentine's Day"


def test_get_holidays_filters_by_county():
    with patch(
        "nager_date_mcp.server._client.get_holidays",
        return_value=[h.copy() for h in MOCK_HOLIDAYS],
    ):
        result = get_holidays("GB", year=2025, county="GB-NIR")
    # New Year's Day (lists GB-NIR), St Patrick's Day (GB-NIR only), plus the two
    # global holidays (Good Friday, Valentine's) which apply to every county.
    assert len(result) == 4
    names = {h["name"] for h in result}
    assert "New Year's Day" in names
    assert "St Patrick's Day" in names
    assert "Good Friday" in names
    assert "Valentine's Day" in names


def test_get_holidays_county_includes_global_null_counties():
    """Global holidays (counties=null) ARE included when a county filter is set."""
    with patch(
        "nager_date_mcp.server._client.get_holidays",
        return_value=[h.copy() for h in MOCK_HOLIDAYS],
    ):
        result = get_holidays("GB", year=2025, county="GB-ENG")
    # Good Friday (global, counties=None) and Valentine's (global) are included,
    # plus New Year's Day which lists GB-ENG. St Patrick's (GB-NIR only) excluded.
    names = {h["name"] for h in result}
    assert "Good Friday" in names
    assert "Valentine's Day" in names
    assert "New Year's Day" in names
    assert "St Patrick's Day" not in names


def test_get_holidays_invalid_country_returns_error_string():
    with patch(
        "nager_date_mcp.server._client.get_holidays",
        side_effect=ValueError("Country not found: ZZ"),
    ):
        result = get_holidays("ZZ", year=2025)
    assert isinstance(result, str)
    assert "Country not found: ZZ" in result


def test_get_holidays_year_out_of_range_returns_error():
    with patch(
        "nager_date_mcp.server._client.get_holidays",
        side_effect=ValueError("Bad request: The specified year '1800' is not supported."),
    ):
        result = get_holidays("US", year=1800)
    assert isinstance(result, str)
    assert "1800" in result


def test_get_holidays_network_error_returns_friendly_string():
    """Transport errors (connect failures) must not propagate."""
    with patch(
        "nager_date_mcp.server._client.get_holidays",
        side_effect=urllib.error.URLError("connection refused"),
    ):
        result = get_holidays("US", year=2025)
    assert isinstance(result, str)
    assert result.startswith("Error: API request failed")


# R1 witness (seam pin in tests/test_transport.py; REFERENCE §5: py3.11 TimeoutError not a URLError)
def test_is_today_holiday_timeout_returns_friendly_string():
    with patch(
        "nager_date_mcp.server._client.is_today_holiday",
        side_effect=TimeoutError("timed out"),
    ):
        result = is_today_holiday("PE")
    assert isinstance(result, str)
    assert result.startswith("Error: API request failed")


def test_list_countries_server_error_returns_friendly_string():
    with patch(
        "nager_date_mcp.server._client.get_available_countries",
        side_effect=urllib.error.HTTPError(
            "https://date.nager.at/api/v3/AvailableCountries", 500, "500 Server Error", None, None
        ),
    ):
        result = list_countries()
    assert isinstance(result, str)
    assert result.startswith("Error: API request failed")

# -- get_next_holidays tests ------------------------------------------------


def test_get_next_holidays_enriches_with_day_of_week():
    upcoming = [
        {"date": "2026-08-15", "name": "Assumption Day", "countryCode": "FR",
         "counties": None, "types": ["Public"]},
    ]
    with patch(
        "nager_date_mcp.server._client.get_next_holidays",
        return_value=upcoming,
    ):
        result = get_next_holidays("FR")
    assert result[0]["day_of_week"] == "Saturday"  # 2026-08-15


def test_get_next_holidays_county_filter():
    upcoming = [
        {"date": "2026-08-01", "name": "Regional Day", "countryCode": "GB",
         "counties": ["GB-SCT"], "types": ["Public"]},
        {"date": "2026-08-15", "name": "National Day", "countryCode": "GB",
         "counties": None, "types": ["Public"]},
    ]
    with patch(
        "nager_date_mcp.server._client.get_next_holidays",
        return_value=upcoming,
    ):
        result = get_next_holidays("GB", county="GB-SCT")
    # Regional (GB-SCT) AND the national/global one (counties=None) are included.
    assert len(result) == 2
    names = {h["name"] for h in result}
    assert names == {"Regional Day", "National Day"}


def test_get_next_holidays_invalid_country():
    with patch(
        "nager_date_mcp.server._client.get_next_holidays",
        side_effect=ValueError("Country not found: XX"),
    ):
        result = get_next_holidays("XX")
    assert isinstance(result, str)
    assert "Country not found" in result

# -- get_long_weekends tests ------------------------------------------------

MOCK_LONG_WEEKENDS = [
    {"startDate": "2025-01-18", "endDate": "2025-01-20", "dayCount": 3,
     "needBridgeDay": False, "bridgeDays": []},
    {"startDate": "2025-06-19", "endDate": "2025-06-22", "dayCount": 4,
     "needBridgeDay": True, "bridgeDays": ["2025-06-20"]},
]


def test_get_long_weekends_returns_list():
    with patch(
        "nager_date_mcp.server._client.get_long_weekends",
        return_value=MOCK_LONG_WEEKENDS,
    ):
        result = get_long_weekends("US", year=2025)
    assert len(result) == 2
    assert result[0]["dayCount"] == 3
    assert result[1]["needBridgeDay"] is True


def test_get_long_weekends_defaults_year():
    from datetime import date

    current_year = date.today().year
    mock = Mock(return_value=[])
    with patch("nager_date_mcp.server._client.get_long_weekends", mock), patch(
        "nager_date_mcp.server._client.get_available_countries",
        return_value=[{"countryCode": "US", "name": "United States"}],
    ):
        get_long_weekends("US")
    mock.assert_called_once_with(current_year, "US")


def test_get_long_weekends_invalid_country():
    with patch(
        "nager_date_mcp.server._client.get_long_weekends",
        side_effect=ValueError("Country not found: ZZ"),
    ):
        result = get_long_weekends("ZZ")
    assert isinstance(result, str)
    assert "Error" in result

# -- is_today_holiday tests -------------------------------------------------


def test_is_today_holiday_true():
    with patch(
        "nager_date_mcp.server._client.is_today_holiday",
        return_value=True,
    ):
        result = is_today_holiday("PE")
    assert result == {"is_holiday": True, "country": "PE"}


def test_is_today_holiday_false():
    """Supported country where today isn't a holiday (204 + in supported list)."""
    with (
        patch(
            "nager_date_mcp.server._client.is_today_holiday",
            return_value=None,
        ),
        patch(
            "nager_date_mcp.server._client.get_available_countries",
            return_value=[{"countryCode": "GB", "name": "United Kingdom"}],
        ),
    ):
        result = is_today_holiday("GB")
    assert result == {"is_holiday": False, "country": "GB"}


def test_is_today_holiday_unsupported_country_returns_no_data_message():
    """Unsupported country (204 + NOT in supported list) -> friendly no-data msg."""
    with (
        patch(
            "nager_date_mcp.server._client.is_today_holiday",
            return_value=None,
        ),
        patch(
            "nager_date_mcp.server._client.get_available_countries",
            return_value=[{"countryCode": "US", "name": "United States"}],
        ),
    ):
        result = is_today_holiday("IN")
    assert result == "No holiday data available for IN."


def test_is_today_holiday_normalizes_code():
    mock = Mock(return_value=None)
    with (
        patch("nager_date_mcp.server._client.is_today_holiday", mock),
        patch(
            "nager_date_mcp.server._client.get_available_countries",
            return_value=[{"countryCode": "US", "name": "United States"}],
        ),
    ):
        result = is_today_holiday("  us  ")
    assert result["country"] == "US"
    mock.assert_called_once_with("US", county_code=None, offset=None)


def test_is_today_holiday_passes_county():
    mock = Mock(return_value=None)
    with (
        patch("nager_date_mcp.server._client.is_today_holiday", mock),
        patch(
            "nager_date_mcp.server._client.get_available_countries",
            return_value=[{"countryCode": "GB", "name": "United Kingdom"}],
        ),
    ):
        is_today_holiday("GB", county="GB-ENG")
    mock.assert_called_once_with("GB", county_code="GB-ENG", offset=None)


def test_is_today_holiday_invalid_country():
    with patch(
        "nager_date_mcp.server._client.is_today_holiday",
        side_effect=ValueError("Country not found: QQ"),
    ):
        result = is_today_holiday("QQ")
    assert isinstance(result, str)
    assert "Error" in result

# -- list_countries tests ---------------------------------------------------


def test_list_countries_maps_fields():
    raw = [
        {"countryCode": "AD", "name": "Andorra"},
        {"countryCode": "US", "name": "United States"},
    ]
    with patch(
        "nager_date_mcp.server._client.get_available_countries",
        return_value=raw,
    ):
        result = list_countries()
    assert result == [
        {"code": "AD", "name": "Andorra"},
        {"code": "US", "name": "United States"},
    ]

# -- get_country_info tests -------------------------------------------------


def test_get_country_info_returns_dict():
    info = {
        "commonName": "United Kingdom",
        "officialName": "United Kingdom of Great Britain and Northern Ireland",
        "countryCode": "GB",
        "region": "Europe",
        "borders": [{"commonName": "Ireland", "countryCode": "IE"}],
    }
    with patch(
        "nager_date_mcp.server._client.get_country_info",
        return_value=info,
    ):
        result = get_country_info("gb")
    assert result["commonName"] == "United Kingdom"
    assert result["region"] == "Europe"


def test_get_country_info_invalid_country():
    with patch(
        "nager_date_mcp.server._client.get_country_info",
        side_effect=ValueError("Country not found: ZZ"),
    ):
        result = get_country_info("ZZ")
    assert isinstance(result, str)
    assert "Country not found" in result

# -- get_worldwide_holidays tests -------------------------------------------


def test_get_worldwide_holidays_enriches():
    holidays = [
        {"date": "2026-07-28", "name": "Independence Day", "countryCode": "PE",
         "types": ["Public"]},
    ]
    with patch(
        "nager_date_mcp.server._client.get_worldwide_holidays",
        return_value=holidays,
    ):
        result = get_worldwide_holidays()
    assert result[0]["day_of_week"] == "Tuesday"  # 2026-07-28


def test_get_worldwide_holidays_error():
    with patch(
        "nager_date_mcp.server._client.get_worldwide_holidays",
        side_effect=ValueError("Something went wrong"),
    ):
        result = get_worldwide_holidays()
    assert isinstance(result, str)
    assert "Error" in result

# -- empty-list handling ----------------------------------------------------


def _seam_returning(status_code: int, text: str):
    """Return a fetch_raw replacement serving a fixed SeamResponse.

    Legacy injected a mock transport into the client instance; the sync
    client (T02) has no transport hook — its seam is the module-level
    ``nager_date_mcp.client.fetch_raw`` (T08's sanctioned injection point). The
    fake is a REAL SeamResponse built from the request URL, so
    ``request.url.path`` stays honest (the /api/v3 prefix is kept by the
    literal base_url + path concat).
    """

    def fake_fetch_raw(url, timeout=5.0):
        return SeamResponse(status_code=status_code, text=text, url=url)

    return fake_fetch_raw


def _mock_transport_client(body: bytes):
    """A fresh sync NagerDateClient + seam fake serving 200 + body.

    Fresh client per test = no cache bleed between tests (the legacy clients
    were likewise per-test). Patch BOTH ``nager_date_mcp.client.fetch_raw``
    (the seam) and ``nager_date_mcp.server._client`` (the singleton) in tests.
    """
    return NagerDateClient(), _seam_returning(200, body.decode("utf-8"))


def _mock_204_client():
    """A fresh NagerDateClient whose seam always returns 204 No Content.

    The Nager.Date API returns 204 (not 404) for valid-format country codes it
    has no data for (IN, SA, AE, TH, ...). The client maps that to None.
    """
    return NagerDateClient(), _seam_returning(204, "")

# -- FIX 1: 204 -> None must not crash --------------------------------------


def test_get_holidays_204_returns_friendly_message():
    """A 204 (None) for an unsupported country must not raise TypeError."""
    client, seam = _mock_204_client()
    with patch("nager_date_mcp.client.fetch_raw", seam), patch(
        "nager_date_mcp.server._client", client
    ):
        result = get_holidays("IN", year=2026)
    assert isinstance(result, str)
    assert "No holiday data available for IN" in result


def test_get_next_holidays_204_returns_friendly_message():
    """A 204 (None) for an unsupported country must not raise TypeError."""
    client, seam = _mock_204_client()
    with patch("nager_date_mcp.client.fetch_raw", seam), patch(
        "nager_date_mcp.server._client", client
    ):
        result = get_next_holidays("SA")
    assert isinstance(result, str)
    assert "No holiday data available for SA" in result


def test_get_long_weekends_204_returns_friendly_message():
    """A 204 (None) for an unsupported country must return the friendly
    no-data message, NOT a silent empty list — consistent with siblings."""
    client, seam = _mock_204_client()
    with patch("nager_date_mcp.client.fetch_raw", seam), patch(
        "nager_date_mcp.server._client", client
    ):
        result = get_long_weekends("IN", year=2026)
    assert isinstance(result, str)
    assert "No holiday data available for IN" in result
    assert result != []


def test_get_long_weekends_empty_for_unsupported_country_friendly_message():
    """The real LongWeekend endpoint returns 200 + [] (NOT 204) for unsupported
    countries like IN. An empty result for an unsupported country must still
    surface the friendly no-data message rather than a silent []."""
    client, _seam = _mock_204_client()  # real client; we patch its methods below
    with patch.object(
        client, "get_long_weekends", return_value=[]
    ), patch.object(
        client,
        "get_available_countries",
        return_value=[{"countryCode": "US", "name": "United States"}],
    ), patch("nager_date_mcp.server._client", client):
        result = get_long_weekends("IN", year=2026)
    assert isinstance(result, str)
    assert "No holiday data available for IN" in result


def test_get_long_weekends_empty_for_supported_country_returns_list():
    """A genuinely empty result for a SUPPORTED country (no long weekends that
    year) must be returned as-is — NOT turned into a no-data message."""
    client, _seam = _mock_204_client()
    with patch.object(
        client, "get_long_weekends", return_value=[]
    ), patch.object(
        client,
        "get_available_countries",
        return_value=[{"countryCode": "US", "name": "United States"}],
    ), patch("nager_date_mcp.server._client", client):
        result = get_long_weekends("US", year=2026)
    assert result == []


def test_get_long_weekends_supported_country_returns_list():
    """No regression: a supported country still returns the real list."""
    with patch(
        "nager_date_mcp.server._client.get_long_weekends",
        return_value=MOCK_LONG_WEEKENDS,
    ):
        result = get_long_weekends("US", year=2025)
    assert isinstance(result, list)
    assert result == MOCK_LONG_WEEKENDS


def test_enrich_holidays_none_returns_empty():
    assert _enrich_holidays(None) == []
    assert _enrich_holidays([]) == []

# -- FIX 2: type filter empty -> helpful note --------------------------------


def test_get_holidays_type_no_match_adds_note():
    """A type filter that matches nothing (but country has data) adds a note."""
    with patch(
        "nager_date_mcp.server._client.get_holidays",
        return_value=[h.copy() for h in MOCK_HOLIDAYS],
    ):
        result = get_holidays("GB", year=2025, type="school")
    # No MOCK holiday has type "School" -> dict with empty holidays + note.
    assert isinstance(result, dict)
    assert result["holidays"] == []
    assert "no holidays matched type 'school'" in result["note"]
    assert "GB" in result["note"]


def test_get_holidays_type_match_no_note():
    """When the type filter matches, no note is added (plain list)."""
    with patch(
        "nager_date_mcp.server._client.get_holidays",
        return_value=[h.copy() for h in MOCK_HOLIDAYS],
    ):
        result = get_holidays("GB", year=2025, type="public")
    assert isinstance(result, list)
    assert len(result) >= 1

# -- FIX 3: county filter no regional match -> note --------------------------


def test_get_holidays_county_no_regional_match_adds_note():
    """A county that matches no regional holiday adds a 'national only' note."""
    with patch(
        "nager_date_mcp.server._client.get_holidays",
        return_value=[h.copy() for h in MOCK_HOLIDAYS],
    ):
        result = get_holidays("GB", year=2025, county="ZZ-NOPE")
    assert isinstance(result, dict)
    # Only the two global holidays survive (Good Friday, Valentine's).
    names = {h["name"] for h in result["holidays"]}
    assert names == {"Good Friday", "Valentine's Day"}
    assert "no regional holidays found for county 'ZZ-NOPE'" in result["note"]
    assert "national holidays only" in result["note"]


def test_get_holidays_county_regional_match_no_note():
    """When the county matches a regional holiday, no note is added."""
    with patch(
        "nager_date_mcp.server._client.get_holidays",
        return_value=[h.copy() for h in MOCK_HOLIDAYS],
    ):
        result = get_holidays("GB", year=2025, county="GB-NIR")
    # GB-NIR is a real regional match -> plain list, no note.
    assert isinstance(result, list)

# -- FIX 4: list_countries query filter --------------------------------------


def test_list_countries_query_filters():
    raw = [
        {"countryCode": "GB", "name": "United Kingdom"},
        {"countryCode": "US", "name": "United States"},
        {"countryCode": "FR", "name": "France"},
    ]
    with patch(
        "nager_date_mcp.server._client.get_available_countries",
        return_value=raw,
    ):
        result = list_countries(query="united")
    assert result == [
        {"code": "GB", "name": "United Kingdom"},
        {"code": "US", "name": "United States"},
    ]


def test_list_countries_query_matches_code():
    raw = [
        {"countryCode": "GB", "name": "United Kingdom"},
        {"countryCode": "US", "name": "United States"},
    ]
    with patch(
        "nager_date_mcp.server._client.get_available_countries",
        return_value=raw,
    ):
        result = list_countries(query="gb")
    assert result == [{"code": "GB", "name": "United Kingdom"}]


def test_list_countries_no_query_returns_all():
    raw = [
        {"countryCode": "GB", "name": "United Kingdom"},
        {"countryCode": "FR", "name": "France"},
    ]
    with patch(
        "nager_date_mcp.server._client.get_available_countries",
        return_value=raw,
    ):
        result = list_countries()
    assert len(result) == 2


def test_get_worldwide_holidays_empty_list_returns_empty():
    """An empty API result must flow through _enrich_holidays([]) -> [] gracefully."""
    client, seam = _mock_transport_client(b"[]")
    with patch("nager_date_mcp.client.fetch_raw", seam), patch(
        "nager_date_mcp.server._client", client
    ):
        result = get_worldwide_holidays()
    assert result == []


def test_get_next_holidays_empty_list_returns_empty():
    """An empty API result must flow through _enrich_holidays([]) -> [] gracefully."""
    client, seam = _mock_transport_client(b"[]")
    with patch("nager_date_mcp.client.fetch_raw", seam), patch(
        "nager_date_mcp.server._client", client
    ):
        result = get_next_holidays("GB")
    assert result == []

# -- get_next_holidays county no-regional-match note (mirrors get_holidays) ---


def test_get_next_holidays_county_no_regional_match_adds_note():
    """A county that matches no regional holiday adds a 'national only' note."""
    upcoming = [
        {"date": "2026-08-01", "name": "Regional Day", "countryCode": "GB",
         "counties": ["GB-SCT"], "types": ["Public"]},
        {"date": "2026-08-15", "name": "National Day", "countryCode": "GB",
         "counties": None, "types": ["Public"]},
    ]
    with patch(
        "nager_date_mcp.server._client.get_next_holidays",
        return_value=upcoming,
    ):
        result = get_next_holidays("GB", county="ZZ-NOPE")
    assert isinstance(result, dict)
    # Only the national/global holiday survives the filter.
    names = {h["name"] for h in result["holidays"]}
    assert names == {"National Day"}
    assert "no regional holidays found for county 'ZZ-NOPE'" in result["note"]
    assert "national holidays only" in result["note"]


def test_get_next_holidays_county_regional_match_no_note():
    """When the county matches a regional holiday, a plain list is returned."""
    upcoming = [
        {"date": "2026-08-01", "name": "Regional Day", "countryCode": "GB",
         "counties": ["GB-SCT"], "types": ["Public"]},
        {"date": "2026-08-15", "name": "National Day", "countryCode": "GB",
         "counties": None, "types": ["Public"]},
    ]
    with patch(
        "nager_date_mcp.server._client.get_next_holidays",
        return_value=upcoming,
    ):
        result = get_next_holidays("GB", county="GB-SCT")
    assert isinstance(result, list)
    assert len(result) == 2

# -- per-record date-parse resilience in _enrich_holidays --------------------


def test_enrich_holidays_malformed_date_does_not_crash():
    """One bad date must not sink the whole response: good records keep their
    day_of_week, the bad record gets day_of_week=None, no exception raised."""
    holidays = [
        {"date": "2025-01-01", "name": "Good"},
        {"date": "not-a-date", "name": "Bad"},
    ]
    result = _enrich_holidays(holidays)
    assert result[0]["day_of_week"] == "Wednesday"
    assert result[1]["day_of_week"] is None


# ===========================================================================
# CLI-pilot P1 characterization (test-only; no production code changed).
# These pin CURRENT observable MCP behaviour that the later extraction must
# preserve. Characterization tests legitimately pass on the baseline; their
# sensitivity was demonstrated once against a deliberately wrong expectation
# (see results/p1-implementation.md) before locking the real values in.
# ===========================================================================


def _tools_call(name, arguments, rid=1):
    """Build one tools/call request line as an object."""
    return {
        "jsonrpc": "2.0",
        "id": rid,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }


def _protocol_exchange(requests, client, seam):
    """Run ``server.main()`` in-process over in-memory stdio.

    The REAL handler + dispatch loop executes (so the returned JSON-RPC object
    is the oracle); only the low-level ``client.fetch_raw`` seam and the shared
    ``_client`` instance are replaced. No socket is ever opened.
    """
    out = io.StringIO()
    original_stdin = sys.stdin
    sys.stdin = io.StringIO("".join(json.dumps(r) + "\n" for r in requests))
    try:
        with ExitStack() as stack:
            stack.enter_context(patch("nager_date_mcp.client.fetch_raw", seam))
            stack.enter_context(patch.object(server_module, "_client", client))
            stack.enter_context(redirect_stdout(out))
            server_module.main()
    finally:
        sys.stdin = original_stdin
    return [json.loads(line) for line in out.getvalue().splitlines()]


# -- county filtering asymmetry ---------------------------------------------
# MCP holidays/next-holidays strip+uppercase the supplied county before
# filtering; MCP is_today_holiday forwards it unchanged. Pin both halves.


@pytest.mark.parametrize(
    "invoke, method, payload, expected_names",
    [
        (
            lambda: get_holidays("GB", year=2025, county="  gb-NIR  "),
            "get_holidays",
            lambda: [h.copy() for h in MOCK_HOLIDAYS],
            {"New Year's Day", "Good Friday", "St Patrick's Day", "Valentine's Day"},
        ),
        (
            lambda: get_next_holidays("GB", county="  gb-SCT  "),
            "get_next_holidays",
            lambda: [
                {"date": "2026-08-01", "name": "Regional Day",
                 "countryCode": "GB", "counties": ["GB-SCT"], "types": ["Public"]},
                {"date": "2026-08-15", "name": "National Day",
                 "countryCode": "GB", "counties": None, "types": ["Public"]},
            ],
            {"Regional Day", "National Day"},
        ),
    ],
    ids=["get_holidays", "get_next_holidays"],
)
def test_mcp_county_filters_strip_and_upper_county(invoke, method, payload, expected_names):
    """Lowercase/surrounding whitespace still matches after strip().upper()."""
    with patch(f"nager_date_mcp.server._client.{method}", return_value=payload()):
        result = invoke()
    assert isinstance(result, list)  # a regional match adds no note
    assert {h["name"] for h in result} == expected_names


def test_mcp_today_check_forwards_county_verbatim():
    """The today-check does NOT normalize the county (per-operation asymmetry):
    the raw supplied string reaches the client kwarg unchanged."""
    mock = Mock(return_value=None)
    with (
        patch("nager_date_mcp.server._client.is_today_holiday", mock),
        patch(
            "nager_date_mcp.server._client.get_available_countries",
            return_value=[{"countryCode": "GB", "name": "United Kingdom"}],
        ),
    ):
        result = is_today_holiday("GB", county="  gb-eng  ", offset=-5)
    assert result == {"is_holiday": False, "country": "GB"}
    mock.assert_called_once_with("GB", county_code="  gb-eng  ", offset=-5)


# -- combined filter notes: order, joining and text --------------------------


def test_get_holidays_type_then_county_notes_keep_order_and_text():
    """Both notes present: type note first, then county note, joined by '; '."""
    with patch(
        "nager_date_mcp.server._client.get_holidays",
        return_value=[h.copy() for h in MOCK_HOLIDAYS],
    ):
        result = get_holidays("GB", year=2025, type="school", county=" zz-nope ")
    assert result == {
        "holidays": [],
        "note": (
            "note: no holidays matched type 'school' for GB "
            "(this country may use a different type label); "
            "note: no regional holidays found for county 'ZZ-NOPE'; "
            "showing national holidays only"
        ),
    }


# -- endpoint-specific empty/no-data classes --------------------------------


def test_get_worldwide_holidays_204_is_successful_empty_list():
    """A worldwide 204 reaches _enrich_holidays as None -> []: a SUCCESSFUL
    empty result, NOT the country-style no-data string."""
    client, seam = _mock_204_client()
    with patch("nager_date_mcp.client.fetch_raw", seam), patch(
        "nager_date_mcp.server._client", client
    ):
        result = get_worldwide_holidays()
    assert result == []


@pytest.mark.parametrize(
    "invoke, method, empty_value",
    [
        (lambda: get_long_weekends("US", year=2025), "get_long_weekends", []),
        (lambda: is_today_holiday("US"), "is_today_holiday", None),
    ],
    ids=["get_long_weekends", "is_today_holiday"],
)
def test_support_lookup_failure_is_a_failure(invoke, method, empty_value):
    """When the empty-result disambiguation lookup itself fails, the transport
    fold applies — it must not surface as a false/no-data answer."""
    client = NagerDateClient()
    with (
        patch.object(client, method, return_value=empty_value),
        patch.object(
            client,
            "get_available_countries",
            side_effect=urllib.error.URLError("lookup down"),
        ),
        patch("nager_date_mcp.server._client", client),
    ):
        result = invoke()
    assert result == "Error: API request failed — <urlopen error lookup down>"


# -- protocol rendering: compact Unicode content -----------------------------


def test_protocol_content_is_compact_and_unicode_preserving():
    """Result text is compact JSON (no spaces) with raw non-ASCII characters."""
    body = json.dumps(
        [{
            "date": "2025-11-19",
            "name": "Buß- und Bettag",
            "localName": "Buß- und Bettag",
            "types": ["Public"],
        }],
        ensure_ascii=False,
    ).encode("utf-8")
    client, seam = _mock_transport_client(body)
    [resp] = _protocol_exchange(
        [_tools_call("get_holidays", {"country": "DE", "year": 2025})], client, seam
    )
    assert resp["result"]["content"] == [{
        "type": "text",
        "text": (
            '[{"date":"2025-11-19","name":"Buß- und Bettag",'
            '"localName":"Buß- und Bettag","types":["Public"],'
            '"day_of_week":"Wednesday"}]'
        ),
    }]


# -- malformed source shapes: current fold/dispatch behaviour ----------------


def test_protocol_null_types_field_raises_dispatch_error():
    """A null ``types`` field makes the type filter raise TypeError, which the
    dispatch guard renders as -32603 (NOT a friendly fold string)."""
    body = json.dumps([{"date": "2025-01-01", "types": None}]).encode("utf-8")
    client, seam = _mock_transport_client(body)
    [resp] = _protocol_exchange(
        [_tools_call("get_holidays", {"country": "GB", "year": 2025, "type": "Public"})],
        client,
        seam,
    )
    assert "result" not in resp
    assert resp["error"] == {
        "code": -32603,
        "message": "'NoneType' object is not iterable",
    }


def test_protocol_object_body_with_date_key_is_dispatch_error():
    """An object in place of a holiday list is iterated by key; a 'date' key
    reaches enrichment as a str and item assignment raises -> -32603."""
    body = b'{"date":"2025-01-01","name":"Oops"}'
    client, seam = _mock_transport_client(body)
    [resp] = _protocol_exchange(
        [_tools_call("get_holidays", {"country": "GB", "year": 2025})], client, seam
    )
    assert "result" not in resp
    assert resp["error"] == {
        "code": -32603,
        "message": "'str' object does not support item assignment",
    }


def test_protocol_object_body_without_date_key_passes_through_as_success():
    """Without a 'date' key the same malformed object is enshrined as a
    SUCCESS text object — the MCP layer performs no result-shape check, which
    is why the CLI must own that check at its result boundary."""
    body = b'{"name":"Oops"}'
    client, seam = _mock_transport_client(body)
    [resp] = _protocol_exchange(
        [_tools_call("get_holidays", {"country": "GB", "year": 2025})], client, seam
    )
    assert resp["result"]["content"] == [{"type": "text", "text": '{"name":"Oops"}'}]
    assert resp["result"].get("isError") is not True


def test_protocol_country_info_204_renders_null_success():
    """A missing/non-object country-info result (204 -> None) is currently a
    literal 'null' SUCCESS text; the CLI must classify it invalid_response."""
    client, seam = _mock_204_client()
    [resp] = _protocol_exchange(
        [_tools_call("get_country_info", {"country": "ZZ"})], client, seam
    )
    assert resp["result"]["content"] == [{"type": "text", "text": "null"}]
    assert resp["result"].get("isError") is not True


def test_protocol_non_collection_countries_is_dispatch_error():
    """An unusable countries collection (null) raises at iteration -> -32603."""
    body = b"null"
    client, seam = _mock_transport_client(body)
    [resp] = _protocol_exchange(
        [_tools_call("list_countries", {})], client, seam
    )
    assert "result" not in resp
    assert resp["error"] == {
        "code": -32603,
        "message": "'NoneType' object is not iterable",
    }
