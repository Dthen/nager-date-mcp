"""Stateless 2026-07-28 era MCP server for the Nager.Date public-holiday API.

Stdlib-only newline-delimited JSON-RPC 2.0 over stdio, copied verbatim from
REFERENCE §1–§3/§5–§7 (no framework, PLAN D5). stdout is protocol-only;
logs/notes go to stderr.
"""

import json
import sys
import urllib.error
from datetime import date, datetime

from nager_date_mcp.client import NagerDateClient

if hasattr(sys.stdin, "reconfigure"):            # binary/undecodable bytes must not kill the loop
    sys.stdin.reconfigure(errors="replace")      # invalid UTF-8 → U+FFFD → lands in the json.loads except


def send(resp):
    sys.stdout.write(json.dumps(resp) + "\n")   # exactly one object per line
    sys.stdout.flush()                          # a buffered reply = a client timeout


ERA_VERSION = "2026-07-28"
SERVER_INFO = {"name": "nager-date", "version": "0.2.0"}
ERA_RESULT_FIELDS = {"resultType": "complete", "ttlMs": 0, "cacheScope": "private"}
RESULT_META = {"io.modelcontextprotocol/serverInfo": SERVER_INFO}


def era_result(payload):
    """A result carrying the era-strict fields D3 mandates on every response."""
    out = dict(payload)
    out.update(ERA_RESULT_FIELDS)
    out["_meta"] = RESULT_META
    return out


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
            try:
                h["day_of_week"] = _day_of_week(h["date"])
            except (ValueError, TypeError):
                # A single malformed/missing date must not sink the whole
                # response — mark it None and keep the good records.
                h["day_of_week"] = None
    return holidays


# Byte-frozen from golden/nager-date.tools.json (T00). outputSchema deliberately ABSENT — PLAN D3 / REFERENCE §4.
TOOLS: list[dict] = [{'name': 'get_holidays', 'description': 'Get all public holidays for a country in a given year.', 'inputSchema': {'additionalProperties': False, 'properties': {'country': {'type': 'string', 'description': 'ISO 3166-1 alpha-2 country code (e.g. "US", "GB", "DE").'}, 'year': {'anyOf': [{'type': 'integer'}, {'type': 'null'}], 'default': None, 'description': 'Year to query (1976–2076). Defaults to the current year.'}, 'type': {'anyOf': [{'type': 'string'}, {'type': 'null'}], 'default': None, 'description': 'Filter by holiday type (e.g. "Public", "Bank", "Observance"). Case-insensitive.'}, 'county': {'anyOf': [{'type': 'string'}, {'type': 'null'}], 'default': None, 'description': 'Filter by ISO 3166-2 subdivision code (e.g. "GB-ENG", "US-CA").'}}, 'required': ['country'], 'type': 'object'}}, {'name': 'get_next_holidays', 'description': 'Get the upcoming public holidays for a country (from today forward).', 'inputSchema': {'additionalProperties': False, 'properties': {'country': {'type': 'string', 'description': 'ISO 3166-1 alpha-2 country code (e.g. "US", "GB", "JP").'}, 'county': {'anyOf': [{'type': 'string'}, {'type': 'null'}], 'default': None, 'description': 'Optional ISO 3166-2 subdivision code to filter regional holidays.'}}, 'required': ['country'], 'type': 'object'}}, {'name': 'get_long_weekends', 'description': 'Get all long weekends for a country in a given year.\n\nA long weekend occurs when a holiday falls adjacent to a weekend,\ncreating an extended break. Includes bridge-day information.', 'inputSchema': {'additionalProperties': False, 'properties': {'country': {'type': 'string', 'description': 'ISO 3166-1 alpha-2 country code (e.g. "US", "GB").'}, 'year': {'anyOf': [{'type': 'integer'}, {'type': 'null'}], 'default': None, 'description': 'Year to query (1976–2076). Defaults to the current year.'}}, 'required': ['country'], 'type': 'object'}}, {'name': 'is_today_holiday', 'description': 'Check whether today is a public holiday in a given country.', 'inputSchema': {'additionalProperties': False, 'properties': {'country': {'type': 'string', 'description': 'ISO 3166-1 alpha-2 country code (e.g. "US", "PE").'}, 'county': {'anyOf': [{'type': 'string'}, {'type': 'null'}], 'default': None, 'description': 'Optional ISO 3166-2 subdivision code for regional check.'}, 'offset': {'anyOf': [{'type': 'integer'}, {'type': 'null'}], 'default': None, 'description': "UTC timezone offset for determining 'today', e.g. -5 for\nUS Eastern. Defaults to UTC."}}, 'required': ['country'], 'type': 'object'}}, {'name': 'list_countries', 'description': 'List countries supported by the Nager.Date holiday API.', 'inputSchema': {'additionalProperties': False, 'properties': {'query': {'anyOf': [{'type': 'string'}, {'type': 'null'}], 'default': None, 'description': 'Optional case-insensitive substring to filter by country name\nor code (e.g. "united", "US"). Omit to return all 200+ countries.'}}, 'type': 'object'}}, {'name': 'get_country_info', 'description': 'Get detailed information about a country including its region and borders.', 'inputSchema': {'additionalProperties': False, 'properties': {'country': {'type': 'string', 'description': 'ISO 3166-1 alpha-2 country code (e.g. "GB", "DE").'}}, 'required': ['country'], 'type': 'object'}}, {'name': 'get_worldwide_holidays', 'description': 'Get all public holidays happening today worldwide.\n\nReturns a list of holiday objects from all countries that have\na public holiday today, each enriched with day_of_week.', 'inputSchema': {'additionalProperties': False, 'properties': {}, 'type': 'object'}}]


