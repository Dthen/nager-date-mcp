"""Tests for the shared holiday-operation layer (P2 native CLI pilot).

The operation layer is interface-neutral: it owns the holidays fetch/filter/
enrich logic and returns domain outcomes (data + typed warnings + original
failure provenance). The MCP adapter and the CLI adapter are separate
presentation layers; nothing here imports either of them.
"""

from __future__ import annotations

import copy
import urllib.error
from datetime import date

import pytest

import nager_date_mcp.client as client_mod
from nager_date_mcp import operations
from nager_date_mcp.operations import (
    COUNTY_NATIONAL_ONLY,
    TYPE_NO_MATCHES,
    Outcome,
    Warning,
)
from nager_date_mcp.transport import SeamResponse


def _holidays_validator(payload):
    if not isinstance(payload, list) or not all(isinstance(r, dict) for r in payload):
        raise operations.InvalidResponse("invalid holiday payload")


def _countries_validator(payload):
    if not isinstance(payload, list) or not all(
        isinstance(c, dict) and isinstance(c.get("countryCode"), str)
        for c in payload
    ):
        raise operations.InvalidResponse("invalid countries payload")


def _country_info_validator(payload):
    if not isinstance(payload, dict):
        raise operations.InvalidResponse("invalid country-info payload")


HOLIDAYS = [
    {
        "date": "2025-01-01",
        "localName": "New Year's Day",
        "name": "New Year's Day",
        "countryCode": "GB",
        "fixed": True,
        "global": True,
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
        "types": ["Public", "Bank"],
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


class FakeClient:
    """Minimal client stand-in: records calls, returns a deep copy."""

    def __init__(self, result=None, error=None) -> None:
        self.result = result
        self.error = error
        self.calls: list[tuple[int, str]] = []

    def get_holidays(self, year: int, country_code: str):
        self.calls.append((year, country_code))
        if self.error is not None:
            raise self.error
        return copy.deepcopy(self.result)


# -- success path ------------------------------------------------------------


def test_get_holidays_returns_enriched_outcome_and_calls_client_positionally():
    client = FakeClient(copy.deepcopy(HOLIDAYS))

    outcome = operations.get_holidays(client, "  gb  ", year=2025)

    assert isinstance(outcome, Outcome)
    assert outcome.failure is None
    assert outcome.warnings == ()
    assert client.calls == [(2025, "GB")]  # same positional call shape as MCP
    assert [h["day_of_week"] for h in outcome.data] == [
        "Wednesday",
        "Friday",
        "Monday",
        "Friday",
    ]
    # Upstream fields/order retained, enrichment appended.
    assert list(outcome.data[0]) == list(HOLIDAYS[0]) + ["day_of_week"]


@pytest.mark.parametrize(
    "today",
    [date(2031, 7, 4), (lambda: date(2031, 7, 4))],
    ids=["date-value", "clock-callable"],
)
def test_default_year_uses_injected_today_at_call_time(today):
    client = FakeClient([])

    operations.get_holidays(client, "GB", today=today)

    assert client.calls == [(2031, "GB")]


def test_explicit_year_wins_over_injected_today():
    client = FakeClient([])

    operations.get_holidays(client, "GB", year=2025, today=date(2031, 7, 4))

    assert client.calls == [(2025, "GB")]


def test_none_response_is_no_data_failure_with_country_details():
    outcome = operations.get_holidays(FakeClient(None), "GB", year=2025)

    assert outcome.data is None
    assert outcome.warnings == ()
    assert outcome.failure is not None
    assert outcome.failure.code == "no_data"
    # Domain carrier, not a pre-rendered MCP sentence (P2 correction 4).
    assert outcome.failure.details == {"country": "GB"}
    assert "No holiday data available" not in outcome.failure.message
    assert outcome.failure.cause is None


# -- filtering + warning kinds ----------------------------------------------


def test_type_filter_miss_warns_and_keeps_supplied_text():
    client = FakeClient(copy.deepcopy(HOLIDAYS))

    outcome = operations.get_holidays(client, "GB", year=2025, type="School ")

    assert outcome.failure is None
    assert outcome.data == []
    assert outcome.warnings == (
        Warning(TYPE_NO_MATCHES, {"type": "School ", "country": "GB"}),
    )


def test_type_filter_hit_has_no_warning_and_matches_case_insensitively():
    client = FakeClient(copy.deepcopy(HOLIDAYS))

    outcome = operations.get_holidays(client, "GB", year=2025, type="bank")

    assert outcome.warnings == ()
    assert [h["name"] for h in outcome.data] == ["Good Friday", "St Patrick's Day"]


def test_county_filter_miss_warns_national_only_and_keeps_global_rows():
    client = FakeClient(copy.deepcopy(HOLIDAYS))

    outcome = operations.get_holidays(client, "GB", year=2025, county=" zz-nope ")

    assert outcome.failure is None
    assert {h["name"] for h in outcome.data} == {
        "New Year's Day",
        "Good Friday",
        "Valentine's Day",
    }
    assert outcome.warnings == (
        Warning(COUNTY_NATIONAL_ONLY, {"county": "ZZ-NOPE"}),
    )


def test_county_filter_regional_hit_has_no_warning():
    client = FakeClient(copy.deepcopy(HOLIDAYS))

    outcome = operations.get_holidays(client, "GB", year=2025, county="gb-nir")

    assert outcome.warnings == ()
    assert {h["name"] for h in outcome.data} == {
        "New Year's Day",
        "Good Friday",
        "St Patrick's Day",
        "Valentine's Day",
    }


def test_type_warning_precedes_county_warning_when_both_apply():
    client = FakeClient(copy.deepcopy(HOLIDAYS))

    outcome = operations.get_holidays(
        client, "GB", year=2025, type="School", county="zz-nope"
    )

    assert outcome.data == []
    assert [w.code for w in outcome.warnings] == [
        TYPE_NO_MATCHES,
        COUNTY_NATIONAL_ONLY,
    ]
    assert outcome.warnings[0].details == {"type": "School", "country": "GB"}
    assert outcome.warnings[1].details == {"county": "ZZ-NOPE"}


# -- source-shape behaviour: MCP-compatible, not strict ---------------------


def test_null_types_field_preserves_type_error_provenance():
    client = FakeClient(
        [{"date": "2025-01-01", "name": "X", "types": None}]
    )

    outcome = operations.get_holidays(client, "GB", year=2025, type="Public")

    assert outcome.data is None
    assert isinstance(outcome.failure.cause, TypeError)
    assert str(outcome.failure.cause) == "'NoneType' object is not iterable"


def test_object_body_without_date_is_passed_through_untouched():
    client = FakeClient({"name": "Oops"})

    outcome = operations.get_holidays(client, "GB", year=2025)

    assert outcome.failure is None
    assert outcome.data == {"name": "Oops"}


def test_object_body_with_date_keeps_type_error_provenance():
    client = FakeClient({"date": "2025-01-01", "name": "Oops"})

    outcome = operations.get_holidays(client, "GB", year=2025)

    assert isinstance(outcome.failure.cause, TypeError)
    assert str(outcome.failure.cause) == "'str' object does not support item assignment"


def test_malformed_date_in_record_gets_null_day_of_week_only():
    client = FakeClient(
        [
            {"date": "not-a-date", "name": "A"},
            {"date": "2025-01-01", "name": "B"},
        ]
    )

    outcome = operations.get_holidays(client, "GB", year=2025)

    assert outcome.failure is None
    assert outcome.data[0]["day_of_week"] is None
    assert outcome.data[1]["day_of_week"] == "Wednesday"


# -- failure provenance -----------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [urllib.error.URLError("down"), ValueError("bad input")],
    ids=["urlerror", "valueerror"],
)
def test_request_failure_keeps_original_exception_object(error):
    outcome = operations.get_holidays(FakeClient(error=error), "GB", year=2025)

    assert outcome.data is None
    assert outcome.failure.code == "request_failed"
    assert outcome.failure.cause is error
    assert outcome.failure.message == str(error)


def test_interface_supplied_validator_rejection_is_invalid_response():
    def validate(payload):
        raise operations.InvalidResponse("bad payload shape")

    outcome = operations.get_holidays(
        FakeClient([1]), "GB", year=2025, validate=validate
    )

    assert outcome.failure.code == "invalid_response"
    assert isinstance(outcome.failure.cause, operations.InvalidResponse)
    assert outcome.failure.message == "bad payload shape"


def test_no_validator_means_no_extra_strictness():
    """Without an interface validator, the operation layer must not reject
    shapes MCP currently accepts (e.g. a bare object body)."""
    outcome = operations.get_holidays(FakeClient({"name": "Oops"}), "GB", year=2025)

    assert outcome.failure is None
    assert outcome.data == {"name": "Oops"}


# -- helpers re-exported through the server module -------------------------


def test_helpers_are_shared_public_operations():
    assert operations.day_of_week("2025-01-01") == "Wednesday"
    assert operations.enrich_holidays(None) == []
    holidays = [{"date": "2025-01-01"}]
    assert operations.enrich_holidays(holidays)[0]["day_of_week"] == "Wednesday"
    # Underscored aliases are what server re-exports for backwards compat.
    assert operations._day_of_week is operations.day_of_week
    assert operations._enrich_holidays is operations.enrich_holidays


# -- client timeout plumbing (narrow, default call shape preserved) --------


def seam(status: int = 200, text: str = "[]", path: str = "/x") -> SeamResponse:
    return SeamResponse(status_code=status, text=text, url=client_mod.BASE_URL + path)


class RecordingSeam:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bool, float | None]] = []

    def __call__(self, url: str, *args, **kwargs):
        self.calls.append((url, bool(args), kwargs.get("timeout")))
        return seam(text="[]")


