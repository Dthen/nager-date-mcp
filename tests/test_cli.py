"""Tests for the native ``nager-date`` CLI (all seven commands).

HTTP is faked ONLY at the existing ``nager_date_mcp.client.fetch_raw`` seam;
the real client, operations layer and CLI run. The expected outcomes are the
actual domain results, never hand-written CLI output fixtures.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import urllib.error
from datetime import date
from email.message import Message
from pathlib import Path

import pytest

from conftest import V2
from nager_date_mcp import cli
from nager_date_mcp import client as client_mod
from nager_date_mcp import operations
from nager_date_mcp.transport import SeamResponse

REPO = Path(__file__).resolve().parents[1]
BASE_URL = "https://date.nager.at/api/v3"
_MISSING = object()  # sentinel: the key is absent, not JSON null

HOLIDAYS = [
    {
        "date": "2025-01-01",
        "localName": "New Year's Day",
        "name": "New Year's Day",
        "countryCode": "GB",
        "fixed": True,
        "global": True,
        "counties": ["GB-ENG", "GB-NIR"],
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
]


class FakeSeam:
    """Queue of seam responses/exceptions, recording (url, timeout)."""

    def __init__(self, *responses) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, float | None]] = []

    def __call__(self, url: str, timeout=None) -> SeamResponse:
        self.calls.append((url, timeout))
        item = self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]
        if isinstance(item, BaseException):
            raise item
        return item


def seam(status: int = 200, text: str = "", path: str = "/PublicHolidays/2025/GB"):
    return SeamResponse(status_code=status, text=text, url=BASE_URL + path)


@pytest.fixture
def install(monkeypatch):
    def _install(*responses) -> FakeSeam:
        fake = FakeSeam(*responses)
        monkeypatch.setattr(client_mod, "fetch_raw", fake)
        return fake

    return _install


def run(argv):
    return cli.main(list(argv))


def json_out(capsys):
    out, err = capsys.readouterr()
    return json.loads(out), out, err


# -- management actions: help / version ------------------------------------


@pytest.mark.parametrize(
    "argv, marker",
    [
        (["--help"], "usage: nager-date"),
        (["holidays", "--help"], "usage: nager-date"),
        (["--version"], "nager-date 0.3.0"),
        (["holidays", "--version"], "nager-date 0.3.0"),
    ],
    ids=["help", "subcommand-help", "version", "subcommand-version"],
)
def test_management_actions_print_stdout_exit_zero_offline(
    argv, marker, monkeypatch, capsys
):
    def no_client(*args, **kwargs):
        raise AssertionError("managed action must not construct the client")

    monkeypatch.setattr(cli, "NagerDateClient", no_client)
    monkeypatch.setattr(
        client_mod,
        "fetch_raw",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("network touched")),
    )

    assert run(argv) == 0
    out, err = capsys.readouterr()
    assert marker in out
    assert err == ""


def test_help_wins_over_json_mode(monkeypatch, capsys):
    monkeypatch.setattr(cli, "NagerDateClient", lambda *a, **k: pytest.fail("client"))

    assert run(["--json", "--help"]) == 0
    out, err = capsys.readouterr()
    assert out.startswith("usage: nager-date")
    assert '"data"' not in out
    assert err == ""


def test_main_returns_integer_status(capsys):
    assert isinstance(cli.main(["--help"]), int)
    capsys.readouterr()


# -- usage errors -----------------------------------------------------------


def test_missing_command_is_usage_error_without_menu(capsys):
    assert run([]) == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert "usage: nager-date" in err
    assert "required" in err


def test_usage_error_json_mode_is_selected_by_exact_token_in_value_slot(capsys):
    assert run(["holidays", "--year", "--json"]) == 2
    out, err = capsys.readouterr()
    assert err == ""
    obj = json.loads(out)
    assert list(obj) == ["data", "warnings", "error"]
    assert obj["data"] is None
    assert obj["warnings"] == []
    assert obj["error"]["code"] == "usage_error"
    assert obj["error"]["message"]


def test_json_text_used_as_option_value_does_not_select_json_mode(capsys, install):
    install(seam(text="[]"))

    assert run(["holidays", "GB", "--type=--json"]) == 0
    out, err = capsys.readouterr()
    assert out == "No holidays found.\n"
    assert err == ""


def test_json_after_double_dash_does_not_select_json_mode(capsys, install):
    install(seam(text="[]"))

    assert run(["holidays", "GB", "--", "--json"]) == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert "unrecognized arguments" in err


@pytest.mark.parametrize(
    "argv",
    [
        ["--json", "holidays", "GB", "--year", "2025"],
        ["holidays", "GB", "--json", "--year", "2025"],
        ["--json", "holidays", "--year", "2025", "GB"],
        ["--json", "--json", "holidays", "GB", "--year", "2025"],
    ],
    ids=["before", "after", "mixed", "repeated-json"],
)
def test_common_option_placement_and_repeated_json(argv, capsys, install):
    install(seam(text=json.dumps(HOLIDAYS)))

    assert run(argv) == 0
    obj, out, err = json_out(capsys)
    assert err == ""
    assert obj["error"] is None
    assert len(obj["data"]) == 2
    assert out.count("\n") == 1


@pytest.mark.parametrize(
    "option, values",
    [
        ("--year", ("2024", "2025")),
        ("--type", ("Public", "Bank")),
        ("--county", ("GB-ENG", "GB-NIR")),
        ("--timeout", ("1", "2")),
    ],
)
def test_repeated_value_taking_options_are_usage_errors(option, values, capsys):
    argv = ["holidays", "GB", option, values[0], option, values[1]]
    assert run(argv) == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert "may only be given once" in err


@pytest.mark.parametrize("country", ["G", "GBR", "G1", "G B", "\u00c4B", ""])
def test_invalid_country_is_usage_error(country, capsys):
    assert run(["holidays", country]) == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert "usage: nager-date" in err


@pytest.mark.parametrize(
    "county", ["GB", "GB-", "-ENG", "GB-E&G", "GB ENG", "GB_ENG", "GB-ENG?"]
)
def test_invalid_county_is_usage_error(county, capsys):
    assert run(["holidays", "GB", "--county", county]) == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert "--county" in err


@pytest.mark.parametrize("year", ["abc", "2024.0", ""])
def test_invalid_year_is_usage_error(year, capsys):
    assert run(["holidays", "GB", "--year", year]) == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert "--year" in err


@pytest.mark.parametrize("timeout", ["0", "-1", "nan", "inf", "-inf", "abc", ""])
def test_invalid_timeout_is_usage_error_before_io(timeout, capsys, install):
    fake = install(seam(text="[]"))

    assert run(["holidays", "GB", "--timeout", timeout]) == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert "--timeout" in err
    assert fake.calls == []  # rejected before any request


def test_extra_positional_is_usage_error(capsys, install):
    install(seam(text="[]"))
    assert run(["holidays", "GB", "extra"]) == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert "unrecognized arguments" in err


def test_long_option_abbreviation_is_disabled(capsys, install):
    install(seam(text="[]"))
    assert run(["holidays", "GB", "--yea", "2025"]) == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert "unrecognized arguments" in err


# -- P6: usage line follows the selected command ----------------------------


@pytest.mark.parametrize(
    "argv, prefix",
    [
        ([], "usage: nager-date [-h]"),
        (["--bogus"], "usage: nager-date [-h]"),
        (["--county", "GB-ENG", "holidays", "GB"], "usage: nager-date [-h]"),
        (["holidays"], "usage: nager-date holidays [-h]"),
        (["next-holidays"], "usage: nager-date next-holidays [-h]"),
        (["countries", "--query"], "usage: nager-date countries [-h]"),
        (["holidays", "GB", "--bogus"], "usage: nager-date holidays [-h]"),
        (["holidays", "GB", "--year", "abc"], "usage: nager-date holidays [-h]"),
        (
            ["is-today-holiday", "GB", "--offset", "soon"],
            "usage: nager-date is-today-holiday [-h]",
        ),
    ],
    ids=[
        "no-command",
        "unknown-root",
        "option-before-command",
        "missing-country",
        "missing-country-next",
        "missing-value",
        "unknown-command-local",
        "typed-year",
        "typed-offset",
    ],
)
def test_usage_line_matches_command_scope(argv, prefix, monkeypatch, capsys):
    def no_client(*args, **kwargs):
        raise AssertionError("usage failures must not construct the client")

    monkeypatch.setattr(cli, "NagerDateClient", no_client)

    assert run(argv) == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert err.splitlines()[0].startswith(prefix)


# -- scanner/argparse agreement for ordinary value tokens -------------------


@pytest.mark.parametrize(
    "value", ["-", "-.5", "-0.5", "-12"], ids=["lone-dash", "point-five", "zero-point-five", "integer"]
)
def test_type_accepts_value_tokens_argparse_also_accepts(value, capsys, install):
    fake = install(seam(text=json.dumps(HOLIDAYS)))

    assert run(["--json", "holidays", "GB", "--year", "2025", "--type", value]) == 0
    obj, out, err = json_out(capsys)
    assert err == ""
    assert obj["data"] == []
    assert obj["warnings"][0]["code"] == "type_no_matches"
    assert value in obj["warnings"][0]["message"]
    assert fake.calls  # reached the operation, not rejected by the preflight scanner


@pytest.mark.parametrize("value", ["", "Nope"], ids=["empty", "unknown"])
def test_empty_or_unknown_type_is_valid_and_warns(value, capsys, install):
    install(seam(text=json.dumps(HOLIDAYS)))

    assert run(["--json", "holidays", "GB", "--type", value]) == 0
    obj, out, err = json_out(capsys)
    assert err == ""
    assert obj["data"] == []
    assert obj["warnings"] == [
        {
            "code": "type_no_matches",
            "message": (
                f"no holidays matched type '{value}' for GB "
                "(this country may use a different type label)"
            ),
        }
    ]


def test_timeout_before_command_reaches_transport(capsys, install):
    fake = install(seam(text="[]"))

    assert run(["--timeout", "2.5", "holidays", "GB"]) == 0
    capsys.readouterr()
    assert fake.calls[0][1] == 2.5


def test_invalid_timeout_before_command_is_usage_error_before_io(capsys, install):
    fake = install(seam(text="[]"))

    assert run(["--timeout", "0", "holidays", "GB"]) == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert "--timeout" in err
    assert fake.calls == []


def test_type_missing_value_before_json_stays_usage_error(capsys, install):
    install(seam(text="[]"))

    assert run(["--json", "holidays", "GB", "--type", "--json"]) == 2
    obj, out, err = json_out(capsys)
    assert err == ""
    assert obj["error"]["code"] == "usage_error"


# -- success / empty / warnings --------------------------------------------


def test_json_success_shape_fields_and_unicode(capsys, install):
    body = json.dumps(
        [
            {
                "date": "2025-11-19",
                "name": "Bu\u00df- und Bettag",
                "localName": "Bu\u00df- und Bettag",
                "countryCode": "DE",
                "types": ["Public"],
                "counties": None,
                "global": True,
                "fixed": False,
                "launchYear": None,
            }
        ],
        ensure_ascii=False,
    )
    fake = install(seam(text=body, path="/PublicHolidays/2025/DE"))

    assert run(["--json", "holidays", " de ", "--year", "2025"]) == 0
    obj, out, err = json_out(capsys)
    assert err == ""
    assert out.endswith("\n") and out.count("\n") == 1
    assert "\u00df" in out and "\\u00df" not in out
    assert list(obj) == ["data", "warnings", "error"]
    assert obj["error"] is None and obj["warnings"] == []
    holiday = obj["data"][0]
    assert holiday["name"] == "Bu\u00df- und Bettag"
    assert holiday["day_of_week"] == "Wednesday"
    assert holiday["global"] is True
    assert holiday["launchYear"] is None
    assert list(holiday)[-1] == "day_of_week"
    # Same positional client path as MCP, uppercased, default transport timeout.
    assert fake.calls == [(BASE_URL + "/PublicHolidays/2025/DE", None)]


def test_json_empty_result_is_success(capsys, install):
    install(seam(text="[]"))

    assert run(["--json", "holidays", "GB"]) == 0
    obj, out, err = json_out(capsys)
    assert err == ""
    assert obj == {"data": [], "warnings": [], "error": None}


def test_timeout_option_reaches_transport(capsys, install):
    fake = install(seam(text="[]"))

    assert run(["--json", "holidays", "GB", "--timeout", "2.5"]) == 0
    capsys.readouterr()
    assert fake.calls[0][1] == 2.5


def test_type_warning_json(capsys, install):
    install(seam(text=json.dumps(HOLIDAYS)))

    assert run(["--json", "holidays", "GB", "--type", "school"]) == 0
    obj, out, err = json_out(capsys)
    assert err == ""
    assert obj["data"] == []
    assert obj["warnings"] == [
        {
            "code": "type_no_matches",
            "message": (
                "no holidays matched type 'school' for GB "
                "(this country may use a different type label)"
            ),
        }
    ]


def test_county_warning_never_claims_national_rows_survived(capsys, install):
    install(seam(text=json.dumps(HOLIDAYS)))

    assert run(["--json", "holidays", "GB", "--type", "school",
                "--county", "zz-nope"]) == 0
    obj, out, err = json_out(capsys)
    assert err == ""
    assert [w["code"] for w in obj["warnings"]] == [
        "type_no_matches",
        "county_national_only",
    ]
    county_message = obj["warnings"][1]["message"]
    assert "ZZ-NOPE" in county_message
    assert "showing national" not in county_message
    assert "national holidays" not in county_message


def test_human_success_renders_date_name_and_coverage(capsys, install):
    install(seam(text=json.dumps(HOLIDAYS)))

    assert run(["holidays", "GB", "--year", "2025"]) == 0
    out, err = capsys.readouterr()
    assert err == ""
    assert "2025-01-01" in out
    assert "Wednesday" in out
    assert "New Year's Day" in out
    assert "[GB-ENG, GB-NIR]" in out
    assert "[national]" in out


def test_human_empty_result_is_short_and_successful(capsys, install):
    install(seam(text="[]"))

    assert run(["holidays", "GB"]) == 0
    out, err = capsys.readouterr()
    assert out == "No holidays found.\n"
    assert err == ""


def test_human_warnings_go_to_stderr_with_codes(capsys, install):
    install(seam(text=json.dumps(HOLIDAYS)))

    assert run(["holidays", "GB", "--type", "school", "--county", "zz-nope"]) == 0
    out, err = capsys.readouterr()
    assert out == "No holidays found.\n"
    assert "warning: type_no_matches:" in err
    assert "warning: county_national_only:" in err
    assert "showing national" not in err


def test_human_output_escapes_remote_control_characters(capsys, install):
    body = json.dumps(
        [
            {
                "date": "2025-01-01",
                "name": "Bad\u001b[31mName\nSecond",
                "types": ["Public"],
                "counties": None,
                "global": True,
            }
        ]
    )
    install(seam(text=body))

    assert run(["holidays", "GB"]) == 0
    out, err = capsys.readouterr()
    assert "\u001b" not in out
    assert "\\x1b[31m" in out
    assert "\\nSecond" in out


def test_json_output_escapes_remote_control_characters(capsys, install):
    body = json.dumps(
        [
            {
                "date": "2025-01-01",
                "name": "Bad\u001b[31mName",
                "types": ["Public"],
                "counties": None,
                "global": True,
            }
        ]
    )
    install(seam(text=body))

    assert run(["--json", "holidays", "GB"]) == 0
    out, err = capsys.readouterr()
    assert "\u001b" not in out
    assert "\\u001b" in out


# -- no_data and typed failures --------------------------------------------


def test_no_data_is_operation_failure(capsys, install):
    install(seam(status=204, text=""))

    assert run(["--json", "holidays", "IN"]) == 1
    obj, out, err = json_out(capsys)
    assert err == ""
    assert obj["data"] is None
    assert obj["warnings"] == []
    assert obj["error"] == {
        "code": "no_data",
        "message": "No holiday data available for IN.",
    }


def test_no_data_human_stdout_empty(capsys, install):
    install(seam(status=204, text=""))

    assert run(["holidays", "IN"]) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert "no_data" in err
    assert "No holiday data available for IN." in err


@pytest.mark.parametrize(
    "response, expected_code",
    [
        (seam(status=404, text="nope", path="/PublicHolidays/2025/ZZ"), "not_found"),
        (seam(status=400, text='{"errors":{"year":["bad year"]}}'), "invalid_request"),
        (seam(status=429, text="slow down"), "rate_limited"),
        (seam(status=500, text="boom"), "upstream_error"),
        (urllib.error.URLError("connection refused"), "network_error"),
        (urllib.error.URLError(TimeoutError("read timed out")), "timeout"),
        (TimeoutError("direct timeout"), "timeout"),
    ],
    ids=["404", "400", "429", "500", "urlerror", "urlerror-timeout", "timeout"],
)
def test_typed_failures_json(response, expected_code, capsys, install):
    install(response)

    assert run(["--json", "holidays", "GB"]) == 1
    obj, out, err = json_out(capsys)
    assert err == ""
    assert obj["data"] is None and obj["warnings"] == []
    assert obj["error"]["code"] == expected_code
    assert obj["error"]["message"]


def test_typed_failure_human_stdout_empty_stderr_has_code(capsys, install):
    install(seam(status=404, text="nope", path="/PublicHolidays/2025/ZZ"))

    assert run(["holidays", "ZZ"]) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert "not_found" in err
    assert "Country not found: ZZ" in err


# -- P6: friendly HTTP error text without an orphan colon -------------------


@pytest.mark.parametrize(
    "status, code", [(429, "rate_limited"), (500, "upstream_error")]
)
@pytest.mark.parametrize("json_mode", [True, False], ids=["json", "human"])
def test_empty_http_reason_never_leaves_an_orphan_colon(
    status, code, json_mode, capsys, install
):
    install(
        urllib.error.HTTPError(
            BASE_URL + "/PublicHolidays/2025/GB", status, "", Message(), None
        )
    )
    argv = ["--json", "holidays", "GB"] if json_mode else ["holidays", "GB"]

    assert run(argv) == 1
    out, err = capsys.readouterr()
    if json_mode:
        assert err == ""
        error = json.loads(out)["error"]
        assert error["code"] == code
        message = error["message"]
    else:
        assert out == ""
        assert f"error: {code}:" in err
        message = err.strip()
    assert message and not message.rstrip().endswith(":")
    assert "HTTP Error" in message


@pytest.mark.parametrize("json_mode", [True, False], ids=["json", "human"])
def test_useful_http_reason_is_preserved_for_429(json_mode, capsys, install):
    install(
        urllib.error.HTTPError(
            BASE_URL + "/PublicHolidays/2025/GB",
            429,
            "Too Many Requests",
            Message(),
            None,
        )
    )
    argv = ["--json", "holidays", "GB"] if json_mode else ["holidays", "GB"]

    assert run(argv) == 1
    out, err = capsys.readouterr()
    if json_mode:
        assert err == ""
        assert json.loads(out)["error"] == {
            "code": "rate_limited",
            "message": "HTTP Error 429: Too Many Requests",
        }
    else:
        assert out == ""
        assert "error: rate_limited: HTTP Error 429: Too Many Requests" in err


def test_invalid_json_body_is_invalid_response(capsys, install):
    install(seam(text="{not json"))

    assert run(["--json", "holidays", "GB"]) == 1
    obj, out, err = json_out(capsys)
    assert err == ""
    assert obj["error"]["code"] == "invalid_response"


def test_invalid_utf8_is_invalid_response(capsys, install):
    install(UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"))

    assert run(["--json", "holidays", "GB"]) == 1
    obj, out, err = json_out(capsys)
    assert err == ""
    assert obj["error"]["code"] == "invalid_response"


@pytest.mark.parametrize(
    "body",
    [
        '{"name":"Oops"}',
        "[1, 2]",
        '[{"date":"2025-01-01"}]',
        '[{"date":"2025-01-01","name":"X","types":null}]',
        '[{"date":"2025-01-01","name":"X","counties":"GB-ENG"}]',
        '[{"date":"2025-01-01","name":"X","global":"yes"}]',
    ],
    ids=[
        "top-level-object",
        "non-object-records",
        "missing-name",
        "null-types",
        "string-counties",
        "non-bool-global",
    ],
)
def test_wrong_shape_is_invalid_response_not_silent_success(body, capsys, install):
    install(seam(text=body))

    assert run(["--json", "holidays", "GB"]) == 1
    obj, out, err = json_out(capsys)
    assert err == ""
    assert obj["data"] is None
    assert obj["error"]["code"] == "invalid_response"


# -- tolerant date rows (P2 correction 5, CLI rows included) ----------------


@pytest.mark.parametrize(
    "date_value, expected_weekday, date_present",
    [
        ("2025-11-19", "Wednesday", True),
        ("not-a-date", None, True),
        (None, None, True),
        (20250101, None, True),
        (_MISSING, None, False),
    ],
    ids=["valid-string", "malformed-string", "null", "numeric", "missing"],
)
def test_date_tolerance_group_keeps_rows_and_null_weekday(
    date_value, expected_weekday, date_present, capsys, install
):
    record = {"name": "X"}
    if date_value is not _MISSING:
        record["date"] = date_value
    install(seam(text=json.dumps([record])))

    assert run(["--json", "holidays", "GB"]) == 0
    obj, out, err = json_out(capsys)
    assert err == ""
    assert len(obj["data"]) == 1
    row = obj["data"][0]
    assert row["name"] == "X"
    if date_present:
        assert row["date"] == date_value  # original value preserved
        assert row["day_of_week"] == expected_weekday
    else:
        assert "date" not in row
        assert "day_of_week" not in row  # not invented for a missing date


@pytest.mark.parametrize(
    "date_value",
    ["2025-11-19", "not-a-date", None, 20250101, _MISSING],
    ids=["valid-string", "malformed-string", "null", "numeric", "missing"],
)
def test_date_tolerance_group_human_rendering_is_safe(
    date_value, capsys, install
):
    record = {"name": "X"}
    if date_value is not _MISSING:
        record["date"] = date_value
    install(seam(text=json.dumps([record])))

    assert run(["holidays", "GB"]) == 0
    out, err = capsys.readouterr()
    assert err == ""
    assert "X" in out
    assert "None" not in out


def test_nonfinite_number_is_invalid_response(capsys, install):
    install(seam(text='[{"date":"2025-01-01","name":"X","score": NaN}]'))

    assert run(["--json", "holidays", "GB"]) == 1
    obj, out, err = json_out(capsys)
    assert err == ""
    assert obj["error"]["code"] == "invalid_response"


def test_unclassified_value_error_is_internal_error(capsys, install):
    install(ValueError("kaboom"))

    assert run(["--json", "holidays", "GB"]) == 1
    obj, out, err = json_out(capsys)
    assert err == ""
    assert obj["error"] == {"code": "internal_error", "message": "Internal error."}


def test_programmer_error_is_not_routed_to_invalid_response(monkeypatch, capsys, install):
    install(seam(text="[]"))

    def boom(*args, **kwargs):
        raise RuntimeError("unexpected bug")

    # The command registry binds the operation object, so an unexpected
    # failure is injected at the guarded dispatch boundary instead of by
    # monkeypatching the operations module after binding.
    monkeypatch.setattr(cli, "_run_command", boom)

    assert run(["--json", "holidays", "GB"]) == 1
    obj, out, err = json_out(capsys)
    assert err == ""
    assert obj["error"]["code"] == "internal_error"


# -- interruption / output failure -----------------------------------------


def test_ctrl_c_before_output_is_structured_and_130(capsys, install):
    install(KeyboardInterrupt())

    assert run(["--json", "holidays", "GB"]) == 130
    obj, out, err = json_out(capsys)
    assert err == ""
    assert obj["data"] is None and obj["warnings"] == []
    assert obj["error"]["code"] == "interrupted"


def test_ctrl_c_human_is_quiet_and_130(capsys, install):
    install(KeyboardInterrupt())

    assert run(["holidays", "GB"]) == 130
    out, err = capsys.readouterr()
    assert out == ""
    assert "interrupted" in err


def test_output_failure_is_quiet_nonzero(monkeypatch, capsys, install):
    install(seam(text="[]"))
    calls = []

    def broken_write(text, encoding=None):
        calls.append(text)
        raise BrokenPipeError("closed")

    monkeypatch.setattr(cli, "_write", broken_write)

    assert run(["--json", "holidays", "GB"]) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err == ""
    assert len(calls) == 1  # no retry result after the failed write


# -- P6: the devnull descriptor opened on output failure is closed ----------


class _BrokenBuffer:
    def write(self, data):
        raise BrokenPipeError("closed")

    def flush(self):
        pass


class _BrokenStdout:
    """Minimal stdout whose buffered write fails, with a real fileno()."""

    def __init__(self, fd, fileno_error=None):
        self.buffer = _BrokenBuffer()
        self._fd = fd
        self._fileno_error = fileno_error

    def fileno(self):
        if self._fileno_error is not None:
            raise self._fileno_error
        return self._fd


@pytest.mark.parametrize(
    "fileno_error",
    [None, ValueError("no usable fileno")],
    ids=["dup2-ok", "fileno-unusable"],
)
def test_output_failure_closes_the_devnull_descriptor_it_opens(
    fileno_error, monkeypatch
):
    scratch = os.open(os.devnull, os.O_WRONLY)  # stands in for fd 1
    real_open = os.open
    opened: list[int] = []

    def spy_open(path, flags, *args, **kwargs):
        fd = real_open(path, flags, *args, **kwargs)
        opened.append(fd)
        return fd

    monkeypatch.setattr(cli.sys, "stdout", _BrokenStdout(scratch, fileno_error))
    monkeypatch.setattr(cli.os, "open", spy_open)
    try:
        assert cli._write("payload") is False
        assert len(opened) == 1
        with pytest.raises(OSError):
            os.fstat(opened[0])  # closed: the owned descriptor did not leak
        os.fstat(scratch)  # the stdout sink itself is still valid
    finally:
        os.close(scratch)


def test_closed_pipe_subprocess_keeps_stdout_sink_without_fd_leak(tmp_path):
    probe = tmp_path / "closed_pipe_probe.py"
    probe.write_text(
        "import json, os, sys\n"
        "from nager_date_mcp import cli\n"
        "\n"
        "def fd_count():\n"
        "    return len(os.listdir('/proc/self/fd'))\n"
        "\n"
        "before = fd_count()\n"
        "status = cli.main(['--version'])\n"
        "after = fd_count()\n"
        "with open(sys.argv[1], 'w', encoding='utf-8') as handle:\n"
        "    json.dump({'status': status, 'before': before, 'after': after}, handle)\n",
        encoding="utf-8",
    )
    report = tmp_path / "report.json"
    read_fd, write_fd = os.pipe()
    os.close(read_fd)  # no reader: the child's stdout writes fail with EPIPE
    try:
        proc = subprocess.run(
            [V2, str(probe), str(report)],
            stdout=write_fd,
            stderr=subprocess.PIPE,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            timeout=60,
        )
    finally:
        os.close(write_fd)

    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    assert proc.stderr == b""
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["status"] == 1
    assert data["after"] == data["before"]


# -- R1: the replacement sink survives when open() reuses stdout's fd --------


def test_output_failure_keeps_the_sink_when_open_reuses_stdout_fd(monkeypatch):
    scratch = os.open(os.devnull, os.O_WRONLY)  # stands in for the closed fd 1
    dup2_calls: list[tuple[int, int]] = []

    monkeypatch.setattr(cli.sys, "stdout", _BrokenStdout(scratch))
    with monkeypatch.context() as patch_ctx:

        def reuse_open(path, flags, *args, **kwargs):
            # A closed fd 1: open() hands back the same descriptor number.
            return scratch

        patch_ctx.setattr(cli.os, "open", reuse_open)
        patch_ctx.setattr(cli.os, "dup2", lambda a, b: dup2_calls.append((a, b)))
        try:
            assert cli._write("payload") is False
            assert dup2_calls == []  # same descriptor: dup2 would be a no-op
            os.fstat(scratch)  # the replacement sink itself is still open
        finally:
            try:
                os.close(scratch)
            except OSError:
                pass


def test_output_failure_closes_owned_descriptor_when_dup2_fails(monkeypatch):
    scratch = os.open(os.devnull, os.O_WRONLY)
    real_open = os.open
    opened: list[int] = []

    def spy_open(path, flags, *args, **kwargs):
        fd = real_open(path, flags, *args, **kwargs)
        opened.append(fd)
        return fd

    def broken_dup2(source, target):
        raise OSError("dup2 failed")

    monkeypatch.setattr(cli.sys, "stdout", _BrokenStdout(scratch))
    with monkeypatch.context() as patch_ctx:
        patch_ctx.setattr(cli.os, "open", spy_open)
        patch_ctx.setattr(cli.os, "dup2", broken_dup2)
        assert cli._write("payload") is False
    assert len(opened) == 1
    with pytest.raises(OSError):
        os.fstat(opened[0])  # owned descriptor closed on the failing dup2 path
    try:
        os.fstat(scratch)  # the original sink stays valid
    finally:
        os.close(scratch)


def test_closed_stdout_fd_real_shutdown_exits_1_with_empty_output(tmp_path):
    probe = tmp_path / "closed_fd1_probe.py"
    probe.write_text(
        "import os\n"
        "from nager_date_mcp import cli\n"
        "os.close(1)\n"
        "raise SystemExit(cli.main(['--version']))\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [V2, "-B", str(probe)],
        capture_output=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        timeout=30,
    )

    # Real interpreter shutdown must not turn the quiet status 1 into 120 or
    # emit an "Exception ignored ... Bad file descriptor" line.
    assert proc.returncode == 1, proc.stderr.decode("utf-8", "replace")
    assert proc.stdout == b""
    assert proc.stderr == b""


# -- R2: streams missing from interpreter startup (fd closed before exec) ---


@pytest.mark.parametrize(
    "closed_fd, argv, expected_status",
    [
        (1, ["--version"], 1),
        (1, ["--json", "--bogus"], 1),
        (2, ["--bogus"], 2),
    ],
    ids=[
        "stdout-missing-version",
        "stdout-missing-json-usage",
        "stderr-missing-usage",
    ],
)
def test_missing_startup_stream_fails_quietly(
    closed_fd, argv, expected_status
):
    """fd 1/2 closed before exec leaves sys.stdout/sys.stderr as None."""
    proc = subprocess.run(
        [V2, "-B", "-m", "nager_date_mcp.cli", *argv],
        capture_output=True,
        preexec_fn=lambda: os.close(closed_fd),
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        timeout=30,
    )

    assert proc.returncode == expected_status, proc.stderr.decode(
        "utf-8", "replace"
    )
    assert proc.stdout == b""
    assert proc.stderr == b""


def _one_shot_json_interrupt(monkeypatch):
    real = cli._json_text
    fired = {"once": False}

    def one_shot(*args, **kwargs):
        if not fired["once"]:
            fired["once"] = True
            raise KeyboardInterrupt()
        return real(*args, **kwargs)

    monkeypatch.setattr(cli, "_json_text", one_shot)


@pytest.mark.parametrize(
    "argv, response",
    [
        (["--json", "--bogus"], None),
        (["--json", "holidays", "GB"], seam(status=500, text="boom")),
        (["--json", "holidays", "GB"], seam(text="[]")),
    ],
    ids=["usage-serialization", "failure-serialization", "success-serialization"],
)
def test_ctrl_c_during_json_serialization_is_structured_130(
    argv, response, monkeypatch, capsys, install
):
    install(*([response] if response is not None else []))
    _one_shot_json_interrupt(monkeypatch)

    assert run(argv) == 130
    obj, out, err = json_out(capsys)
    assert err == ""
    assert obj["data"] is None and obj["warnings"] == []
    assert obj["error"]["code"] == "interrupted"


@pytest.mark.parametrize(
    "argv, response, expected_stdout",
    [
        (["--bogus"], None, ""),
        (["holidays", "GB"], seam(status=500, text="boom"), ""),
        (
            ["holidays", "GB", "--type", "school"],
            seam(text=json.dumps(HOLIDAYS)),
            "No holidays found.\n",
        ),
    ],
    ids=["usage-diagnostics", "failure-diagnostics", "warning-after-result"],
)
def test_ctrl_c_during_human_diagnostics_never_traces_or_claims_zero(
    argv, response, expected_stdout, monkeypatch, capsys, install
):
    install(*([response] if response is not None else []))

    def interrupted_stderr(text):
        raise KeyboardInterrupt()

    monkeypatch.setattr(cli, "_write_stderr", interrupted_stderr)

    assert run(argv) == 130
    out, err = capsys.readouterr()
    assert out == expected_stdout
    assert err == ""


def test_interrupt_handler_is_guarded_against_repeated_interrupt(
    monkeypatch, capsys, install
):
    install(seam(status=500, text="boom"))

    def interrupted_stderr(text):
        raise KeyboardInterrupt()

    monkeypatch.setattr(cli, "_write_stderr", interrupted_stderr)

    assert run(["holidays", "GB"]) == 130
    out, err = capsys.readouterr()
    assert out == ""
    assert err == ""


@pytest.mark.parametrize(
    "argv, marker", [(["--help"], True), (["--version"], False)], ids=["help", "version"]
)
def test_ctrl_c_during_management_is_quiet_130(argv, marker, monkeypatch, capsys):
    def interrupted_emit(*args, **kwargs):
        raise KeyboardInterrupt()

    monkeypatch.setattr(cli, "_emit", interrupted_emit)

    assert run(argv) == 130
    out, err = capsys.readouterr()
    assert out == ""
    assert "interrupted" in err


# -- F1: interrupt after a management write attempt has begun ---------------


@pytest.mark.parametrize(
    "argv, prefix",
    [
        (["--help"], "usage"),
        (["holidays", "--help"], "usage"),
        (["--version"], "nager"),
        (["holidays", "--version"], "nager"),
        (["--json", "--help"], "usage"),
        (["--json", "holidays", "--help"], "usage"),
        (["--json", "--version"], "nager"),
        (["--json", "holidays", "--version"], "nager"),
    ],
    ids=[
        "help-human",
        "subcommand-help-human",
        "version-human",
        "subcommand-version-human",
        "help-json",
        "subcommand-help-json",
        "version-json",
        "subcommand-version-json",
    ],
)
def test_ctrl_c_after_management_write_begins_is_quiet_130(
    argv, prefix, monkeypatch, capsys
):
    calls = []

    def prefix_then_interrupt(text, encoding=None):
        calls.append(text)
        sys.stdout.write(text[:5])
        sys.stdout.flush()
        raise KeyboardInterrupt()

    monkeypatch.setattr(cli, "_write", prefix_then_interrupt)

    assert run(argv) == 130
    out, err = capsys.readouterr()
    assert out == prefix  # only the partial first write; no replacement envelope
    assert err == ""  # quiet, no traceback or interruption prose
    assert len(calls) == 1  # no second stdout attempt


# -- F2: interrupt during startup / parser construction ---------------------


@pytest.mark.parametrize(
    "argv, json_expected",
    [
        (["--help"], False),
        (["holidays", "GB"], False),
        (["--json", "--help"], True),
        (["--json", "holidays", "GB"], True),
    ],
    ids=["help-human", "run-human", "help-json", "run-json"],
)
def test_ctrl_c_during_parser_construction_is_130_and_keeps_json_rule(
    argv, json_expected, monkeypatch, capsys
):
    def interrupted_build(*args, **kwargs):
        raise KeyboardInterrupt()

    monkeypatch.setattr(cli, "_build_parser", interrupted_build)

    assert run(argv) == 130
    out, err = capsys.readouterr()
    if json_expected:
        obj = json.loads(out)
        assert obj["data"] is None and obj["warnings"] == []
        assert obj["error"]["code"] == "interrupted"
        assert err == ""
    else:
        assert out == ""
        assert "interrupted" in err


@pytest.mark.parametrize(
    "argv, json_expected",
    [(["--help"], False), (["--json", "--help"], True)],
    ids=["human", "json"],
)
def test_ctrl_c_during_json_mode_selection_is_130_and_falls_back_to_argv_rule(
    argv, json_expected, monkeypatch, capsys
):
    def interrupted_selection(*args, **kwargs):
        raise KeyboardInterrupt()

    monkeypatch.setattr(cli, "_json_requested", interrupted_selection)

    assert run(argv) == 130
    out, err = capsys.readouterr()
    if json_expected:
        obj = json.loads(out)
        assert obj["error"]["code"] == "interrupted"
        assert err == ""
    else:
        assert out == ""
        assert "interrupted" in err


def test_startup_interrupt_fallback_is_itself_guarded(monkeypatch, capsys):
    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt()

    monkeypatch.setattr(cli, "_json_requested", interrupted)
    monkeypatch.setattr(cli, "_json_mode_from_argv", interrupted)

    assert run(["--json", "--help"]) == 130
    out, err = capsys.readouterr()
    assert out == ""
    assert "interrupted" in err


# -- P6: interrupt during the initial argv copy -----------------------------


class _InterruptingArgv:
    """Iterable that yields its tokens and then raises Ctrl-C once."""

    def __init__(self, tokens):
        self._tokens = tokens

    def __iter__(self):
        yield from self._tokens
        raise KeyboardInterrupt()


@pytest.mark.parametrize(
    "argv, json_expected",
    [
        (["--json"], True),
        (["--json", "holidays"], True),
        (["holidays", "GB"], False),
    ],
    ids=["json-only", "json-prefix", "human"],
)
def test_ctrl_c_while_copying_argv_is_130_without_traceback(
    argv, json_expected, monkeypatch, capsys
):
    def no_client(*args, **kwargs):
        raise AssertionError("interrupted startup must not construct the client")

    monkeypatch.setattr(cli, "NagerDateClient", no_client)

    assert cli.main(_InterruptingArgv(argv)) == 130
    out, err = capsys.readouterr()
    if json_expected:
        assert err == ""
        obj = json.loads(out)
        assert obj["data"] is None and obj["warnings"] == []
        assert obj["error"]["code"] == "interrupted"
    else:
        assert out == ""
        assert "interrupted" in err


# -- closed text-only streams (same output boundary) -----------------------


def test_closed_text_stdout_is_quiet_nonzero(monkeypatch, capsys, install):
    install(seam(text="[]"))
    stream = io.StringIO()
    stream.close()
    monkeypatch.setattr(cli.sys, "stdout", stream)

    assert cli.main(["--json", "holidays", "GB"]) == 1
    capsys.readouterr()


def test_closed_text_stderr_does_not_escape(monkeypatch, capsys, install):
    install(seam(text=json.dumps(HOLIDAYS)))
    stream = io.StringIO()
    stream.close()
    monkeypatch.setattr(cli.sys, "stderr", stream)

    assert cli.main(["holidays", "GB", "--type", "school"]) == 0
    capsys.readouterr()


@pytest.mark.parametrize(
    "argv, response", [(["--json", "holidays", "GB"], seam(text="[]")), (["holidays", "GB"], seam(text="[]"))],
    ids=["json-write", "human-write"],
)
def test_ctrl_c_during_stdout_write_is_quiet_130(
    argv, response, monkeypatch, capsys, install
):
    install(response)

    def interrupted_write(text, encoding=None):
        raise KeyboardInterrupt()

    monkeypatch.setattr(cli, "_write", interrupted_write)

    assert run(argv) == 130
    out, err = capsys.readouterr()
    assert out == ""
    assert err == ""


# -- encoding, replaced streams, surrogate escaping (P2 correction 3) -------


@pytest.mark.parametrize(
    "argv", [["--json", "holidays", "GB"], ["holidays", "GB"]],
    ids=["json", "human"],
)
def test_text_only_stdout_stream_without_buffer_is_supported(
    argv, monkeypatch, capsys, install
):
    body = json.dumps(
        [{"date": "2025-11-19", "name": "Bu\u00df- und Bettag"}], ensure_ascii=False
    )
    install(seam(text=body))
    stream = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", stream)

    assert cli.main(list(argv)) == 0
    assert "Bu\u00df- und Bettag" in stream.getvalue()
    capsys.readouterr()


def test_json_text_escapes_lone_surrogates_and_keeps_ordinary_unicode():
    text = cli._json_text([{"name": "Bu\u00df A\ud800B"}], (), None)

    assert "Bu\u00df" in text
    assert "\\ud800" in text
    assert "\ud800" not in text
    assert json.loads(text)["data"][0]["name"] == "Bu\u00df A\ud800B"


BYTE_HARNESS = r"""
import sys
from nager_date_mcp import cli
from nager_date_mcp import client as client_mod
from nager_date_mcp.transport import SeamResponse

