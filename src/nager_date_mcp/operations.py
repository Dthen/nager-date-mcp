"""Interface-neutral holiday operations shared by the MCP and CLI adapters.

This module owns the minimum holidays logic extracted for the native CLI
pilot: country normalisation, the type/county filters, weekday enrichment and
the domain outcome/warning vocabulary. It imports neither CLI parsing/output
nor the MCP server/protocol layer — each adapter owns its own presentation.

The operation layer deliberately does NOT validate upstream payload shapes:
the MCP adapter's observable behaviour (including its historical TypeError
outcomes) is preserved by running the same expressions on unvalidated data.
An interface that needs strict shape checks injects a ``validate`` callable
(the CLI does); omitting it leaves MCP behaviour untouched.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable

# Warning kinds. Details (never pre-rendered prose) let each interface choose
# its own wording; the MCP adapter keeps its historical note strings verbatim.
TYPE_NO_MATCHES = "type_no_matches"
COUNTY_NATIONAL_ONLY = "county_national_only"


class InvalidResponse(Exception):
    """Raised by an interface-supplied validator when a payload is unusable."""


@dataclass(frozen=True)
class Warning:
    """A non-fatal caveat about a filtered result."""

    code: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Failure:
    """A failed operation with original-exception provenance.

    ``code`` is interface-neutral: ``no_data`` (upstream has no holiday data
    for the country), ``invalid_response`` (an injected validator rejected the
    payload) or ``request_failed`` (the fetch/filter raised; ``cause`` is the
    original exception object so each adapter can fold it its own way).

    ``details`` carries domain data an adapter needs to render its own
    message (e.g. ``{"country": "IN"}`` for ``no_data``); it is never a
    pre-rendered MCP sentence. ``message`` stays a short domain description
    for adapter fallbacks. The field is appended after ``cause`` so existing
    positional ``Failure(code, message, cause)`` construction is unchanged.
    """

    code: str
    message: str
    cause: BaseException | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Outcome:
    """Domain result: data, warnings and/or a failure with provenance."""

    data: Any = None
    warnings: tuple[Warning, ...] = ()
    failure: Failure | None = None


# -- helpers ----------------------------------------------------------------


def day_of_week(date_str: str) -> str:
    """Compute the weekday name (e.g. 'Monday') from an ISO date string."""
    d = datetime.strptime(date_str, "%Y-%m-%d").date()
    return d.strftime("%A")


def enrich_holidays(holidays: list[dict] | None) -> list[dict]:
    """Add a ``day_of_week`` field to each holiday dict (in place).

    Defensive: a None or empty input (e.g. the API returned 204 No Content
    for an unsupported country) yields an empty list rather than raising.
    A single malformed/missing date must not sink the whole response — mark
    its weekday None and keep the good records.
    """
    if not holidays:
        return []
    for h in holidays:
        if "date" in h:
            try:
                h["day_of_week"] = day_of_week(h["date"])
            except (ValueError, TypeError):
                h["day_of_week"] = None
    return holidays


# Backwards-compatible private aliases re-exported through ``server``.
_day_of_week = day_of_week
_enrich_holidays = enrich_holidays


def normalize_country(country: str) -> str:
    """Strip and upper-case a supplied country code (no format validation)."""
    return country.strip().upper()


def filter_holidays(
    holidays: list[dict],
    *,
    country_code: str,
    type_filter: str | None = None,
    county_filter: str | None = None,
) -> tuple[list[dict], tuple[Warning, ...]]:
    """Apply the type then county filters, returning (rows, warnings).

    Semantics are exactly the legacy MCP ones: a type miss warns only when the
    source list was non-empty; a county "national only" warning means no
    regional row matched — it is not proof the county is invalid. Warnings are
    ordered type first, then county.
    """
    warnings: list[Warning] = []

    if type_filter is not None:
        type_lower = type_filter.lower()
        filtered = [
            h
            for h in holidays
            if any(type_lower == t.lower() for t in h.get("types", []))
        ]
        if not filtered and holidays:
            warnings.append(
                Warning(
                    TYPE_NO_MATCHES,
                    {"type": type_filter, "country": country_code},
                )
            )
        holidays = filtered

    if county_filter is not None:
        county_upper = county_filter.strip().upper()
        filtered = [
            h
            for h in holidays
            if h.get("global")
            or not h.get("counties")
            or county_upper in h["counties"]
        ]
        regional_match = any(
            county_upper in (h.get("counties") or []) for h in filtered
        )
        if not regional_match:
            warnings.append(
                Warning(COUNTY_NATIONAL_ONLY, {"county": county_upper})
            )
        holidays = filtered

    return holidays, tuple(warnings)


def _today() -> date:
    """Private today seam: the host-local calendar date at CALL time."""
    return date.today()


def _resolve_today(today: date | Callable[[], date] | None) -> date:
    if today is None:
        return _today()  # resolved at CALL time, not import time
    if callable(today):
        return today()
    return today


# -- the holidays operation -------------------------------------------------


def get_holidays(
    client: Any,
    country: str,
    year: int | None = None,
    type: str | None = None,
    county: str | None = None,
    *,
    today: date | Callable[[], date] | None = None,
    validate: Callable[[Any], None] | None = None,
) -> Outcome:
    """Fetch/filter/enrich holidays for ``country`` and return an Outcome.

    ``client`` is supplied by the interface (the MCP adapter passes its shared
    ``server._client`` instance). ``validate`` is an optional interface-owned
    boundary check invoked on the raw payload before filtering; it raises
    :class:`InvalidResponse` to reject a shape (the CLI uses this, MCP does
    not).
    """
    try:
        code = normalize_country(country)
        y = year if year is not None else _resolve_today(today).year
        holidays = client.get_holidays(y, code)

        # 204 No Content (None) for valid-format countries the API has no
        # data for (e.g. IN, SA, AE, TH).
        if holidays is None:
            return Outcome(
                failure=Failure(
                    "no_data", "no holiday data", details={"country": code}
                )
            )

        if validate is not None:
            validate(holidays)

        filtered, warnings = filter_holidays(
            holidays,
            country_code=code,
            type_filter=type,
            county_filter=county,
        )
        return Outcome(data=enrich_holidays(filtered), warnings=warnings)
    except InvalidResponse as exc:
        return Outcome(
            failure=Failure("invalid_response", str(exc), exc)
        )
    except Exception as exc:  # provenance kept for adapter-specific folding
        return Outcome(
            failure=Failure("request_failed", str(exc), exc)
        )


# -- the remaining six operations -------------------------------------------
#
# Endpoint-specific distinctions stay explicit and mirror the legacy server
# bodies exactly. Each operation receives an interface-supplied client and an
# optional interface-owned validator; the MCP adapter supplies neither (its
# historical TypeError/KeyError dispatch behaviour is preserved), the CLI
# supplies strict shape checks at its own boundary.


def _support_codes(client: Any, support_validate: Callable[[Any], None] | None) -> set:
    """Fetch the catalogue and return supported country codes.

    ``support_validate`` is CLI-only: a malformed catalogue must become an
    invalid_response failure, never a guessed "unsupported" answer. With no
    validator the legacy expressions run unchanged (MCP).
    """
    countries = client.get_available_countries()
    if support_validate is not None:
        support_validate(countries)
    return {c["countryCode"] for c in countries}


def get_next_holidays(
    client: Any,
    country: str,
    county: str | None = None,
    *,
    validate: Callable[[Any], None] | None = None,
) -> Outcome:
    """Upcoming holidays: None is no_data, [] is a genuine success."""
    try:
        code = normalize_country(country)
        holidays = client.get_next_holidays(code)
        if holidays is None:
            return Outcome(
                failure=Failure(
                    "no_data", "no holiday data", details={"country": code}
                )
            )
        if validate is not None:
            validate(holidays)
        filtered, warnings = filter_holidays(
            holidays, country_code=code, county_filter=county
        )
        return Outcome(data=enrich_holidays(filtered), warnings=warnings)
    except InvalidResponse as exc:
        return Outcome(failure=Failure("invalid_response", str(exc), exc))
    except Exception as exc:
        return Outcome(failure=Failure("request_failed", str(exc), exc))


def get_long_weekends(
    client: Any,
    country: str,
    year: int | None = None,
    *,
    today: date | Callable[[], date] | None = None,
    validate: Callable[[Any], None] | None = None,
    support_validate: Callable[[Any], None] | None = None,
) -> Outcome:
    """Long weekends, disambiguating an empty result via country support.

    ``None`` is no_data. An empty list is ambiguous: supported -> success [],
    unsupported -> no_data, failed support lookup -> request_failed. A failed
    lookup is never collapsed into either empty answer.
    """
    try:
        code = normalize_country(country)
        y = year if year is not None else _resolve_today(today).year
        long_weekends = client.get_long_weekends(y, code)
        if long_weekends is None:
            return Outcome(
                failure=Failure(
                    "no_data", "no holiday data", details={"country": code}
                )
            )
        if validate is not None:
            validate(long_weekends)
        if not long_weekends:
            supported = _support_codes(client, support_validate)
            if code not in supported:
                return Outcome(
                    failure=Failure(
                        "no_data", "no holiday data", details={"country": code}
                    )
                )
        return Outcome(data=long_weekends)
    except InvalidResponse as exc:
        return Outcome(failure=Failure("invalid_response", str(exc), exc))
    except Exception as exc:
        return Outcome(failure=Failure("request_failed", str(exc), exc))


def is_today_holiday(
    client: Any,
    country: str,
    county: str | None = None,
    offset: int | None = None,
    *,
    support_validate: Callable[[Any], None] | None = None,
) -> Outcome:
    """Today check: 200 means true, ambiguous 204 is disambiguated by support.

    The county is forwarded to the client RAW (the MCP legacy asymmetry); the
    CLI normalizes its own input before calling. An explicit ``offset=0`` is
    distinct from omission.
    """
    try:
        code = normalize_country(country)
        result = client.is_today_holiday(code, county_code=county, offset=offset)
        if result is True:
            return Outcome(data={"is_holiday": True, "country": code})
        if result is None:
            supported = _support_codes(client, support_validate)
            if code not in supported:
                return Outcome(
                    failure=Failure(
                        "no_data", "no holiday data", details={"country": code}
                    )
                )
            return Outcome(data={"is_holiday": False, "country": code})
        # Defensive legacy return: anything not True/None is a false answer.
        return Outcome(data={"is_holiday": False, "country": code})
    except InvalidResponse as exc:
        return Outcome(failure=Failure("invalid_response", str(exc), exc))
    except Exception as exc:
        return Outcome(failure=Failure("request_failed", str(exc), exc))


def list_countries(
    client: Any,
    query: str | None = None,
    *,
    validate: Callable[[Any], None] | None = None,
) -> Outcome:
    """Available countries mapped to ``{code, name}`` with the legacy query."""
    try:
        countries = client.get_available_countries()
        if validate is not None:
            validate(countries)
        mapped = [
            {"code": c["countryCode"], "name": c["name"]} for c in countries
        ]
        if query is not None:
            q = query.strip().lower()
            if q:
                mapped = [
                    c
                    for c in mapped
                    if q in c["name"].lower() or q in c["code"].lower()
                ]
        return Outcome(data=mapped)
    except InvalidResponse as exc:
        return Outcome(failure=Failure("invalid_response", str(exc), exc))
    except Exception as exc:
        return Outcome(failure=Failure("request_failed", str(exc), exc))


def get_country_info(
    client: Any,
    country: str,
    *,
    validate: Callable[[Any], None] | None = None,
) -> Outcome:
    """Country metadata; MCP keeps its historical null success on 204."""
    try:
        code = normalize_country(country)
        info = client.get_country_info(code)
        if validate is not None:
            validate(info)
        return Outcome(data=info)
    except InvalidResponse as exc:
        return Outcome(failure=Failure("invalid_response", str(exc), exc))
    except Exception as exc:
        return Outcome(failure=Failure("request_failed", str(exc), exc))


def get_worldwide_holidays(
    client: Any,
    *,
    validate: Callable[[Any], None] | None = None,
) -> Outcome:
    """Upcoming worldwide holidays; a 204 (None) is a successful empty list."""
    try:
        holidays = client.get_worldwide_holidays()
        if holidays is None:
            return Outcome(data=[])
        if validate is not None:
            validate(holidays)
        return Outcome(data=enrich_holidays(holidays))
    except InvalidResponse as exc:
        return Outcome(failure=Failure("invalid_response", str(exc), exc))
    except Exception as exc:
        return Outcome(failure=Failure("request_failed", str(exc), exc))