def test_client_default_timeout_preserves_no_override_call_shape(monkeypatch):
    fake = RecordingSeam()
    monkeypatch.setattr(client_mod, "fetch_raw", fake)
    client = client_mod.NagerDateClient()

    client.get("/PublicHolidays/2025/GB", cache_ttl=0)
    client.is_today_holiday("GB")

    assert fake.calls == [
        (client_mod.BASE_URL + "/PublicHolidays/2025/GB", False, None),
        (client_mod.BASE_URL + "/IsTodayPublicHoliday/GB", False, None),
    ]


def test_client_configured_timeout_reaches_ordinary_get_and_today_check(monkeypatch):
    fake = RecordingSeam()
    monkeypatch.setattr(client_mod, "fetch_raw", fake)
    client = client_mod.NagerDateClient(timeout=2.5)

    client.get_holidays(2025, "GB")
    client.is_today_holiday("GB")

    assert fake.calls == [
        (client_mod.BASE_URL + "/PublicHolidays/2025/GB", False, 2.5),
        (client_mod.BASE_URL + "/IsTodayPublicHoliday/GB", False, 2.5),
    ]


# -- failure provenance stays positional-compatible (P2 correction 4) ------


def test_failure_constructor_keeps_positional_cause_compatibility():
    error = ValueError("bad")
    failure = operations.Failure("request_failed", "bad", error)

    assert failure.cause is error
    assert failure.details == {}

    no_data = operations.Failure("no_data", "no holiday data", None, {"country": "GB"})
    assert no_data.details == {"country": "GB"}