# ---------------------------------------------------------------------------
# Tool layer: the 7 legacy handlers ported sync (PLAN D5 — the loop is a
# serialized stdio single-line reader; the legacy coroutines are dead code).
# Bodies are verbatim modulo de-async; _client is the sync T02 client class.
# ---------------------------------------------------------------------------

# Single shared client instance (module-level), as in the legacy server.
_client = NagerDateClient()


def get_holidays(
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
        holidays = _client.get_holidays(y, code)

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
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        # R2 parity: legacy httpx.HTTPError fold, tag-verified git show pre-migration/20260914 src/nager_date_mcp/server.py:124-126
        # Tuple per card T07 §1: URLError ⊂ OSError and py3.11 TimeoutError ⊂ OSError, but TimeoutError
        # is NOT a URLError (REFERENCE §5 client-port note) — the tuple guards the seam-miss case; the
        # transport.py seam (TRANSPORT_ERRORS) already normalizes to URLError (R2 fact 4).
        return f"Error: API request failed — {e}"


def get_next_holidays(
    country: str,
    county: str | None = None,
) -> list[dict] | dict | str:
    """Get the upcoming public holidays for a country (from today forward).

    Args:
        country: ISO 3166-1 alpha-2 country code (e.g. "US", "GB", "JP").
        county: Optional ISO 3166-2 subdivision code to filter regional holidays.

    Returns a list of upcoming holiday objects, each enriched with day_of_week.
    When a county filter matches no regional holiday (only globals survive),
    returns a dict {"holidays": [...], "note": "..."} explaining why — mirroring
    get_holidays' convention.
    """
    try:
        code = country.strip().upper()
        holidays = _client.get_next_holidays(code)

        # 204 No Content (mapped to None) for unsupported countries.
        if holidays is None:
            return f"No holiday data available for {code}."

        note: str | None = None

        if county is not None:
            county_upper = county.strip().upper()
            holidays = [
                h
                for h in holidays
                if h.get("global")
                or not h.get("counties")
                or county_upper in h["counties"]
            ]
            # Warn if the county matched no regional holiday (only globals
            # survived) — a typo'd county otherwise looks like a full answer.
            regional_match = any(
                county_upper in (h.get("counties") or []) for h in holidays
            )
            if not regional_match:
                note = (
                    f"note: no regional holidays found for county '{county_upper}'; "
                    f"showing national holidays only"
                )

        enriched = _enrich_holidays(holidays)
        if note is not None:
            return {"holidays": enriched, "note": note}
        return enriched
    except ValueError as e:
        return f"Error: {e}"
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        # R2 parity: legacy httpx.HTTPError fold, tag-verified git show pre-migration/20260914 src/nager_date_mcp/server.py:180-182
        # Tuple per card T07 §1: URLError ⊂ OSError and py3.11 TimeoutError ⊂ OSError, but TimeoutError
        # is NOT a URLError (REFERENCE §5 client-port note) — the tuple guards the seam-miss case; the
        # transport.py seam (TRANSPORT_ERRORS) already normalizes to URLError (R2 fact 4).
        return f"Error: API request failed — {e}"


# ---------------------------------------------------------------------------
# Planning tools
# ---------------------------------------------------------------------------


def get_long_weekends(
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
    dayCount, needBridgeDay, and bridgeDays fields. For a country the API
    has no data for, returns a friendly message string.
    """
    try:
        code = country.strip().upper()
        y = year if year is not None else date.today().year
        long_weekends = _client.get_long_weekends(y, code)

        # No-data handling, kept consistent with get_holidays /
        # get_next_holidays. Unlike those endpoints (which return 204 -> None
        # for unsupported countries), the LongWeekend endpoint returns 200 with
        # an EMPTY list for the same unsupported codes (IN, SA, TH, ...). So we
        # treat None as no-data AND, for an empty result, confirm the country is
        # actually supported before returning a bare []. A genuinely empty list
        # for a supported country (no long weekends that year) is returned as-is.
        if long_weekends is None:
            return f"No holiday data available for {code}."

        if not long_weekends:
            supported = {
                c["countryCode"]
                for c in _client.get_available_countries()
            }
            if code not in supported:
                return f"No holiday data available for {code}."

        return long_weekends
    except ValueError as e:
        return f"Error: {e}"
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        # R2 parity: legacy httpx.HTTPError fold, tag-verified git show pre-migration/20260914 src/nager_date_mcp/server.py:233-235
        # Tuple per card T07 §1: URLError ⊂ OSError and py3.11 TimeoutError ⊂ OSError, but TimeoutError
        # is NOT a URLError (REFERENCE §5 client-port note) — the tuple guards the seam-miss case; the
        # transport.py seam (TRANSPORT_ERRORS) already normalizes to URLError (R2 fact 4).
        return f"Error: API request failed — {e}"


def is_today_holiday(
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

    Returns {"is_holiday": true/false, "country": "<CODE>"}. For a country the
    API has no data for, returns a friendly message string (consistent with the
    other tools) rather than a misleading "is_holiday: false".
    """
    try:
        code = country.strip().upper()
        result = _client.is_today_holiday(code, county_code=county, offset=offset)

        # 200 -> today is a holiday.
        if result is True:
            return {"is_holiday": True, "country": code}

        # 204 is ambiguous: the API returns it both for an unsupported country
        # (no data) and for a supported country where today isn't a holiday.
        # Disambiguate against the cached supported-countries list — mirroring
        # get_long_weekends — so we never report a bare "not a holiday" for a
        # country the API simply has no data for.
        if result is None:
            supported = {
                c["countryCode"]
                for c in _client.get_available_countries()
            }
            if code not in supported:
                return f"No holiday data available for {code}."
            return {"is_holiday": False, "country": code}

        # Defensive fallback (client only returns True or None today).
        return {"is_holiday": False, "country": code}
    except ValueError as e:
        return f"Error: {e}"
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        # R2 parity: legacy httpx.HTTPError fold, tag-verified git show pre-migration/20260914 src/nager_date_mcp/server.py:281-283
        # Tuple per card T07 §1: URLError ⊂ OSError and py3.11 TimeoutError ⊂ OSError, but TimeoutError
        # is NOT a URLError (REFERENCE §5 client-port note) — the tuple guards the seam-miss case; the
        # transport.py seam (TRANSPORT_ERRORS) already normalizes to URLError (R2 fact 4).
        return f"Error: API request failed — {e}"


# ---------------------------------------------------------------------------
# Discovery tools
# ---------------------------------------------------------------------------


def list_countries(query: str | None = None) -> list[dict] | str:
    """List countries supported by the Nager.Date holiday API.

    Args:
        query: Optional case-insensitive substring to filter by country name
            or code (e.g. "united", "US"). Omit to return all 200+ countries.

    Returns a list of {code, name} objects. Use the code values with other
    tools like get_holidays.
    """
    try:
        countries = _client.get_available_countries()
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
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        # R2 parity: legacy httpx.HTTPError fold, tag-verified git show pre-migration/20260914 src/nager_date_mcp/server.py:315-317
        # Tuple per card T07 §1: URLError ⊂ OSError and py3.11 TimeoutError ⊂ OSError, but TimeoutError
        # is NOT a URLError (REFERENCE §5 client-port note) — the tuple guards the seam-miss case; the
        # transport.py seam (TRANSPORT_ERRORS) already normalizes to URLError (R2 fact 4).
        return f"Error: API request failed — {e}"


def get_country_info(country: str) -> dict | str:
    """Get detailed information about a country including its region and borders.

    Args:
        country: ISO 3166-1 alpha-2 country code (e.g. "GB", "DE").

    Returns country metadata: commonName, officialName, region, and
    a list of bordering countries.
    """
    try:
        code = country.strip().upper()
        return _client.get_country_info(code)
    except ValueError as e:
        return f"Error: {e}"
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        # R2 parity: legacy httpx.HTTPError fold, tag-verified git show pre-migration/20260914 src/nager_date_mcp/server.py:334-336
        # Tuple per card T07 §1: URLError ⊂ OSError and py3.11 TimeoutError ⊂ OSError, but TimeoutError
        # is NOT a URLError (REFERENCE §5 client-port note) — the tuple guards the seam-miss case; the
        # transport.py seam (TRANSPORT_ERRORS) already normalizes to URLError (R2 fact 4).
        return f"Error: API request failed — {e}"


def get_worldwide_holidays() -> list[dict] | str:
    """Get all public holidays happening today worldwide.

    Returns a list of holiday objects from all countries that have
    a public holiday today, each enriched with day_of_week.
    """
    try:
        holidays = _client.get_worldwide_holidays()
        return _enrich_holidays(holidays)
    except ValueError as e:
        return f"Error: {e}"
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        # R2 parity: legacy httpx.HTTPError fold, tag-verified git show pre-migration/20260914 src/nager_date_mcp/server.py:350-352
        # Tuple per card T07 §1: URLError ⊂ OSError and py3.11 TimeoutError ⊂ OSError, but TimeoutError
        # is NOT a URLError (REFERENCE §5 client-port note) — the tuple guards the seam-miss case; the
        # transport.py seam (TRANSPORT_ERRORS) already normalizes to URLError (R2 fact 4).
        return f"Error: API request failed — {e}"


# Single dispatch table (name -> handler); the 7 functions above mirror the
# legacy signatures exactly.
_TOOLS_BY_NAME = {
    "get_holidays": get_holidays,
    "get_next_holidays": get_next_holidays,
    "get_long_weekends": get_long_weekends,
    "is_today_holiday": is_today_holiday,
    "list_countries": list_countries,
    "get_country_info": get_country_info,
    "get_worldwide_holidays": get_worldwide_holidays,
}


def handle_call(name: str, arguments: dict):
    """Dispatch a tools/call to a sync tool function (REFERENCE §5).

    An unknown tool is an error RESULT (dict with an error key ⇒ isError:true),
    never a crash. Missing/extra keys fail the **arguments binding with
    TypeError, which surfaces at §5's -32603 dispatch guard — legacy only ever
    produced its friendly fold strings through the tool's own except arms, so
    no synthetic error-dict fold is added here (card T07 §2). The caller
    (loop) has already validated `arguments` is a dict.
    """
    handler = _TOOLS_BY_NAME.get(name)
    if handler is None:
        return {"error": f"Unknown tool: {name}"}
    return handler(**arguments)


def main():
    for line in sys.stdin:                      # EOF on stdin ends the loop (see §7)
        line = line.strip()
        if not line: continue
        try: req = json.loads(line)
        except Exception: continue              # garbage lines: skip, NEVER die (§7)
        if not isinstance(req, dict): continue  # valid JSON, not an object ("5", null, [1,2]): skip, NEVER die (§7)
        rid = req.get("id")                     # str or int; absent ⇒ notification
        method = req.get("method")              # null/42/etc must not crash .startswith below
        if not isinstance(method, str): method = ""   # route as unknown-method
        if method == "server/discover":
            send({"jsonrpc":"2.0","id":rid,"result":era_result({
                "supportedVersions":[ERA_VERSION],
                "capabilities":{"tools":{}}})})
        elif method == "tools/list":
            send({"jsonrpc":"2.0","id":rid,"result":era_result({"tools":TOOLS})})
        elif method == "tools/call":
            params = req.get("params")
            if not isinstance(params, dict) or not isinstance(params.get("name"), str):
                send({"jsonrpc":"2.0","id":rid,"error":{"code":-32602,
                    "message":"missing required param: params (with string 'name')"}})
                continue
            arguments = params.get("arguments", {})
            if not isinstance(arguments, dict):      # §5: validate in the call branch, never crash the handler
                send({"jsonrpc":"2.0","id":rid,"error":{"code":-32602,
                    "message":"invalid params: arguments must be an object"}})
                continue
            try:
                result = handle_call(params["name"], arguments)
                is_err = isinstance(result, dict) and ("error" in result or "transport_error" in result)
                if isinstance(result, str):
                    # DEVIATION from REFERENCE §5 (justified: all 7 tools could return str on the legacy server — verbatim strings verified live in T00 golden capture; re-dumping would change bytes; R3 string-passthrough)
                    # Owner R3 overrides REFERENCE §5's no-tag instruction for result-shape drift (passthrough is result-shape; the no-tag line covers the encoding decision rule only).
                    text = result
                else:
                    # D4 byte-freeze / REFERENCE §5 encoding rule (pattern decision, not tagged):
                    # compact non-ASCII-preserving JSON — derived live from the legacy fastmcp server
                    # (German holiday names raw, no \u escapes; _chain.md live-facts).
                    text = json.dumps(result, separators=(",", ":"), ensure_ascii=False)
                payload: dict = {"content": [{"type": "text", "text": text}]}
                if is_err:
                    payload["isError"] = True
                send({"jsonrpc":"2.0","id":rid,"result":era_result(payload)})
            except Exception as e:               # dispatch-level only (shouldn't happen)
                send({"jsonrpc":"2.0","id":rid,"error":{"code":-32603,"message":str(e)}})
        elif method.startswith("notifications/"): pass    # §6
        elif method == "ping":
            send({"jsonrpc":"2.0","id":rid,"result":{}})
        else:
            # includes legacy `initialize` (D2) and every other unknown method, per JSON-RPC
            if rid is None and "id" not in req: continue   # no-id = notification: never respond
            send({"jsonrpc":"2.0","id":rid,"error":{"code":-32601,"message":f"Method not found: {method}"}})


if __name__ == "__main__":
    main()
