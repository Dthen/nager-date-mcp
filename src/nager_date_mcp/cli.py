"""Native ``nager-date`` command-line interface.

Design boundary: this module owns argument parsing, terminal/JSON
presentation and process status. All holiday fetching/filtering/enrichment
lives in :mod:`nager_date_mcp.operations`; the client owns HTTP and the
transport seam owns sockets. Nothing here imports the MCP server.

Each command is described once by a :class:`_Command` entry in ``_COMMANDS``;
that registry drives command help, the country positional, the allowed value
options with their converters, parsing, dispatch and human rendering.

``main()`` returns the integer process status; the console-script wrapper and
the ``python -m nager_date_mcp.cli`` launcher both pass it to ``SystemExit``.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import urllib.error
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Callable

from nager_date_mcp import __version__, operations
from nager_date_mcp.client import NagerDateClient

PROG = "nager-date"
VERSION_TEXT = f"{PROG} {__version__}"

_COUNTRY_RE = re.compile(r"[A-Za-z]{2}")
_COUNTY_RE = re.compile(r"[A-Za-z]{2}-[A-Za-z0-9]+")
_INTEGER_RE = re.compile(r"[+-]?[0-9]+")
_HTML_DOCUMENT_RE = re.compile(r"<\s*(?:!doctype|html|head|body|title)\b", re.I)
_HTML_TAG_RE = re.compile(r"<\s*/?\s*[A-Za-z][^<>]*>")

# urllib wraps a connection failure once and the transport wraps that URLError
# again (it is an OSError), so the CLI normally sees two levels. The bound is
# defensive only: a hand-built exception graph must not spin the unwrap.
_MAX_URLError_WRAPPERS = 10
_SURROGATE_RE = re.compile("[\ud800-\udfff]")

# Set as soon as a stdout emission is attempted in the current main() call.
# Used only to suppress a replacement envelope after that boundary.
_stdout_write_attempted = False

class _UsageError(Exception):
    """A parse/validation failure that must map to exit status 2."""


class _ParserExit(Exception):
    """Raised instead of SystemExit so ``main`` can return the status."""

    def __init__(self, status: int) -> None:
        self.status = status


class _Parser(argparse.ArgumentParser):
    # Root parser only; maps subcommand name -> its parser for help output.
    command_parsers: dict[str, "_Parser"]

    def error(self, message: str):  # noqa: D401 - argparse hook
        raise _UsageError(message)

    def exit(self, status: int = 0, message: str | None = None):
        if message:
            self._print_message(message, sys.stderr)
        raise _ParserExit(status)


# -- value validation -------------------------------------------------------


def _country_value(text: str) -> str:
    value = text.strip()
    if not _COUNTRY_RE.fullmatch(value):
        raise argparse.ArgumentTypeError(
            f"invalid country code {text!r}: expected exactly two ASCII letters"
        )
    return value.upper()


def _county_value(text: str) -> str:
    value = text.strip()
    if not _COUNTY_RE.fullmatch(value):
        raise argparse.ArgumentTypeError(
            f"invalid county code {text!r}: expected two ASCII letters, a "
            "hyphen and an alphanumeric suffix (e.g. GB-ENG)"
        )
    return value.upper()


def _year_value(text: str) -> int:
    value = text.strip()
    if not _INTEGER_RE.fullmatch(value):
        raise argparse.ArgumentTypeError(f"invalid year value {text!r}")
    return int(value)


def _offset_value(text: str) -> int:
    value = text.strip()
    if not _INTEGER_RE.fullmatch(value):
        raise argparse.ArgumentTypeError(f"invalid offset value {text!r}")
    return int(value)


def _timeout_value(text: str) -> float:
    try:
        value = float(text)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(f"invalid timeout value {text!r}") from None
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError(
            "timeout must be a positive finite number of seconds"
        )
    return value


# -- parser -----------------------------------------------------------------


@dataclass(frozen=True)
class _Option:
    """One value-taking option, shared by argparse and the early scanner."""

    name: str
    metavar: str | None
    help: str
    convert: Callable[[str], Any] | None = None


@dataclass(frozen=True)
class _Command:
    """Everything the CLI needs to know about one command."""

    help: str
    description: str
    takes_country: bool
    options: tuple[_Option, ...]
    operation: Callable[..., operations.Outcome]
    arguments: tuple[str, ...]
    render: Callable[[operations.Outcome], str]
    validator: Callable[[Any], None] | None = None
    support_validator: Callable[[Any], None] | None = None


_YEAR = _Option("--year", None, "year to query (default: current year)", _year_value)
_TYPE = _Option("--type", "TEXT", "case-insensitive holiday type filter")
_COUNTY = _Option(
    "--county", "CODE", "subdivision code filter (e.g. GB-ENG)", _county_value
)
_OFFSET = _Option(
    "--offset",
    "HOURS",
    "UTC offset in whole hours (e.g. -5); "
    "omitted keeps the API's UTC default",
    _offset_value,
)
_QUERY = _Option(
    "--query", "TEXT", "case-insensitive name/code substring filter"
)
_TIMEOUT = _Option(
    "--timeout",
    "SECONDS",
    "per-request timeout in seconds (default: transport's 5 s)",
    _timeout_value,
)
_JSON = _Option("--json", None, "emit one JSON object with data, warnings and error")


def _add_value_option(parser: argparse.ArgumentParser, option: _Option) -> None:
    """Add one registry value option; the converter is optional."""
    kwargs: dict[str, Any] = {
        "default": argparse.SUPPRESS,
        "metavar": option.metavar,
        "help": option.help,
    }
    if option.convert is not None:
        kwargs["type"] = option.convert
    parser.add_argument(option.name, **kwargs)


def _add_common_options(parser: argparse.ArgumentParser) -> None:
    # SUPPRESS keeps an explicitly supplied common option from being reset by
    # the subparser's defaults when the option appears before the subcommand.
    parser.add_argument(
        _JSON.name,
        action="store_true",
        default=argparse.SUPPRESS,
        help=_JSON.help,
    )
    _add_value_option(parser, _TIMEOUT)
    parser.add_argument(
        "--version",
        action="version",
        version=VERSION_TEXT,
        help="print the program version and exit",
    )


def _add_command_options(
    parser: argparse.ArgumentParser, command: _Command
) -> None:
    if command.takes_country:
        parser.add_argument(
            "country",
            type=_country_value,
            metavar="COUNTRY",
            help="ISO alpha-2 code",
        )
    for option in command.options:
        _add_value_option(parser, option)


def _build_parser() -> _Parser:
    parser = _Parser(
        prog=PROG,
        description="Query public holidays from the Nager.Date API.",
        allow_abbrev=False,
    )
    _add_common_options(parser)
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")
    subparsers.required = True

    command_parsers: dict[str, _Parser] = {}
    # ``_COMMANDS`` is defined below the renderers; read it at call time.
    for command_name, command in _COMMANDS.items():
        sub = subparsers.add_parser(
            command_name,
            help=command.help,
            description=command.description,
            allow_abbrev=False,
        )
        _add_common_options(sub)
        _add_command_options(sub, command)
        command_parsers[command_name] = sub
    # Kept for management output selection (root vs subcommand help). Never
    # used for parsing decisions beyond that.
    parser.command_parsers = command_parsers
    return parser


# -- early mode selection ---------------------------------------------------


def _json_mode_from_argv(argv: list[str]) -> bool:
    """The exact lexical JSON-mode rule, independent of any caller seam."""
    for token in argv:
        if token == "--":
            return False
        if token == "--json":
            return True
    return False


def _json_requested(argv: list[str]) -> bool:
    """True when the exact token ``--json`` appears before a literal ``--``.

    Deliberately lexical and independent of parse success: it selects the
    output mode for early usage errors. No substring matching, no value-slot
    awareness.
    """
    return _json_mode_from_argv(argv)


def _is_negative_number(token: str) -> bool:
    """Mirror argparse's negative-value grammar (``-1``, ``-.5``, ``-0.5``).

    argparse treats these as values, not option strings; a lone ``-`` is
    handled separately. Anything else starting with ``-`` remains an option.
    """
    return bool(re.fullmatch(r"-\d+|-\d*\.\d+", token))


_GLOBAL_VALUE_OPTIONS = (_TIMEOUT,)


def _command_option(command: str | None, name: str) -> _Option | None:
    """The command-local option ``name`` when the command actually has it."""
    if command is None:
        return None
    return next(
        (option for option in _COMMANDS[command].options if option.name == name),
        None,
    )


def _scan_early(argv: list[str]):
    """Order parse failures against terminating management actions.

    Walks argv once (stopping at a literal ``--``) and returns the FIRST
    event: ``("help"|"version", command, None)`` or
    ``(None, command, message)`` for a usage problem. This makes
    "failure before --help/--version" take the usage path while a management
    action seen first terminates normally, as the CLI contract requires.

    Command-specific value options are accepted only once their command has
    been seen; common options are accepted before or after the command. Option
    converters come from the same registry that configures argparse.
    """
    command: str | None = None
    country_seen = False
    counts: dict[str, int] = {}
    index = 0
    total = len(argv)
    choices = ", ".join(repr(name) for name in _COMMANDS)

    while index < total:
        token = argv[index]
        if token == "--":
            return None, command, None
        if token in ("-h", "--help"):
            return "help", command, None
        if token == "--version":
            return "version", command, None

        if token.startswith("--"):
            name, has_value, inline = token.partition("=")
            if name == _JSON.name:
                if has_value:
                    return None, command, f"unrecognized arguments: {token}"
                index += 1
                continue

            option = next(
                (item for item in _GLOBAL_VALUE_OPTIONS if item.name == name),
                None,
            )
            if option is None:
                option = _command_option(command, name)
            if option is None:
                return None, command, f"unrecognized arguments: {token}"

            counts[name] = counts.get(name, 0) + 1
            if counts[name] > 1:
                return None, command, f"option {name} may only be given once"

            if has_value:
                value = inline
                next_index = index + 1
            else:
                following = index + 1
                if following >= total or argv[following] == "--":
                    return None, command, f"argument {name}: expected one argument"
                follower = argv[following]
                # Same value-slot rule as argparse: a lone '-' is a value and
                # negative-number spellings are values, everything else that
                # starts with '-' is an option (so a required value is missing).
                if follower.startswith("-") and not (
                    follower == "-" or _is_negative_number(follower)
                ):
                    return None, command, f"argument {name}: expected one argument"
                value = follower
                next_index = index + 2

            if option.convert is not None:
                try:
                    option.convert(value)
                except argparse.ArgumentTypeError as exc:
                    return None, command, f"argument {name}: {exc}"
            index = next_index
            continue

        if token.startswith("-") and token != "-":
            return None, command, f"unrecognized arguments: {token}"

        # Positional token.
        if command is None:
            if token in _COMMANDS:
                command = token
            else:
                return None, command, (
                    f"argument COMMAND: invalid choice: {token!r} "
                    f"(choose from {choices})"
                )
        elif _COMMANDS[command].takes_country and not country_seen:
            country_seen = True
            try:
                _country_value(token)
            except argparse.ArgumentTypeError as exc:
                return None, command, f"argument COUNTRY: {exc}"
        else:
            return None, command, f"unrecognized arguments: {token}"
        index += 1

    return None, command, None


def _management(action: str, command: str | None, parser: _Parser) -> int:
    if action == "version":
        return _emit(VERSION_TEXT + "\n", 0)
    target = parser.command_parsers.get(command or "", parser)
    return _emit(target.format_help(), 0)


# -- running the operation --------------------------------------------------


def _has_nonfinite(value: Any) -> bool:
    if isinstance(value, float):
        return not math.isfinite(value)
    if isinstance(value, dict):
        return any(_has_nonfinite(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_has_nonfinite(item) for item in value)
    return False


def _validate_holidays(payload: Any) -> None:
    """CLI-owned strict boundary check (never applied by the MCP adapter)."""
    if not isinstance(payload, list):
        raise operations.InvalidResponse(
            "invalid holiday payload: expected a list of holiday objects"
        )
    for index, record in enumerate(payload):
        if not isinstance(record, dict):
            raise operations.InvalidResponse(
                f"invalid holiday record at index {index}: expected an object"
            )
        if not isinstance(record.get("name"), str):
            raise operations.InvalidResponse(
                f"invalid holiday record at index {index}: "
                "'name' must be a string"
            )
        # ``date`` is deliberately tolerant (P2 correction 5): a present but
        # malformed date (string, null or another JSON type) becomes a null
        # weekday during enrichment; a missing date stays absent and no
        # weekday is invented for it.
        if "types" in record:
            types = record["types"]
            if not isinstance(types, list) or not all(
                isinstance(item, str) for item in types
            ):
                raise operations.InvalidResponse(
                    f"invalid holiday record at index {index}: "
                    "'types' must be a list of strings"
                )
        counties = record.get("counties")
        if counties is not None and (
            not isinstance(counties, list)
            or not all(isinstance(item, str) for item in counties)
        ):
            raise operations.InvalidResponse(
                f"invalid holiday record at index {index}: "
                "'counties' must be null or a list of strings"
            )
        if not isinstance(record.get("global", False), bool):
            raise operations.InvalidResponse(
                f"invalid holiday record at index {index}: "
                "'global' must be a boolean"
            )
        if _has_nonfinite(record):
            raise operations.InvalidResponse(
                f"invalid holiday record at index {index}: non-finite number"
            )


def _validate_records(payload: Any, label: str) -> None:
    if not isinstance(payload, list):
        raise operations.InvalidResponse(
            f"invalid {label} payload: expected a list of objects"
        )
    for index, record in enumerate(payload):
        if not isinstance(record, dict):
            raise operations.InvalidResponse(
                f"invalid {label} record at index {index}: expected an object"
            )
        if _has_nonfinite(record):
            raise operations.InvalidResponse(
                f"invalid {label} record at index {index}: non-finite number"
            )


def _validate_long_weekends(payload: Any) -> None:
    _validate_records(payload, "long-weekend")


def _validate_countries(payload: Any) -> None:
    """CLI-only boundary check used for results AND support lookups.

    A malformed catalogue must become invalid_response, never a guessed
    "unsupported country" answer. The MCP adapter never passes this.
    """
    _validate_records(payload, "countries")
    for index, record in enumerate(payload):
        if not isinstance(record.get("countryCode"), str):
            raise operations.InvalidResponse(
                f"invalid countries record at index {index}: "
                "'countryCode' must be a string"
            )
        if not isinstance(record.get("name"), str):
            raise operations.InvalidResponse(
                f"invalid countries record at index {index}: "
                "'name' must be a string"
            )


def _validate_country_info(payload: Any) -> None:
    if not isinstance(payload, dict):
        raise operations.InvalidResponse(
            "invalid country-info payload: expected an object"
        )
    if _has_nonfinite(payload):
        raise operations.InvalidResponse(
            "invalid country-info payload: non-finite number"
        )


def _run_command(namespace: argparse.Namespace) -> operations.Outcome:
    """Run exactly one operation over one client for this invocation."""
    command = _COMMANDS.get(namespace.command)
    if command is None:
        raise _UsageError(f"unknown command: {namespace.command!r}")
    client = NagerDateClient(timeout=getattr(namespace, "timeout", None))
    arguments = [getattr(namespace, name, None) for name in command.arguments]
    keywords: dict[str, Any] = {}
    if command.validator is not None:
        keywords["validate"] = command.validator
    if command.support_validator is not None:
        keywords["support_validate"] = command.support_validator
    return command.operation(client, *arguments, **keywords)


# -- failure classification -------------------------------------------------


def _is_timeout(exc: BaseException) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    reason = getattr(exc, "reason", None)
    return isinstance(reason, TimeoutError)


def _message_text(message: str) -> str:
    """Keep error text bounded and free of HTML documents/fragments."""
    if _HTML_DOCUMENT_RE.search(message) or _HTML_TAG_RE.search(message):
        return "the server returned an HTML error page"
    collapsed = " ".join(message.split())
    # An HTTPError with an empty reason formats as "HTTP Error 429: ".
    # Collapse that dangling separator at the CLI boundary instead of
    # emitting a message that ends in an orphan colon.
    if collapsed.endswith(":"):
        collapsed = collapsed[:-1].rstrip()
    if len(collapsed) > 300:
        collapsed = collapsed[:300].rstrip() + "..."
    return collapsed


def _unwrap_urlerror(cause: Any) -> Any:
    """Collapse urllib's nested URLError wrappers to the real cause.

    ``urllib.request.urlopen`` raises ``URLError(reason)`` for connection
    failures, and ``transport.fetch_raw`` catches that through
    ``TRANSPORT_ERRORS`` (which contains ``OSError`` and therefore URLError)
    and wraps it once more. The CLI therefore receives
    ``URLError(URLError(reason))``; formatting the outer string keeps
    urllib's ``<urlopen error ...>`` text, which the HTML probe above would
    misread. HTTPError is never unwrapped: its status mapping keeps priority.
    """
    depth = 0
    while (
        isinstance(cause, urllib.error.URLError)
        and not isinstance(cause, urllib.error.HTTPError)
        and depth < _MAX_URLError_WRAPPERS
    ):
        reason = cause.reason
        if reason is None or reason is cause:
            break
        cause = reason
        depth += 1
    return cause


def _classify(failure: operations.Failure) -> tuple[str, str]:
    if failure.code == "no_data":
        # CLI owns its own sentence; the shared layer carries only the code
        # and country detail (P2 correction 4).
        return "no_data", (
            f"No holiday data available for {failure.details.get('country', '')}."
        )
    if failure.code == "invalid_response":
        return "invalid_response", failure.message

    cause = failure.cause
    if cause is None:
        return "internal_error", "Internal error."

    network_failure = isinstance(cause, urllib.error.URLError)
    cause = _unwrap_urlerror(cause)

    # HTTPError is both URLError and OSError: check it first.
    if isinstance(cause, urllib.error.HTTPError):
        status = cause.code
        if status == 404:
            return "not_found", _message_text(str(cause))
        if status == 400:
            return "invalid_request", _message_text(str(cause))
        if status == 429:
            return "rate_limited", _message_text(str(cause))
        return "upstream_error", _message_text(str(cause))

    if _is_timeout(cause):
        return "timeout", "Request timed out."

    if network_failure or isinstance(cause, urllib.error.URLError):
        if isinstance(cause, urllib.error.URLError):
            # The unwrap stopped on a self/None/bound edge; keep the last
            # reason rather than the outer "<urlopen error ...>" wrapper.
            reason = cause.reason
            text = str(cause) if reason is None else str(reason)
        else:
            text = str(cause)
        return "network_error", _message_text(text)

    if isinstance(cause, ValueError):
        # JSON/UTF-8 decode failures are invalid responses; the client's
        # typed 400/404 metadata comes next; anything else is a bug.
        if isinstance(cause, (json.JSONDecodeError, UnicodeDecodeError)):
            return "invalid_response", "Invalid response from the holiday API."
        status = getattr(cause, "status_code", None)
        if status == 404:
            return "not_found", _message_text(str(cause))
        if status == 400:
            return "invalid_request", _message_text(str(cause))
        return "internal_error", "Internal error."

    if isinstance(cause, OSError):
        return "network_error", _message_text(str(cause))

    return "internal_error", "Internal error."


# -- rendering --------------------------------------------------------------


def _safe(text: str) -> str:
    """Escape terminal control characters from remote/user supplied text."""
    out: list[str] = []
    for char in text:
        if char == "\n":
            out.append("\\n")
        elif char == "\r":
            out.append("\\r")
        elif char == "\t":
            out.append("\\t")
        elif ord(char) < 0x20 or ord(char) == 0x7F:
            out.append(f"\\x{ord(char):02x}")
        else:
            out.append(char)
    return "".join(out)


def _warning_message(warning: operations.Warning) -> str:
    if warning.code == operations.TYPE_NO_MATCHES:
        return (
            f"no holidays matched type "
            f"'{_safe(str(warning.details.get('type', '')))}' for "
            f"{_safe(str(warning.details.get('country', '')))} "
            f"(this country may use a different type label)"
        )
    if warning.code == operations.COUNTY_NATIONAL_ONLY:
        return (
            "no regional holidays found for county "
            f"'{_safe(str(warning.details.get('county', '')))}'"
        )
    return _safe(warning.code)


def _coverage(record: dict) -> str:
    counties = record.get("counties")
    if counties:
        return ", ".join(_safe(str(county)) for county in counties)
    return "national"


def _render_holidays(records: list[dict]) -> str:
    if not records:
        return "No holidays found.\n"
    lines: list[str] = []
    for record in records:
        date_value = record.get("date", "")
        date_text = "" if date_value is None else _safe(str(date_value))
        weekday = _safe(str(record.get("day_of_week") or ""))
        name = _safe(str(record.get("name", "")))
        lines.append(
            f"{date_text}  {weekday:<9}  {name}  [{_coverage(record)}]"
        )
    return "\n".join(lines) + "\n"


def _render_long_weekends(records: list[dict]) -> str:
    if not records:
        return "No long weekends found.\n"
    lines: list[str] = []
    for record in records:
        start = _safe(str(record.get("startDate", "")))
        end = _safe(str(record.get("endDate", "")))
        day_count = _safe(str(record.get("dayCount", "")))
        bridge_days = record.get("bridgeDays") or []
        if record.get("needBridgeDay"):
            if bridge_days:
                bridge = "bridge: " + ", ".join(
                    _safe(str(day)) for day in bridge_days
                )
            else:
                bridge = "bridge day required"
        else:
            bridge = "no bridge day"
        lines.append(f"{start} -> {end}  {day_count} days  {bridge}")
    return "\n".join(lines) + "\n"


def _render_countries(records: list[dict]) -> str:
    if not records:
        return "No countries found.\n"
    return "\n".join(
        f"{_safe(str(record.get('code', '')))}  "
        f"{_safe(str(record.get('name', '')))}"
        for record in records
    ) + "\n"


def _country_info_value(value: Any) -> str:
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict):
                code = item.get("code") or item.get("countryCode")
                name = item.get("name")
                if code and name:
                    parts.append(f"{_safe(str(name))} ({_safe(str(code))})")
                elif code:
                    parts.append(_safe(str(code)))
                else:
                    parts.append(_safe(str(item)))
            else:
                parts.append(_safe(str(item)))
        return ", ".join(parts)
    if isinstance(value, dict):
        return ", ".join(
            f"{_safe(str(key))}={_safe(str(item))}"
            for key, item in value.items()
        )
    return _safe(str(value))


_INFO_LABELS = {
    "commonName": "Common name",
    "officialName": "Official name",
    "countryCode": "Country",
    "region": "Region",
    "borders": "Borders",
}


def _render_country_info(info: dict) -> str:
    if not info:
        # An empty object is valid, successful metadata: the API returned a
        # record with no fields. Say so instead of printing a blank line (and
        # never call the country unsupported).
        return "No country metadata returned.\n"
    lines = [
        f"{_INFO_LABELS.get(key) or _safe(str(key))}: "
        f"{_country_info_value(value)}"
        for key, value in info.items()
    ]
    return "\n".join(lines) + "\n"


def _render_today(data: dict) -> str:
    country = _safe(str(data.get("country", "")))
    if data.get("is_holiday") is True:
        return f"{country}: yes, today is a public holiday.\n"
    return f"{country}: no, today is not a public holiday.\n"


def _render_human(outcome: operations.Outcome) -> str:
    """Render an enriched holiday list (holidays, next-holidays, worldwide)."""
    return _render_holidays(outcome.data)


def _render_command_human(command: str, outcome: operations.Outcome) -> str:
    spec = _COMMANDS.get(command)
    if spec is None:
        raise _UsageError(f"unknown command: {command!r}")
    return spec.render(outcome)


def _escape_surrogates(text: str) -> str:
    """Escape lone surrogate code points as JSON ``\\uXXXX`` text.

    ``json.dumps(..., ensure_ascii=False)`` leaves unpaired surrogates in the
    string; UTF-8 cannot encode them. Ordinary Unicode is untouched.
    """
    return _SURROGATE_RE.sub(
        lambda match: f"\\u{ord(match.group()):04x}", text
    )


def _json_text(data, warnings, error) -> str:
    payload = {
        "data": data,
        "warnings": [
            {"code": code, "message": message} for code, message in warnings
        ],
        "error": error,
    }
    text = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    return _escape_surrogates(text) + "\n"


def _stream_encoding(stream) -> str:
    return getattr(stream, "encoding", None) or "utf-8"


def _write(text: str, encoding: str | None = None) -> bool:
    """Write one fully pre-encoded payload; never retry a second envelope.

    JSON callers pass ``encoding="utf-8"`` so console bytes are valid JSON
    regardless of the process output encoding. Human/management output uses
    the stream's own encoding and backslash-escapes anything it cannot
    represent. A text-only replaced stream without ``.buffer`` is supported.
    """
    if sys.stdout is None:
        # Interpreter startup with fd 1 already closed leaves sys.stdout as
        # None: there is no stream to write to. This is a failed output
        # attempt, reported quietly (no retry, no diagnostic).
        return False
    target = encoding or _stream_encoding(sys.stdout)
    try:
        payload = text.encode(target, errors="backslashreplace")
    except (LookupError, UnicodeError):
        target = "utf-8"
        payload = text.encode(target, errors="backslashreplace")

    try:
        buffer = getattr(sys.stdout, "buffer", None)
        if buffer is not None:
            buffer.write(payload)
            buffer.flush()
        else:
            sys.stdout.write(payload.decode(target, errors="backslashreplace"))
            sys.stdout.flush()
        return True
    except (BrokenPipeError, OSError, ValueError):
        # Quiet exit: no traceback, no retry, no success claim.
        _silence_stdout()
        return False


def _silence_stdout() -> None:
    """Point the stdout sink at os.devnull without leaking its descriptor."""
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
    except OSError:
        return
    try:
        target = sys.stdout.fileno()
    except (OSError, AttributeError, ValueError):
        # No usable stdout descriptor to replace; the opened one is still ours.
        try:
            os.close(devnull)
        except OSError:
            pass
        return
    if devnull == target:
        # open() reused stdout's own (closed) descriptor number: dup2 would be
        # a no-op and closing it would close the replacement sink itself, so
        # the interpreter's final flush would fail and override the status.
        return
    try:
        os.dup2(devnull, target)
    except (OSError, AttributeError, ValueError):
        pass
    finally:
        # dup2 already duplicated the descriptor onto stdout; the original
        # devnull handle is this call's own and must not leak.
        try:
            os.close(devnull)
        except OSError:
            pass


def _emit(text: str, status: int, encoding: str | None = None) -> int:
    global _stdout_write_attempted
    _stdout_write_attempted = True
    try:
        written = _write(text, encoding)
    except (BrokenPipeError, OSError):
        return 1
    return status if written else 1


def _emit_json(data, warnings, error, status: int) -> int:
    try:
        text = _json_text(data, warnings, error)
    except (TypeError, ValueError):
        text = _json_text(
            None,
            (),
            {"code": "invalid_response",
             "message": "Invalid response from the holiday API."},
        )
        status = 1
    except KeyboardInterrupt:
        return _interrupted(True)
    try:
        return _emit(text, status, "utf-8")
    except KeyboardInterrupt:
        # An interrupt during the write may have emitted partial stdout; do
        # not append a replacement envelope.
        return 130


def _write_stderr(text: str) -> None:
    if sys.stderr is None:
        # Interpreter startup with fd 2 already closed leaves sys.stderr as
        # None: diagnostics are unavailable, so stay quiet and return.
        return
    target = _stream_encoding(sys.stderr)
    try:
        payload = text.encode(target, errors="backslashreplace")
    except (LookupError, UnicodeError):
        target = "utf-8"
        payload = text.encode(target, errors="backslashreplace")
    try:
        buffer = getattr(sys.stderr, "buffer", None)
        if buffer is not None:
            buffer.write(payload)
            buffer.flush()
        else:
            sys.stderr.write(payload.decode(target, errors="backslashreplace"))
            sys.stderr.flush()
    except (BrokenPipeError, OSError, ValueError):
        pass


def _interrupted_after_emission(json_mode: bool) -> int:
    """Interrupt after a stdout write was attempted: quiet 130, no retry."""
    if _stdout_write_attempted:
        return 130
    return _interrupted(json_mode)


def _usage_failure(
    message: str,
    json_mode: bool,
    parser: _Parser,
    command: str | None = None,
) -> int:
    if json_mode:
        # JSON keeps the single stable envelope; only the human usage line
        # follows the selected command.
        return _emit_json(
            None, (), {"code": "usage_error", "message": message}, 2
        )
    target = parser.command_parsers.get(command or "", parser)
    _write_stderr(target.format_usage())
    _write_stderr(f"{PROG}: error: {message}\n")
    return 2


def _interrupted(json_mode: bool) -> int:
    """Structured/quiet 130 exit for Ctrl-C before a completed result.

    Guarded against a repeated interrupt: the handler itself must never
    escape as a traceback or fall through to a status-0 claim.
    """
    try:
        if json_mode:
            text = _json_text(
                None,
                (),
                {"code": "interrupted", "message": "Interrupted."},
            )
            _emit(text, 130, "utf-8")
        else:
            _write_stderr("interrupted\n")
    except KeyboardInterrupt:
        return 130
    return 130


# -- command registry -------------------------------------------------------
#
# Single source of truth for the seven commands. ``_build_parser``,
# ``_scan_early``, ``_run_command`` and ``_render_command_human`` all read
# these entries, so adding a command (or an option) is one registry edit, not
# five parallel lists. Order here is the documented/help order.

_COMMANDS: dict[str, _Command] = {
    "holidays": _Command(
        help="list public holidays for a country and year",
        description="List public holidays for a country and year.",
        takes_country=True,
        options=(_YEAR, _TYPE, _COUNTY),
        operation=operations.get_holidays,
        arguments=("country", "year", "type", "county"),
        render=_render_human,
        validator=_validate_holidays,
    ),
    "next-holidays": _Command(
        help="list upcoming public holidays for a country",
        description="List upcoming public holidays for a country.",
        takes_country=True,
        options=(_COUNTY,),
        operation=operations.get_next_holidays,
        arguments=("country", "county"),
        render=_render_human,
        validator=_validate_holidays,
    ),
    "long-weekends": _Command(
        help="list long weekends for a country and year",
        description="List long weekends for a country and year.",
        takes_country=True,
        options=(_YEAR,),
        operation=operations.get_long_weekends,
        arguments=("country", "year"),
        render=lambda outcome: _render_long_weekends(outcome.data),
        validator=_validate_long_weekends,
        support_validator=_validate_countries,
    ),
    "is-today-holiday": _Command(
        help="check whether today is a public holiday in a country",
        description="Check whether today is a public holiday in a country.",
        takes_country=True,
        options=(_COUNTY, _OFFSET),
        operation=operations.is_today_holiday,
        arguments=("country", "county", "offset"),
        render=lambda outcome: _render_today(outcome.data),
        support_validator=_validate_countries,
    ),
    "countries": _Command(
        help="list countries supported by the holiday API",
        description="List countries supported by the holiday API.",
        takes_country=False,
        options=(_QUERY,),
        operation=operations.list_countries,
        arguments=("query",),
        render=lambda outcome: _render_countries(outcome.data),
        validator=_validate_countries,
    ),
    "country-info": _Command(
        help="show metadata for a country",
        description="Show metadata for a country, including region and borders.",
        takes_country=True,
        options=(),
        operation=operations.get_country_info,
        arguments=("country",),
        render=lambda outcome: _render_country_info(outcome.data),
        validator=_validate_country_info,
    ),
    "worldwide": _Command(
        help="list upcoming public holidays worldwide",
        description="List upcoming public holidays worldwide (not only today's).",
        takes_country=False,
        options=(),
        operation=operations.get_worldwide_holidays,
        arguments=(),
        render=_render_human,
        validator=_validate_holidays,
    ),
}


# -- entry point ------------------------------------------------------------


def main(argv: Iterable[str] | None = None) -> int:
    global _stdout_write_attempted
    _stdout_write_attempted = False

    arguments: list[str] = []
    try:
        source = sys.argv[1:] if argv is None else argv
        # Copy one token at a time: if Ctrl-C arrives mid-copy the prefix is
        # the only reliable data. Never continue consuming the source (it may
        # be a one-shot iterator) and never re-read it.
        for token in source:
            arguments.append(token)
    except KeyboardInterrupt:
        try:
            json_mode = _json_mode_from_argv(arguments)
        except KeyboardInterrupt:
            json_mode = False
        return _interrupted(json_mode)

    try:
        json_mode = _json_requested(arguments)
        parser = _build_parser()
    except KeyboardInterrupt:
        try:
            json_mode = _json_mode_from_argv(arguments)
        except KeyboardInterrupt:
            json_mode = False
        return _interrupted(json_mode)

    try:
        action, command, problem = _scan_early(arguments)
    except KeyboardInterrupt:
        return _interrupted(json_mode)

    if action is not None:
        try:
            return _management(action, command, parser)
        except KeyboardInterrupt:
            return _interrupted_after_emission(json_mode)
    if problem is not None:
        try:
            return _usage_failure(problem, json_mode, parser, command)
        except KeyboardInterrupt:
            return _interrupted(json_mode)

    try:
        namespace = parser.parse_args(arguments)
    except _ParserExit as exc:
        return exc.status
    except _UsageError as exc:
        try:
            return _usage_failure(str(exc), json_mode, parser, command)
        except KeyboardInterrupt:
            return _interrupted(json_mode)
    except KeyboardInterrupt:
        return _interrupted(json_mode)

    try:
        outcome = _run_command(namespace)
    except KeyboardInterrupt:
        return _interrupted(json_mode)
    except Exception:
        if json_mode:
            return _emit_json(
                None, (), {"code": "internal_error",
                           "message": "Internal error."}, 1
            )
        try:
            _write_stderr("error: internal_error: Internal error.\n")
        except KeyboardInterrupt:
            return _interrupted(False)
        return 1

    if outcome.failure is not None:
        try:
            code, message = _classify(outcome.failure)
            if json_mode:
                return _emit_json(None, (), {"code": code, "message": message}, 1)
            _write_stderr(f"error: {code}: {_safe(message)}\n")
        except KeyboardInterrupt:
            return _interrupted(json_mode)
        return 1

    try:
        warnings = [
            (warning.code, _warning_message(warning))
            for warning in outcome.warnings
        ]
        if json_mode:
            return _emit_json(outcome.data, warnings, None, 0)
        text = _render_command_human(namespace.command, outcome)
    except KeyboardInterrupt:
        return _interrupted(json_mode)

    try:
        status = _emit(text, 0)
    except KeyboardInterrupt:
        # stdout emission may have begun; quiet 130, no replacement output.
        return 130
    if status != 0:
        return status
    try:
        for code, message in warnings:
            _write_stderr(f"warning: {code}: {message}\n")
    except KeyboardInterrupt:
        # Human result already emitted: never trace or claim status 0.
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
