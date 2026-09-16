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


# PLACEHOLDER (T06/T07): TOOLS is the golden-frozen 7-tool listing (D4) and
# handle_call dispatches to the client over the era error-result shape
# (REFERENCE §5). T05 installs the loop only.
TOOLS: list[dict] = []


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
