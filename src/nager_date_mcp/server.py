"""Stateless 2026-07-28 era MCP server for the Nager.Date public-holiday API.

Stdlib-only newline-delimited JSON-RPC 2.0 over stdio, copied verbatim from
REFERENCE §1–§3/§5–§7 (no framework, PLAN D5). stdout is protocol-only;
logs/notes go to stderr.
"""

import json
import sys
import urllib.error

from nager_date_mcp.client import NagerDateClient
from nager_date_mcp import operations
from nager_date_mcp.operations import (
    _day_of_week,
    _enrich_holidays,
)

if hasattr(sys.stdin, "reconfigure"):            # binary/undecodable bytes must not kill the loop
    sys.stdin.reconfigure(errors="replace")      # invalid UTF-8 → U+FFFD → lands in the json.loads except


def send(resp):
    sys.stdout.write(json.dumps(resp) + "\n")   # exactly one object per line
    sys.stdout.flush()                          # a buffered reply = a client timeout


ERA_VERSION = "2026-07-28"
SERVER_INFO = {"name": "nager-date", "version": "0.3.0"}
ERA_RESULT_FIELDS = {"resultType": "complete", "ttlMs": 0, "cacheScope": "private"}
RESULT_META = {"io.modelcontextprotocol/serverInfo": SERVER_INFO}


def era_result(payload):
    """A result carrying the era-strict fields D3 mandates on every response."""
    out = dict(payload)
    out.update(ERA_RESULT_FIELDS)
    out["_meta"] = RESULT_META
    return out


# Byte-frozen from golden/nager-date.tools.json (T00). outputSchema deliberately ABSENT — PLAN D3 / REFERENCE §4.
TOOLS: list[dict] = [{'name': 'get_holidays', 'description': 'Get all public holidays for a country in a given year.', 'inputSchema': {'additionalProperties': False, 'properties': {'country': {'type': 'string', 'description': 'ISO 3166-1 alpha-2 country code (e.g. "US", "GB", "DE").'}, 'year': {'anyOf': [{'type': 'integer'}, {'type': 'null'}], 'default': None, 'description': 'Year to query (1976–2076). Defaults to the current year.'}, 'type': {'anyOf': [{'type': 'string'}, {'type': 'null'}], 'default': None, 'description': 'Filter by holiday type (e.g. "Public", "Bank", "Observance"). Case-insensitive.'}, 'county': {'anyOf': [{'type': 'string'}, {'type': 'null'}], 'default': None, 'description': 'Filter by ISO 3166-2 subdivision code (e.g. "GB-ENG", "US-CA").'}}, 'required': ['country'], 'type': 'object'}}, {'name': 'get_next_holidays', 'description': 'Get the upcoming public holidays for a country (from today forward).', 'inputSchema': {'additionalProperties': False, 'properties': {'country': {'type': 'string', 'description': 'ISO 3166-1 alpha-2 country code (e.g. "US", "GB", "JP").'}, 'county': {'anyOf': [{'type': 'string'}, {'type': 'null'}], 'default': None, 'description': 'Optional ISO 3166-2 subdivision code to filter regional holidays.'}}, 'required': ['country'], 'type': 'object'}}, {'name': 'get_long_weekends', 'description': 'Get all long weekends for a country in a given year.\n\nA long weekend occurs when a holiday falls adjacent to a weekend,\ncreating an extended break. Includes bridge-day information.', 'inputSchema': {'additionalProperties': False, 'properties': {'country': {'type': 'string', 'description': 'ISO 3166-1 alpha-2 country code (e.g. "US", "GB").'}, 'year': {'anyOf': [{'type': 'integer'}, {'type': 'null'}], 'default': None, 'description': 'Year to query (1976–2076). Defaults to the current year.'}}, 'required': ['country'], 'type': 'object'}}, {'name': 'is_today_holiday', 'description': 'Check whether today is a public holiday in a given country.', 'inputSchema': {'additionalProperties': False, 'properties': {'country': {'type': 'string', 'description': 'ISO 3166-1 alpha-2 country code (e.g. "US", "PE").'}, 'county': {'anyOf': [{'type': 'string'}, {'type': 'null'}], 'default': None, 'description': 'Optional ISO 3166-2 subdivision code for regional check.'}, 'offset': {'anyOf': [{'type': 'integer'}, {'type': 'null'}], 'default': None, 'description': "UTC timezone offset for determining 'today', e.g. -5 for\nUS Eastern. Defaults to UTC."}}, 'required': ['country'], 'type': 'object'}}, {'name': 'list_countries', 'description': 'List countries supported by the Nager.Date holiday API.', 'inputSchema': {'additionalProperties': False, 'properties': {'query': {'anyOf': [{'type': 'string'}, {'type': 'null'}], 'default': None, 'description': 'Optional case-insensitive substring to filter by country name\nor code (e.g. "united", "US"). Omit to return all 200+ countries.'}}, 'type': 'object'}}, {'name': 'get_country_info', 'description': 'Get detailed information about a country including its region and borders.', 'inputSchema': {'additionalProperties': False, 'properties': {'country': {'type': 'string', 'description': 'ISO 3166-1 alpha-2 country code (e.g. "GB", "DE").'}}, 'required': ['country'], 'type': 'object'}}, {'name': 'get_worldwide_holidays', 'description': 'Get upcoming public holidays worldwide.\n\nReturns a list of holiday objects from all countries with upcoming\npublic holidays, each enriched with day_of_week.', 'inputSchema': {'additionalProperties': False, 'properties': {}, 'type': 'object'}}]


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
    outcome = operations.get_holidays(_client, country, year, type, county)
    failure = outcome.failure
    if failure is not None:
        return _fold_failure(failure)
    if outcome.warnings:
        return {
            "holidays": outcome.data,
            "note": _warning_note(outcome.warnings),
        }
    return outcome.data