# -- adapter owns its outward no_data sentence (P2 correction 4) ------------


def test_mcp_adapter_renders_the_exact_legacy_no_data_sentence(monkeypatch):
    from nager_date_mcp import server

    class NoDataClient:
        def get_holidays(self, year, country_code):
            return None

    monkeypatch.setattr(server, "_client", NoDataClient())

    assert server.get_holidays(" in ", year=2025) == "No holiday data available for IN."


# -- P3: the six remaining operations --------------------------------------
#
# Same interface-neutral contract as get_holidays: domain data plus ordered
# warnings and original failure provenance. The client is injected and every
# endpoint-specific normalisation asymmetry is preserved (MCP next/holidays
# county filters strip+upper; MCP today forwards its county raw).


class RecordingBackend:
    """Stand-in client with per-endpoint responses/exceptions and call log."""

    def __init__(self, **responses) -> None:
        self.responses = responses
        self.calls: list[tuple[str, tuple, dict]] = []

    def _call(self, name: str, args: tuple, kwargs: dict):
        self.calls.append((name, args, kwargs))
        value = self.responses.get(name, [])
        if isinstance(value, BaseException):
            raise value
        return copy.deepcopy(value)

    def get_next_holidays(self, country_code):
        return self._call("get_next_holidays", (country_code,), {})

    def get_long_weekends(self, year, country_code):
        return self._call("get_long_weekends", (year, country_code), {})

    def is_today_holiday(self, country_code, county_code=None, offset=None):
        return self._call(
            "is_today_holiday",
            (country_code,),
            {"county_code": county_code, "offset": offset},
        )

    def get_available_countries(self):
        return self._call("get_available_countries", (), {})

    def get_country_info(self, country_code):
        return self._call("get_country_info", (country_code,), {})

    def get_worldwide_holidays(self):
        return self._call("get_worldwide_holidays", (), {})


NEXT = [
    {"date": "2026-08-01", "name": "Regional Day", "counties": ["GB-SCT"],
     "global": False, "types": ["Public"]},
    {"date": "2026-08-15", "name": "National Day", "counties": None,
     "global": True, "types": ["Public"]},
]