BASE = "https://date.nager.at/api/v3"
body = sys.argv[1]
client_mod.fetch_raw = lambda url, timeout=None: SeamResponse(
    200, body, BASE + "/PublicHolidays/2025/GB"
)
raise SystemExit(cli.main(sys.argv[2:]))
"""

UNICODE_BODY = '[{"date": "2025-11-19", "name": "Bu\\u00df- und Bettag"}]'
SURROGATE_BODY = '[{"date": "2025-11-19", "name": "A\\ud800B"}]'

EXPECTED_UNICODE_JSON = (
    '{"data": [{"date": "2025-11-19", "name": "Bu\u00df- und Bettag", '
    '"day_of_week": "Wednesday"}], "warnings": [], "error": null}\n'
).encode("utf-8")

EXPECTED_SURROGATE_JSON = (
    '{"data": [{"date": "2025-11-19", "name": "A\\ud800B", '
    '"day_of_week": "Wednesday"}], "warnings": [], "error": null}\n'
).encode("utf-8")


def _run_bytes(tmp_path, body, argv, io_encoding):
    env = _clean_env()
    env["PYTHONIOENCODING"] = io_encoding
    return subprocess.run(
        [V2, "-c", BYTE_HARNESS, body, *argv],
        cwd=tmp_path,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=60,
    )


@pytest.mark.parametrize("io_encoding", ["ascii", "latin-1"])
def test_json_console_bytes_are_utf8_under_legacy_encodings(io_encoding, tmp_path):
    proc = _run_bytes(tmp_path, UNICODE_BODY, ["--json", "holidays", "GB"], io_encoding)

    assert proc.returncode == 0
    assert proc.stdout == EXPECTED_UNICODE_JSON  # UTF-8 bytes, ordinary Unicode intact
    assert proc.stderr == b""


@pytest.mark.parametrize("io_encoding", ["ascii", "latin-1"])
def test_json_surrogate_payload_is_escaped_under_legacy_encodings(io_encoding, tmp_path):
    proc = _run_bytes(tmp_path, SURROGATE_BODY, ["--json", "holidays", "GB"], io_encoding)

    assert proc.returncode == 0
    assert proc.stdout == EXPECTED_SURROGATE_JSON
    assert proc.stderr == b""


def test_human_console_bytes_backslash_escape_under_ascii(tmp_path):
    proc = _run_bytes(tmp_path, UNICODE_BODY, ["holidays", "GB"], "ascii")

    assert proc.returncode == 0
    assert proc.stdout == b"2025-11-19  Wednesday  Bu\\xdf- und Bettag  [national]\n"
    assert proc.stderr == b""


def test_human_console_bytes_use_stream_encoding_under_latin1(tmp_path):
    proc = _run_bytes(tmp_path, UNICODE_BODY, ["holidays", "GB"], "latin-1")

    assert proc.returncode == 0
    assert proc.stdout == b"2025-11-19  Wednesday  Bu\xdf- und Bettag  [national]\n"
    assert proc.stderr == b""


def test_warning_stderr_is_encoding_safe_under_ascii(tmp_path):
    body = '[{"date": "2025-11-19", "name": "X", "types": ["Public"]}]'
    proc = _run_bytes(tmp_path, body, ["holidays", "GB", "--type", "\u00df"], "ascii")

    expected = (
        "warning: type_no_matches: no holidays matched type '\u00df' for GB "
        "(this country may use a different type label)\n"
    ).encode("ascii", "backslashreplace")
    assert proc.returncode == 0
    assert proc.stdout == b"No holidays found.\n"
    assert proc.stderr == expected


# -- module launcher / packaging -------------------------------------------


def _clean_env():
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in ("PYTHONPATH", "PYTHONHOME")
    }
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def test_module_launcher_help_is_offline_outside_source_tree(tmp_path):
    proc = subprocess.run(
        [V2, "-m", "nager_date_mcp.cli", "--help"],
        cwd=tmp_path,
        env=_clean_env(),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0
    assert proc.stdout.startswith("usage: nager-date")
    assert proc.stderr == ""


def test_module_launcher_preserves_usage_status(tmp_path):
    proc = subprocess.run(
        [V2, "-m", "nager_date_mcp.cli", "holidays", "G"],
        cwd=tmp_path,
        env=_clean_env(),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert "usage: nager-date" in proc.stderr


def test_module_launcher_and_main_do_not_import_server(tmp_path):
    code = (
        "import sys; import nager_date_mcp.cli; "
        "assert 'nager_date_mcp.server' not in sys.modules"
    )
    proc = subprocess.run(
        [V2, "-c", code],
        cwd=tmp_path,
        env=_clean_env(),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr


HARNESS = """
import sys
from nager_date_mcp import cli
from nager_date_mcp import client as client_mod