def _fold_failure(failure):
    """Fold an operation failure into the historical MCP outward shapes.

    no_data owns the friendly sentence; ValueError keeps its ``Error:`` text;
    typed transport failures become the API-request-failed string; anything
    else is re-raised unchanged for the -32603 dispatch guard.
    """
    if failure.code == "no_data":
        return (
            "No holiday data available for "
            f"{failure.details.get('country', '')}."
        )
    cause = failure.cause
    if cause is None:
        return failure.message
    if isinstance(cause, ValueError):
        return f"Error: {cause}"
    if isinstance(cause, (urllib.error.URLError, OSError, TimeoutError)):
        # R2 parity: legacy httpx.HTTPError fold. URLError ⊂ OSError and
        # py3.11 TimeoutError ⊂ OSError, but TimeoutError is NOT a URLError;
        # the tuple guards the seam-miss case.
        return f"Error: API request failed — {cause}"
    # Non-foldable failures (e.g. the historical TypeError on malformed
    # payloads) surface unchanged at the -32603 dispatch guard.
    raise cause


def _warning_note(warnings) -> str:
    """Render operation warnings as the historical MCP note string."""
    parts: list[str] = []
    for warning in warnings:
        if warning.code == operations.TYPE_NO_MATCHES:
            parts.append(
                f"note: no holidays matched type '{warning.details['type']}' "
                f"for {warning.details['country']} "
                f"(this country may use a different type label)"
            )
        elif warning.code == operations.COUNTY_NATIONAL_ONLY:
            parts.append(
                f"note: no regional holidays found for county "
                f"'{warning.details['county']}'; showing national holidays only"
            )
    return "; ".join(parts)


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
    outcome = operations.get_next_holidays(_client, country, county)
    if outcome.failure is not None:
        return _fold_failure(outcome.failure)
    if outcome.warnings:
        return {"holidays": outcome.data, "note": _warning_note(outcome.warnings)}
    return outcome.data


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
    outcome = operations.get_long_weekends(_client, country, year)
    if outcome.failure is not None:
        return _fold_failure(outcome.failure)
    return outcome.data


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
    outcome = operations.is_today_holiday(_client, country, county, offset)
    if outcome.failure is not None:
        return _fold_failure(outcome.failure)
    return outcome.data


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
    outcome = operations.list_countries(_client, query)
    if outcome.failure is not None:
        return _fold_failure(outcome.failure)
    return outcome.data


def get_country_info(country: str) -> dict | str:
    """Get detailed information about a country including its region and borders.

    Args:
        country: ISO 3166-1 alpha-2 country code (e.g. "GB", "DE").

    Returns country metadata: commonName, officialName, region, and
    a list of bordering countries.
    """
    outcome = operations.get_country_info(_client, country)
    if outcome.failure is not None:
        return _fold_failure(outcome.failure)
    return outcome.data


def get_worldwide_holidays() -> list[dict] | str:
    """Get upcoming public holidays worldwide.

    Returns a list of holiday objects from all countries with upcoming
    public holidays, each enriched with day_of_week.
    """
    outcome = operations.get_worldwide_holidays(_client)
    if outcome.failure is not None:
        return _fold_failure(outcome.failure)
    return outcome.data


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
        if "id" not in req: continue            # JSON-RPC notification: never respond
        rid = req.get("id")                     # str, int, or explicit null
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
            send({"jsonrpc":"2.0","id":rid,"error":{"code":-32601,"message":f"Method not found: {method}"}})


if __name__ == "__main__":
    main()
