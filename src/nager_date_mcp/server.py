"""Stateless 2026-07-28 era MCP server for the Nager.Date public-holiday API.

Stdlib-only newline-delimited JSON-RPC 2.0 over stdio, copied verbatim from
REFERENCE §1–§3/§5–§7 (no framework, PLAN D5). stdout is protocol-only;
logs/notes go to stderr.
"""

import json
import sys
from datetime import datetime

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


def handle_call(name: str, arguments: dict):
    # PLACEHOLDER (T06/T07): real tool dispatch arrives with the tools layer.
    return {"error": f"Unknown tool: {name}"}


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
            try:
                result = handle_call(params["name"], params.get("arguments", {}))
                is_err = isinstance(result, dict) and ("error" in result or "transport_error" in result)
                payload: dict = {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}
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