LONG_WEEKENDS = [
    {"startDate": "2026-01-17", "endDate": "2026-01-19", "dayCount": 3,
     "needBridgeDay": False, "bridgeDays": []},
    {"startDate": "2026-06-18", "endDate": "2026-06-21", "dayCount": 4,
     "needBridgeDay": True, "bridgeDays": ["2026-06-19"]},
]


def test_next_holidays_filters_normalized_county_without_warning_on_hit():
    client = RecordingBackend(get_next_holidays=NEXT)

    outcome = operations.get_next_holidays(client, " gb ", county=" gb-sct ")

    assert outcome.failure is None
    assert [h["name"] for h in outcome.data] == ["Regional Day", "National Day"]
    assert outcome.warnings == ()
    assert client.calls == [("get_next_holidays", ("GB",), {})]


def test_next_holidays_county_miss_warns_without_claiming_regional_rows():
    client = RecordingBackend(get_next_holidays=NEXT)

    outcome = operations.get_next_holidays(client, "GB", county="GB-WLS")

    assert outcome.failure is None
    assert [h["name"] for h in outcome.data] == ["National Day"]
    assert outcome.warnings[0].code == COUNTY_NATIONAL_ONLY
    assert outcome.warnings[0].details["county"] == "GB-WLS"


def test_next_holidays_none_is_no_data_with_country_details():
    outcome = operations.get_next_holidays(RecordingBackend(get_next_holidays=None), "in")

    assert outcome.failure is not None
    assert outcome.failure.code == "no_data"
    assert outcome.failure.details == {"country": "IN"}
    assert outcome.failure.cause is None


def test_next_holidays_empty_list_is_a_success():
    outcome = operations.get_next_holidays(RecordingBackend(get_next_holidays=[]), "GB")

    assert outcome.failure is None
    assert outcome.data == []


def test_next_holidays_request_failure_keeps_original_cause():
    error = urllib.error.URLError("down")
    outcome = operations.get_next_holidays(
        RecordingBackend(get_next_holidays=error), "GB"
    )

    assert outcome.failure.code == "request_failed"
    assert outcome.failure.cause is error


def test_long_weekends_none_is_no_data():
    outcome = operations.get_long_weekends(RecordingBackend(get_long_weekends=None), "in", 2026)

    assert outcome.failure.code == "no_data"
    assert outcome.failure.details == {"country": "IN"}


def test_long_weekends_empty_supported_is_success_and_checks_catalogue():
    client = RecordingBackend(
        get_long_weekends=[],
        get_available_countries=[{"countryCode": "US", "name": "United States"}],
    )

    outcome = operations.get_long_weekends(client, " us ", 2026)

    assert outcome.failure is None
    assert outcome.data == []
    assert [call[0] for call in client.calls] == [
        "get_long_weekends", "get_available_countries",
    ]


def test_long_weekends_empty_unsupported_is_no_data():
    client = RecordingBackend(
        get_long_weekends=[],
        get_available_countries=[{"countryCode": "US", "name": "United States"}],
    )

    outcome = operations.get_long_weekends(client, "IN", 2026)

    assert outcome.failure.code == "no_data"
    assert outcome.failure.details == {"country": "IN"}


def test_long_weekends_support_lookup_failure_is_request_failure():
    error = urllib.error.URLError("catalogue down")
    client = RecordingBackend(get_long_weekends=[], get_available_countries=error)

    outcome = operations.get_long_weekends(client, "GB", 2026)

    assert outcome.failure.code == "request_failed"
    assert outcome.failure.cause is error


def test_long_weekends_nonempty_retains_records_and_order():
    outcome = operations.get_long_weekends(
        RecordingBackend(get_long_weekends=LONG_WEEKENDS), "US", 2026
    )

    assert outcome.failure is None
    assert outcome.data == LONG_WEEKENDS


def test_long_weekends_default_year_uses_private_today_seam_at_call_time(monkeypatch):
    client = RecordingBackend(get_long_weekends=LONG_WEEKENDS)
    years = iter([2029, 2030])
    monkeypatch.setattr(operations, "_today", lambda: date(next(years), 12, 31))

    assert operations.get_long_weekends(client, "GB").failure is None
    assert operations.get_long_weekends(client, "GB").failure is None

    assert client.calls[0][1] == (2029, "GB")
    assert client.calls[1][1] == (2030, "GB")