def fake_fetch(url, timeout=None):
    raise ValueError("seam kaboom")

client_mod.fetch_raw = fake_fetch
raise SystemExit(cli.main(sys.argv[1:]))
"""


def test_launcher_harness_preserves_nonzero_main_status(tmp_path):
    proc = subprocess.run(
        [V2, "-c", HARNESS, "--json", "holidays", "GB"],
        cwd=tmp_path,
        env=_clean_env(),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 1
    obj = json.loads(proc.stdout)
    assert obj["error"]["code"] == "internal_error"


def test_pyproject_declares_console_script():
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert 'nager-date = "nager_date_mcp.cli:main"' in text


def test_html_error_body_is_not_emitted(capsys, install):
    install(seam(status=400, text="<html><body>Proxy denied</body></html>"))

    assert run(["--json", "holidays", "GB"]) == 1
    obj, out, err = json_out(capsys)
    assert err == ""
    assert obj["error"]["code"] == "invalid_request"
    assert "<html" not in out
    assert "Proxy denied" not in out


def test_html_fragment_error_body_uses_safe_summary(capsys, install):
    install(seam(status=400, text="<div>Proxy denied</div>"))

    assert run(["--json", "holidays", "GB"]) == 1
    obj, out, err = json_out(capsys)
    assert err == ""
    assert obj["error"]["code"] == "invalid_request"
    assert obj["error"]["message"] == "the server returned an HTML error page"
    assert "Proxy denied" not in out
    assert "<div>" not in out


def test_html_fragment_human_error_uses_safe_summary(capsys, install):
    install(seam(status=400, text="<div>Proxy denied</div>"))

    assert run(["holidays", "GB"]) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert "Proxy denied" not in err
    assert "<div>" not in err
    assert "the server returned an HTML error page" in err


def test_plain_api_validation_detail_is_preserved(capsys, install):
    install(seam(status=400, text='{"errors":{"year":["bad year"]}}'))

    assert run(["--json", "holidays", "GB"]) == 1
    obj, out, err = json_out(capsys)
    assert err == ""
    assert obj["error"]["code"] == "invalid_request"
    assert obj["error"]["message"] == "Bad request: bad year"


def test_human_failure_message_escapes_control_characters(capsys, install):
    install(seam(status=400, text="bad\u001b[31mrequest"))

    assert run(["holidays", "GB"]) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert "\u001b" not in err
    assert "\\x1b[31m" in err


def test_ctrl_c_while_rendering_is_structured_130(monkeypatch, capsys, install):
    install(seam(text="[]"))

    def interrupted_render(*args, **kwargs):
        raise KeyboardInterrupt()

    # Renderer selection is now registry-driven; interrupt it at the
    # selection boundary rather than at one concrete renderer.
    monkeypatch.setattr(cli, "_render_command_human", interrupted_render)

    assert run(["holidays", "GB"]) == 130
    out, err = capsys.readouterr()
    assert out == ""
    assert "interrupted" in err


@pytest.mark.parametrize(
    "argv",
    [
        ["--bogus", "--help"],
        ["--timeout", "0", "--help"],
        ["holidays", "G", "--help"],
        ["holidays", "GB", "extra", "--help"],
    ],
    ids=["unknown", "invalid-value", "invalid-country", "extra-positional"],
)
def test_parse_failure_before_management_follows_usage_path(argv, capsys):
    assert run(argv) == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert "usage: nager-date" in err


@pytest.mark.parametrize(
    "argv, marker",
    [
        (["--help", "--bogus"], "usage: nager-date"),
        (["--version", "--bogus"], "nager-date 0.3.0"),
        (["holidays", "--help", "--bogus"], "usage: nager-date holidays"),
    ],
    ids=["help-first", "version-first", "subcommand-help-first"],
)
def test_management_before_later_failure_terminates_zero(argv, marker, capsys):
    assert run(argv) == 0
    out, err = capsys.readouterr()
    assert marker in out
    assert err == ""


def test_json_usage_error_when_problem_precedes_help(capsys):
    assert run(["--json", "--bogus", "--help"]) == 2
    out, err = capsys.readouterr()
    assert err == ""
    assert json.loads(out)["error"]["code"] == "usage_error"


# ===========================================================================
# P3: the six remaining native commands
# ===========================================================================
#
# Same harness discipline as the holidays command: HTTP is faked only at the
# client seam, so real client/operations/CLI code runs and the asserted output
# is the actual domain result.

ALL_COMMANDS = (
    "holidays",
    "next-holidays",
    "long-weekends",
    "is-today-holiday",
    "countries",
    "country-info",
    "worldwide",
)


def test_command_registry_drives_help_options_and_dispatch_metadata():
    """P6: one registry entry per command feeds every consumer.

    Adding a command/option is one ``_COMMANDS`` edit; this pins that the
    parser help, country positional, allowed options, operation and renderer
    all agree with that single definition (no parallel hand-maintained lists).
    """
    assert list(cli._COMMANDS) == list(ALL_COMMANDS)
    parser = cli._build_parser()
    root_help = parser.format_help()
    for name, spec in cli._COMMANDS.items():
        assert spec.help in root_help
        sub = parser.command_parsers[name]
        assert sub.format_usage().startswith(f"usage: {cli.PROG} {name}")
        assert ("COUNTRY" in sub.format_usage()) is spec.takes_country
        rendered_help = sub.format_help()
        assert spec.description in rendered_help
        for option in spec.options:
            assert option.name in rendered_help
        assert callable(spec.operation)
        assert callable(spec.render)


NEXT_RECORDS = [
    {"date": "2026-08-01", "name": "Regional Day", "countryCode": "GB",
     "counties": ["GB-SCT"], "global": False, "types": ["Public"]},
    {"date": "2026-08-15", "name": "National Day", "countryCode": "GB",
     "counties": None, "global": True, "types": ["Public"]},
]

LONG_RECORDS = [
    {"startDate": "2026-01-17", "endDate": "2026-01-19", "dayCount": 3,
     "needBridgeDay": False, "bridgeDays": []},
    {"startDate": "2026-06-18", "endDate": "2026-06-21", "dayCount": 4,
     "needBridgeDay": True, "bridgeDays": ["2026-06-19"]},
]

COUNTRIES_BODY = [
    {"countryCode": "US", "name": "United States"},
    {"countryCode": "GB", "name": "United Kingdom"},
    {"countryCode": "DE", "name": "Germany"},
]

COUNTRY_INFO = {
    "commonName": "United Kingdom",
    "officialName": "United Kingdom of Great Britain and Northern Ireland",
    "countryCode": "GB",
    "region": "Europe",
    "borders": [{"name": "Ireland", "code": "IE"}],
}


def body(payload):
    return json.dumps(payload)


# -- discovery and parsing --------------------------------------------------


def test_root_help_discovers_all_seven_commands(capsys):
    assert run(["--help"]) == 0
    out, err = capsys.readouterr()
    assert err == ""
    for command in ALL_COMMANDS:
        assert command in out


@pytest.mark.parametrize(
    "command, markers",
    [
        ("holidays", ("--year", "--type", "--county")),
        ("next-holidays", ("--county",)),
        ("long-weekends", ("--year",)),
        ("is-today-holiday", ("--county", "--offset")),
        ("countries", ("--query",)),
        ("country-info", ("COUNTRY",)),
        ("worldwide", ("upcoming", "not only today's")),
    ],
)
def test_command_help_is_offline_and_lists_its_flags(command, markers, capsys, monkeypatch):
    def no_client(*args, **kwargs):
        raise AssertionError("help must not construct the client")

    monkeypatch.setattr(cli, "NagerDateClient", no_client)

    assert run([command, "--help"]) == 0
    out, err = capsys.readouterr()
    assert out.startswith(f"usage: nager-date {command}")
    assert err == ""
    for marker in markers:
        assert marker in out


@pytest.mark.parametrize(
    "argv",
    [
        ["--county", "GB-ENG", "holidays", "GB"],
        ["--year", "2026", "long-weekends", "GB"],
        ["countries", "--query", "a", "--query", "b"],
        ["is-today-holiday", "GB", "--offset", "soon"],
        ["is-today-holiday", "GB", "--offset", "1.5"],
        ["countries", "US"],
        ["worldwide", "GB"],
        ["country-info", "GB", "--county", "GB-ENG"],
        ["next-holidays"],
    ],
)
def test_new_command_usage_errors_never_construct_a_client(argv, monkeypatch, capsys):
    def no_client(*args, **kwargs):
        raise AssertionError("usage failures must not construct the client")

    monkeypatch.setattr(cli, "NagerDateClient", no_client)

    assert run(argv) == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert "usage: nager-date" in err


def test_missing_query_value_before_json_token_renders_json_usage_error(capsys):
    assert run(["countries", "--query", "--json"]) == 2
    obj, out, err = json_out(capsys)
    assert err == ""
    assert obj["error"]["code"] == "usage_error"
    assert "expected one argument" in obj["error"]["message"]


def test_json_looking_query_value_is_plain_text_without_json_mode(capsys, install):
    install(seam(200, "[]", path="/AvailableCountries"))

    assert run(["countries", "--query=--json"]) == 0
    out, err = capsys.readouterr()
    assert out == "No countries found.\n"
    assert err == ""


# -- next-holidays ----------------------------------------------------------


def test_next_holidays_json_success_enriches_and_filters_county(capsys, install):
    fake = install(seam(200, body(NEXT_RECORDS), path="/NextPublicHolidays/GB"))

    assert run(["--json", "next-holidays", "gb", "--county", "gb-sct"]) == 0
    obj, out, err = json_out(capsys)
    assert err == ""
    assert obj["error"] is None
    assert obj["warnings"] == []
    assert [h["name"] for h in obj["data"]] == ["Regional Day", "National Day"]
    assert [h["day_of_week"] for h in obj["data"]] == ["Saturday", "Saturday"]
    assert fake.calls == [(BASE_URL + "/NextPublicHolidays/GB", None)]


def test_next_holidays_county_miss_warns_on_stderr_with_code(capsys, install):
    install(seam(200, body(NEXT_RECORDS), path="/NextPublicHolidays/GB"))

    assert run(["next-holidays", "GB", "--county", "GB-WLS"]) == 0
    out, err = capsys.readouterr()
    assert "National Day" in out
    assert "Regional Day" not in out
    assert "warning: county_national_only:" in err
    assert "'GB-WLS'" in err
    assert "survived" not in err


def test_next_holidays_204_is_no_data_failure(capsys, install):
    install(seam(204, "", path="/NextPublicHolidays/IN"))

    assert run(["--json", "next-holidays", "IN"]) == 1
    obj, out, err = json_out(capsys)
    assert err == ""
    assert obj["data"] is None
    assert obj["warnings"] == []
    assert obj["error"]["code"] == "no_data"
    assert "IN" in obj["error"]["message"]


def test_next_holidays_empty_list_is_a_success(capsys, install):
    install(seam(200, "[]", path="/NextPublicHolidays/GB"))

    assert run(["--json", "next-holidays", "GB"]) == 0
    obj, _, err = json_out(capsys)
    assert err == ""
    assert obj["data"] == []
    assert obj["error"] is None


def test_next_holidays_malformed_date_keeps_row_and_null_weekday(capsys, install):
    records = [{"date": 20260815, "name": "Numeric Date", "counties": None}]
    install(seam(200, body(records), path="/NextPublicHolidays/GB"))

    assert run(["--json", "next-holidays", "GB"]) == 0
    obj, _, err = json_out(capsys)
    assert err == ""
    assert obj["data"][0]["date"] == 20260815
    assert obj["data"][0]["day_of_week"] is None


def test_next_holidays_timeout_reaches_the_request(capsys, install):
    fake = install(seam(200, "[]", path="/NextPublicHolidays/GB"))

    assert run(["--timeout", "2.5", "next-holidays", "GB"]) == 0
    capsys.readouterr()
    assert fake.calls == [(BASE_URL + "/NextPublicHolidays/GB", 2.5)]


def test_next_holidays_repeated_json_flag_is_harmless(capsys, install):
    install(seam(200, "[]", path="/NextPublicHolidays/GB"))

    assert run(["--json", "next-holidays", "GB", "--json"]) == 0
    obj, _, err = json_out(capsys)
    assert err == ""
    assert obj["data"] == []


# -- long-weekends ----------------------------------------------------------


def test_long_weekends_json_success_retains_upstream_fields(capsys, install):
    fake = install(seam(200, body(LONG_RECORDS), path="/LongWeekend/2026/GB"))

    assert run(["--json", "long-weekends", "GB", "--year", "2026"]) == 0
    obj, _, err = json_out(capsys)
    assert err == ""
    assert obj["data"] == LONG_RECORDS
    assert fake.calls == [(BASE_URL + "/LongWeekend/2026/GB", None)]


def test_long_weekends_supported_empty_is_success_with_support_lookup(capsys, install):
    fake = install(
        seam(200, "[]", path="/LongWeekend/2026/GB"),
        seam(200, body(COUNTRIES_BODY), path="/AvailableCountries"),
    )

    assert run(["--json", "long-weekends", "GB", "--year", "2026"]) == 0
    obj, _, err = json_out(capsys)
    assert err == ""
    assert obj["data"] == []
    assert obj["error"] is None
    assert [call[0] for call in fake.calls] == [
        BASE_URL + "/LongWeekend/2026/GB",
        BASE_URL + "/AvailableCountries",
    ]


def test_long_weekends_unsupported_empty_is_no_data(capsys, install):
    install(
        seam(200, "[]", path="/LongWeekend/2026/IN"),
        seam(200, body([{"countryCode": "US", "name": "United States"}]),
             path="/AvailableCountries"),
    )

    assert run(["--json", "long-weekends", "IN", "--year", "2026"]) == 1
    obj, _, err = json_out(capsys)
    assert err == ""
    assert obj["error"]["code"] == "no_data"
    assert "IN" in obj["error"]["message"]


def test_long_weekends_support_lookup_failure_is_not_no_data(capsys, install):
    install(
        seam(200, "[]", path="/LongWeekend/2026/GB"),
        TimeoutError("catalogue timeout"),
    )

    assert run(["--json", "long-weekends", "GB", "--year", "2026"]) == 1
    obj, _, err = json_out(capsys)
    assert err == ""
    assert obj["error"]["code"] == "timeout"
    assert obj["data"] is None


def test_long_weekends_malformed_catalogue_is_invalid_response(capsys, install):
    install(
        seam(200, "[]", path="/LongWeekend/2026/GB"),
        seam(200, body([{"name": "No code"}]), path="/AvailableCountries"),
    )

    assert run(["--json", "long-weekends", "GB", "--year", "2026"]) == 1
    obj, _, err = json_out(capsys)
    assert err == ""
    assert obj["error"]["code"] == "invalid_response"


def test_long_weekends_default_year_uses_host_local_call_time_seam(
    capsys, install, monkeypatch
):
    monkeypatch.setattr(operations, "_today", lambda: date(2031, 12, 31))
    fake = install(seam(200, body(LONG_RECORDS), path="/LongWeekend/2031/GB"))

    assert run(["--json", "long-weekends", "GB"]) == 0
    capsys.readouterr()
    assert fake.calls == [(BASE_URL + "/LongWeekend/2031/GB", None)]


def test_long_weekends_explicit_year_wins_over_today_seam(
    capsys, install, monkeypatch
):
    monkeypatch.setattr(operations, "_today", lambda: date(2031, 12, 31))
    fake = install(seam(200, body(LONG_RECORDS), path="/LongWeekend/2026/GB"))

    assert run(["--json", "long-weekends", "GB", "--year", "2026"]) == 0
    capsys.readouterr()
    assert fake.calls == [(BASE_URL + "/LongWeekend/2026/GB", None)]


def test_long_weekends_human_rows_show_bridge_details(capsys, install):
    install(seam(200, body(LONG_RECORDS), path="/LongWeekend/2026/GB"))

    assert run(["long-weekends", "GB", "--year", "2026"]) == 0
    out, err = capsys.readouterr()
    assert err == ""
    assert "2026-01-17 -> 2026-01-19  3 days  no bridge day" in out
    assert "2026-06-18 -> 2026-06-21  4 days  bridge: 2026-06-19" in out


# -- is-today-holiday -------------------------------------------------------


def test_today_200_with_empty_body_is_true_without_decoding(capsys, install):
    fake = install(seam(200, "not json at all", path="/IsTodayPublicHoliday/PE"))

    assert run(["--json", "is-today-holiday", "pe"]) == 0
    obj, _, err = json_out(capsys)
    assert err == ""
    assert obj["data"] == {"is_holiday": True, "country": "PE"}
    assert len(fake.calls) == 1  # no support lookup, no body decode


def test_today_204_supported_is_false_success(capsys, install):
    install(
        seam(204, "", path="/IsTodayPublicHoliday/GB"),
        seam(200, body([{"countryCode": "GB", "name": "United Kingdom"}]),
             path="/AvailableCountries"),
    )

    assert run(["--json", "is-today-holiday", "GB"]) == 0
    obj, _, err = json_out(capsys)
    assert err == ""
    assert obj["data"] == {"is_holiday": False, "country": "GB"}
    assert obj["error"] is None


def test_today_204_unsupported_is_no_data(capsys, install):
    install(
        seam(204, "", path="/IsTodayPublicHoliday/IN"),
        seam(200, body([{"countryCode": "US", "name": "United States"}]),
             path="/AvailableCountries"),
    )

    assert run(["--json", "is-today-holiday", "IN"]) == 1
    obj, _, err = json_out(capsys)
    assert err == ""
    assert obj["error"]["code"] == "no_data"


def test_today_support_lookup_failure_is_a_failure_not_false(capsys, install):
    install(seam(204, "", path="/IsTodayPublicHoliday/GB"), urllib.error.URLError("down"))

    assert run(["--json", "is-today-holiday", "GB"]) == 1
    obj, _, err = json_out(capsys)
    assert err == ""
    assert obj["error"]["code"] == "network_error"
    assert obj["data"] is None


def test_today_malformed_support_data_is_invalid_response_not_unsupported(capsys, install):
    install(
        seam(204, "", path="/IsTodayPublicHoliday/IN"),
        seam(200, body([{"name": "No code"}]), path="/AvailableCountries"),
    )

    assert run(["--json", "is-today-holiday", "IN"]) == 1
    obj, _, err = json_out(capsys)
    assert err == ""
    assert obj["error"]["code"] == "invalid_response"


def test_today_county_and_explicit_zero_offset_reach_the_url(capsys, install):
    fake = install(seam(200, "", path="/IsTodayPublicHoliday/GB"))

    assert run(["--json", "is-today-holiday", " gb ", "--county", "gb-eng",
                "--offset", "0"]) == 0
    capsys.readouterr()
    assert fake.calls[0][0] == (
        BASE_URL + "/IsTodayPublicHoliday/GB?countyCode=GB-ENG&offset=0"
    )

    assert run(["--json", "is-today-holiday", "GB"]) == 0
    capsys.readouterr()
    assert fake.calls[1][0] == BASE_URL + "/IsTodayPublicHoliday/GB"


def test_timeout_reaches_direct_and_support_lookups_with_one_client(
    capsys, install, monkeypatch
):
    constructed = []
    real_client = cli.NagerDateClient

    def counting_client(*args, **kwargs):
        constructed.append((args, kwargs))
        return real_client(*args, **kwargs)

    monkeypatch.setattr(cli, "NagerDateClient", counting_client)
    fake = install(
        seam(204, "", path="/IsTodayPublicHoliday/GB"),
        seam(200, body([{"countryCode": "GB", "name": "United Kingdom"}]),
             path="/AvailableCountries"),
    )

    assert run(["--timeout", "1.5", "is-today-holiday", "GB"]) == 0
    capsys.readouterr()
    assert constructed == [((), {"timeout": 1.5})]
    assert fake.calls == [
        (BASE_URL + "/IsTodayPublicHoliday/GB", 1.5),
        (BASE_URL + "/AvailableCountries", 1.5),
    ]


def test_today_negative_offset_equals_form(capsys, install):
    fake = install(seam(200, "", path="/IsTodayPublicHoliday/US"))

    assert run(["--json", "is-today-holiday", "US", "--offset=-5"]) == 0
    capsys.readouterr()
    assert fake.calls[0][0] == (
        BASE_URL + "/IsTodayPublicHoliday/US?offset=-5"
    )


def test_today_human_states_country_and_answer(capsys, install):
    install(seam(200, "", path="/IsTodayPublicHoliday/PE"))

    assert run(["is-today-holiday", "PE"]) == 0
    out, err = capsys.readouterr()
    assert out == "PE: yes, today is a public holiday.\n"
    assert err == ""


# -- countries --------------------------------------------------------------


def test_countries_json_maps_and_filters_with_trimmed_query(capsys, install):
    install(seam(200, body(COUNTRIES_BODY), path="/AvailableCountries"))

    assert run(["--json", "countries", "--query", "  united  "]) == 0
    obj, _, err = json_out(capsys)
    assert err == ""
    assert obj["data"] == [
        {"code": "US", "name": "United States"},
        {"code": "GB", "name": "United Kingdom"},
    ]


@pytest.mark.parametrize("query, expected", [("gb", ["GB"]), ("DE", ["DE"])])
def test_countries_query_matches_code_case_insensitively(query, expected, capsys, install):
    install(seam(200, body(COUNTRIES_BODY), path="/AvailableCountries"))

    assert run(["--json", "countries", "--query", query]) == 0
    obj, _, err = json_out(capsys)
    assert err == ""
    assert [c["code"] for c in obj["data"]] == expected


def test_countries_whitespace_query_returns_everything(capsys, install):
    install(seam(200, body(COUNTRIES_BODY), path="/AvailableCountries"))

    assert run(["--json", "countries", "--query", "   "]) == 0
    obj, _, err = json_out(capsys)
    assert err == ""
    assert [c["code"] for c in obj["data"]] == ["US", "GB", "DE"]


def test_countries_empty_is_success(capsys, install):
    install(seam(200, "[]", path="/AvailableCountries"))

    assert run(["--json", "countries"]) == 0
    obj, _, err = json_out(capsys)
    assert err == ""
    assert obj["data"] == []


@pytest.mark.parametrize(
    "payload",
    [
        [{"name": "No code"}],
        [{"countryCode": "US"}],
        {"not": "a list"},
        [42],
    ],
)
def test_countries_unusable_payload_is_invalid_response(payload, capsys, install):
    install(seam(200, body(payload), path="/AvailableCountries"))

    assert run(["--json", "countries"]) == 1
    obj, out, err = json_out(capsys)
    assert err == ""
    assert obj["data"] is None
    assert obj["error"]["code"] == "invalid_response"


def test_countries_human_lists_code_and_name(capsys, install):
    install(seam(200, body(COUNTRIES_BODY), path="/AvailableCountries"))

    assert run(["countries", "--query", "germany"]) == 0
    out, err = capsys.readouterr()
    assert out == "DE  Germany\n"
    assert err == ""


# -- country-info -----------------------------------------------------------


def test_country_info_json_preserves_upstream_object(capsys, install):
    fake = install(seam(200, body(COUNTRY_INFO), path="/CountryInfo/GB"))

    assert run(["--json", "country-info", "gb"]) == 0
    obj, _, err = json_out(capsys)
    assert err == ""
    assert obj["data"] == COUNTRY_INFO
    assert fake.calls == [(BASE_URL + "/CountryInfo/GB", None)]


def test_country_info_human_renders_labelled_fields(capsys, install):
    install(seam(200, body(COUNTRY_INFO), path="/CountryInfo/GB"))

    assert run(["country-info", "GB"]) == 0
    out, err = capsys.readouterr()
    assert err == ""
    assert "Common name: United Kingdom" in out
    assert "Region: Europe" in out
    assert "Borders: Ireland (IE)" in out


def test_country_info_empty_object_json_is_success_with_empty_data(capsys, install):
    install(seam(200, "{}", path="/CountryInfo/GB"))

    assert run(["--json", "country-info", "GB"]) == 0
    obj, out, err = json_out(capsys)
    assert err == ""
    assert obj == {"data": {}, "warnings": [], "error": None}


def test_country_info_empty_object_human_is_explicit_not_a_blank_line(capsys, install):
    install(seam(200, "{}", path="/CountryInfo/GB"))

    assert run(["country-info", "GB"]) == 0
    out, err = capsys.readouterr()
    assert out == "No country metadata returned.\n"
    assert err == ""


@pytest.mark.parametrize("payload", [None, [], "text", 3])
def test_country_info_missing_or_non_object_is_invalid_response(payload, capsys, install):
    if payload is None:
        install(seam(204, "", path="/CountryInfo/GB"))
    else:
        install(seam(200, body(payload), path="/CountryInfo/GB"))

    assert run(["--json", "country-info", "GB"]) == 1
    obj, out, err = json_out(capsys)
    assert err == ""
    assert obj["data"] is None
    assert obj["error"]["code"] == "invalid_response"


def test_country_info_human_failure_leaves_stdout_empty(capsys, install):
    install(seam(204, "", path="/CountryInfo/GB"))

    assert run(["country-info", "GB"]) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert "error: invalid_response:" in err


# -- worldwide --------------------------------------------------------------


def test_worldwide_json_preserves_upcoming_records_without_filtering(capsys, install):
    records = [
        {"date": "2026-12-24", "name": "Future Eve", "countryCode": "DE",
         "counties": None, "global": True},
        {"date": "2027-01-01", "name": "Next Year", "countryCode": "FR",
         "counties": [], "global": True},
    ]
    install(seam(200, body(records), path="/NextPublicHolidaysWorldwide"))

    assert run(["--json", "worldwide"]) == 0
    obj, _, err = json_out(capsys)
    assert err == ""
    assert [h["date"] for h in obj["data"]] == ["2026-12-24", "2027-01-01"]
    assert [h["day_of_week"] for h in obj["data"]] == ["Thursday", "Friday"]


def test_worldwide_missing_date_stays_absent_without_inventing_a_weekday(
    capsys, install
):
    records = [{"name": "No Date", "countryCode": "DE"}]
    install(seam(200, body(records), path="/NextPublicHolidaysWorldwide"))

    assert run(["--json", "worldwide"]) == 0
    obj, _, err = json_out(capsys)
    assert err == ""
    assert obj["data"] == [{"name": "No Date", "countryCode": "DE"}]


def test_worldwide_204_is_successful_empty_not_no_data(capsys, install):
    install(seam(204, "", path="/NextPublicHolidaysWorldwide"))

    assert run(["--json", "worldwide"]) == 0
    obj, _, err = json_out(capsys)
    assert err == ""
    assert obj["data"] == []
    assert obj["error"] is None


def test_worldwide_non_list_payload_is_invalid_response(capsys, install):
    install(seam(200, body({"not": "a list"}),
                 path="/NextPublicHolidaysWorldwide"))

    assert run(["--json", "worldwide"]) == 1
    obj, _, err = json_out(capsys)
    assert err == ""
    assert obj["error"]["code"] == "invalid_response"


def test_worldwide_human_empty_message(capsys, install):
    install(seam(204, "", path="/NextPublicHolidaysWorldwide"))

    assert run(["worldwide"]) == 0
    out, err = capsys.readouterr()
    assert out == "No holidays found.\n"
    assert err == ""


# -- emission flag reset across separate main() calls (P2 carry-forward) ----


def test_stdout_attempt_flag_resets_between_main_calls(monkeypatch, capsys):
    """After-attempt interrupt, then a genuine pre-emission JSON interrupt.

    The second call must emit the structured interrupted envelope. A stale
    attempt flag would suppress it, so a normal version/help success would not
    discriminate the reset.
    """
    real_write = cli._write

    def interrupted_write(text, encoding=None):
        sys.stdout.write("usage")
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "_write", interrupted_write)
    assert cli.main(["--help"]) == 130

    monkeypatch.setattr(cli, "_write", real_write)

    def interrupted_management(action, command, parser):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "_management", interrupted_management)
    assert cli.main(["--json", "--help"]) == 130

    out, err = capsys.readouterr()
    assert out.startswith("usage")
    assert '"code": "interrupted"' in out
    assert err == ""


# -- fresh-process cache semantics and spawn origin ------------------------

CACHE_HARNESS = r"""
import sys
from nager_date_mcp import cli, client as client_mod
from nager_date_mcp.transport import SeamResponse

