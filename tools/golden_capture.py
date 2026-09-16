#!/usr/bin/env python3
"""Capture tools/list from the CURRENT legacy fastmcp server as the frozen golden (recipe 3).

One-shot real-repo act, run ONLY from the repo root (`cd $R && $PYO python3 tools/golden_capture.py`
... i.e. `python3 tools/golden_capture.py`): the golden output path is intentionally CWD-RELATIVE
(`golden/nager-date.tools.json`), so no `__file__` derivation is needed HERE. The clone-time READ
sites (T03 conftest GOLDEN / T04 #9) are the `__file__`-derived ones.

Spawn line is the production invocation per real ~/.hermes/config.yaml (read-only reference):
    /mnt/HC_Volume_105667182/kimbo/mcp-venvs/nager-date-mcp/bin/python3 -m nager_date_mcp.server

Probe sequence verified live against fastmcp 3.4.7 under $PYO (legacy mcp SDK server):
initialize -> answered; notifications/initialized -> swallowed (no reply); tools/list ->
7 tools, `outputSchema` present on ALL of them (the D3 trap the freeze test later strips and
asserts absent in the new listing). EOF/kill branch: the WAIT branch fired — the server exits
cleanly on stdin EOF (rc=0, ~0.4 s); the kill branch never runs.
"""
import json
import os
import select
import subprocess

PYO = "/mnt/HC_Volume_105667182/kimbo/mcp-venvs/nager-date-mcp/bin/python3"


def read_line_with_timeout(f, sec):
    """fleet mandate (transitous T02 form): never bare readline — pytest-timeout NOT installed;
    a silent legacy server must wedge this script forever otherwise. None == timeout."""
    if not select.select([f], [], [], sec)[0]:
        return None
    return f.readline()


def rpc(p, obj):
    p.stdin.write(json.dumps(obj) + "\n")
    p.stdin.flush()
    if "id" not in obj:
        return None  # notification: no reply expected
    line = read_line_with_timeout(p.stdout, 15)
    assert line, f"no response within 15 s (method: {obj.get('method')})"
    return json.loads(line)


def main():
    p = subprocess.Popen([PYO, "-m", "nager_date_mcp.server"],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, text=True)
    try:
        rpc(p, {"jsonrpc": "2.0", "id": 0, "method": "initialize",
                "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                           "clientInfo": {"name": "golden", "version": "0"}}})
        rpc(p, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        tools = rpc(p, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
        # VERIFIED live 2026-09-14; asserts here document the D3 trap for the freeze test.
        assert len(tools) == 7, f"expected 7 tools, got {len(tools)}"
        assert all("outputSchema" in t for t in tools), "legacy tools must carry outputSchema"
        os.makedirs("golden", exist_ok=True)
        with open("golden/nager-date.tools.json", "w") as f:
            f.write(json.dumps(tools, indent=2) + "\n")
        print("captured %d tools -> golden/nager-date.tools.json" % len(tools))
        print("order:", ", ".join(t["name"] for t in tools))
    finally:
        p.stdin.close()
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()


if __name__ == "__main__":
    main()