def test_long_weekends_explicit_year_wins_over_today(monkeypatch):
    client = RecordingBackend(get_long_weekends=LONG_WEEKENDS)
    monkeypatch.setattr(operations, "_today", lambda: date(2029, 12, 31))

    operations.get_long_weekends(client, "GB", 2026)

    assert client.calls[0][1] == (2026, "GB")


def test_today_true_returns_uppercase_country_and_boolean():
    client = RecordingBackend(is_today_holiday=True)

    outcome = operations.is_today_holiday(client, " pe ")

    assert outcome.data == {"is_holiday": True, "country": "PE"}
    assert client.calls == [
        ("is_today_holiday", ("PE",), {"county_code": None, "offset": None})
    ]


def test_today_204_supported_is_false_success():
    client = RecordingBackend(
        is_today_holiday=None,
        get_available_countries=[{"countryCode": "GB", "name": "United Kingdom"}],
    )

    outcome = operations.is_today_holiday(client, "GB")

    assert outcome.data == {"is_holiday": False, "country": "GB"}


def test_today_204_unsupported_is_no_data():
    client = RecordingBackend(
        is_today_holiday=None,
        get_available_countries=[{"countryCode": "US", "name": "United States"}],
    )

    outcome = operations.is_today_holiday(client, "IN")

    assert outcome.failure.code == "no_data"
    assert outcome.failure.details == {"country": "IN"}


def test_today_support_lookup_failure_is_request_failure_not_false():
    error = TimeoutError("slow catalogue")
    client = RecordingBackend(is_today_holiday=None, get_available_countries=error)

    outcome = operations.is_today_holiday(client, "GB")

    assert outcome.failure.code == "request_failed"
    assert outcome.failure.cause is error


def test_today_forwards_mcp_county_raw_and_keeps_explicit_zero_offset():
    client = RecordingBackend(is_today_holiday=True)

    operations.is_today_holiday(client, "GB", county=" gb-eng ", offset=0)

    assert client.calls == [
        ("is_today_holiday", ("GB",),
         {"county_code": " gb-eng ", "offset": 0})
    ]


@pytest.mark.parametrize("legacy_value", [False, 0, "", "yes"])
def test_today_non_true_non_none_result_is_a_legacy_false_without_lookup(
    legacy_value,
):
    """Characterizes the defensive legacy branch (False/other != True/None).

    This is the current contract, not a redesigned domain answer: the branch
    returns ``is_holiday: false`` and never performs a support lookup.
    """
    client = RecordingBackend(
        is_today_holiday=legacy_value,
        get_available_countries=AssertionError("support lookup must not run"),
    )

    outcome = operations.is_today_holiday(client, "GB")

    assert outcome.failure is None
    assert outcome.data == {"is_holiday": False, "country": "GB"}
    assert [call[0] for call in client.calls] == ["is_today_holiday"]


def test_countries_maps_code_name_and_applies_trimmed_substring_query():
    client = RecordingBackend(
        get_available_countries=[
            {"countryCode": "US", "name": "United States"},
            {"countryCode": "GB", "name": "United Kingdom"},
            {"countryCode": "DE", "name": "Germany"},
        ]
    )

    outcome = operations.list_countries(client, "  united  ")

    assert outcome.failure is None
    assert outcome.data == [
        {"code": "US", "name": "United States"},
        {"code": "GB", "name": "United Kingdom"},
    ]


@pytest.mark.parametrize("query", [None, "", "   "])
def test_countries_omitted_or_whitespace_query_returns_all(query):
    client = RecordingBackend(
        get_available_countries=[
            {"countryCode": "US", "name": "United States"},
            {"countryCode": "GB", "name": "United Kingdom"},
        ]
    )

    outcome = operations.list_countries(client, query)

    assert [c["code"] for c in outcome.data] == ["US", "GB"]


def test_countries_empty_catalogue_is_success():
    outcome = operations.list_countries(RecordingBackend(get_available_countries=[]))

    assert outcome.failure is None
    assert outcome.data == []


def test_countries_cli_validator_rejects_unusable_payload_but_mcp_does_not():
    payload = [{"name": "No code"}]
    client = RecordingBackend(get_available_countries=payload)

    mcp_outcome = operations.list_countries(client)
    cli_outcome = operations.list_countries(
        client, validate=_countries_validator
    )

    assert mcp_outcome.failure.code == "request_failed"
    assert isinstance(mcp_outcome.failure.cause, KeyError)
    assert cli_outcome.failure.code == "invalid_response"