marker = sys.argv[1]

def fetch(url, timeout=None):
    with open(marker, "a", encoding="utf-8") as handle:
        handle.write(url + "\n")
    return SeamResponse(status_code=200, text="[]", url=url)

client_mod.fetch_raw = fetch
raise SystemExit(cli.main(["--json", "worldwide"]))
"""


def test_fresh_process_starts_with_a_fresh_cache(tmp_path):
    marker = tmp_path / "fetches.txt"

    for _ in range(2):
        proc = subprocess.run(
            [V2, "-c", CACHE_HARNESS, str(marker)],
            cwd=tmp_path,
            env=_clean_env(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode == 0, proc.stderr
        assert json.loads(proc.stdout)["data"] == []
        assert proc.stderr == ""

    assert marker.read_text(encoding="utf-8").splitlines() == [
        BASE_URL + "/NextPublicHolidaysWorldwide",
        BASE_URL + "/NextPublicHolidaysWorldwide",
    ]


def test_spawned_cli_uses_configured_interpreter_and_candidate_module(tmp_path):
    code = (
        "import sys; import nager_date_mcp.cli as c; "
        "print(sys.executable); print(c.__file__)"
    )
    proc = subprocess.run(
        [V2, "-c", code],
        cwd=tmp_path,
        env=_clean_env(),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    interpreter, module_path = proc.stdout.splitlines()
    assert Path(interpreter) == Path(V2)
    assert sys.executable != V2
    assert module_path.startswith(str(REPO / "src") + os.sep)