def test_country_info_none_is_historical_mcp_null_success():
    outcome = operations.get_country_info(RecordingBackend(get_country_info=None), "gb")

    assert outcome.failure is None
    assert outcome.data is None


def test_country_info_cli_validator_rejects_non_object():
    outcome = operations.get_country_info(
        RecordingBackend(get_country_info=None),
        "GB",
        validate=_country_info_validator,
    )

    assert outcome.failure.code == "invalid_response"


def test_country_info_object_is_preserved():
    info = {"commonName": "United Kingdom", "region": "Europe",
            "borders": [{"name": "Ireland", "code": "IE"}]}
    outcome = operations.get_country_info(
        RecordingBackend(get_country_info=info), " gb ", validate=_country_info_validator
    )

    assert outcome.failure is None
    assert outcome.data == info


def test_worldwide_204_becomes_empty_success_not_no_data():
    outcome = operations.get_worldwide_holidays(
        RecordingBackend(get_worldwide_holidays=None),
        validate=_holidays_validator,
    )

    assert outcome.failure is None
    assert outcome.data == []


def test_worldwide_enriches_records_with_weekday_and_keeps_order():
    records = [
        {"date": "2026-11-19", "name": "B", "counties": []},
        {"date": "2026-11-20", "name": "A", "counties": None},
    ]

    outcome = operations.get_worldwide_holidays(
        RecordingBackend(get_worldwide_holidays=records),
        validate=_holidays_validator,
    )

    assert [h["name"] for h in outcome.data] == ["B", "A"]
    assert [h["day_of_week"] for h in outcome.data] == ["Thursday", "Friday"]


def test_worldwide_cli_validator_rejects_non_list_payload():
    outcome = operations.get_worldwide_holidays(
        RecordingBackend(get_worldwide_holidays={"not": "a list"}),
        validate=_holidays_validator,
    )

    assert outcome.failure.code == "invalid_response"


# -- P3: MCP adapter parity over the shared operations ---------------------
#
# The server keeps its handler signatures, its single `_client` instance and
# its exact outward shapes; only the domain logic moves to operations.


def test_mcp_today_forwards_county_raw_while_next_normalizes_county(monkeypatch):
    from nager_date_mcp import server

    class RawCountyClient(RecordingBackend):
        def get_next_holidays(self, country_code):
            return self._call("get_next_holidays", (country_code,), {})

    client = RawCountyClient(
        is_today_holiday=True,
        get_next_holidays=[
            {"date": "2026-08-01", "name": "Regional", "counties": ["GB-SCT"]},
            {"date": "2026-08-15", "name": "National", "counties": None,
             "global": True},
        ],
    )
    monkeypatch.setattr(server, "_client", client)

    assert server.is_today_holiday("GB", county=" gb-eng ") == {
        "is_holiday": True, "country": "GB",
    }
    assert client.calls[0] == (
        "is_today_holiday", ("GB",), {"county_code": " gb-eng ", "offset": None}
    )

    result = server.get_next_holidays("GB", county=" gb-sct ")
    assert [h["name"] for h in result] == ["Regional", "National"]
    assert client.calls[1] == ("get_next_holidays", ("GB",), {})


def test_mcp_country_info_keeps_historical_null(monkeypatch):
    from nager_date_mcp import server

    monkeypatch.setattr(server, "_client", RecordingBackend(get_country_info=None))

    assert server.get_country_info("GB") is None


def test_mcp_malformed_countries_payload_still_raises_for_dispatch(monkeypatch):
    from nager_date_mcp import server

    monkeypatch.setattr(
        server, "_client", RecordingBackend(get_available_countries=[{"name": "X"}])
    )

    with pytest.raises(KeyError):
        server.list_countries()


def test_mcp_next_county_miss_keeps_legacy_note_text(monkeypatch):
    from nager_date_mcp import server

    monkeypatch.setattr(
        server, "_client", RecordingBackend(get_next_holidays=NEXT)
    )

    result = server.get_next_holidays("GB", county="gb-wls")

    assert result == {
        "holidays": [
            {
                "date": "2026-08-15",
                "name": "National Day",
                "counties": None,
                "global": True,
                "types": ["Public"],
                "day_of_week": "Saturday",
            }
        ],
        "note": "note: no regional holidays found for county 'GB-WLS'; "
                "showing national holidays only",
    }


def test_mcp_worldwide_204_still_returns_empty_list(monkeypatch):
    from nager_date_mcp import server

    monkeypatch.setattr(server, "_client", RecordingBackend(get_worldwide_holidays=None))

    assert server.get_worldwide_holidays() == []
