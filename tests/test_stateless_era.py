"""Stateless 2026-07-28 era suite (T04) — executable spec of REFERENCE §1–§7.

RED by design: the server spawn uses the production interpreter $V2 with the
production invocation `-m nager_date_mcp.server` (conftest constants, recipe
7 — NEVER sys.executable). Today `src/nager_date_mcp/server.py` is still the
fastmcp implementation and $V2 is deliberately zero-deps (T03), so every
spawn dies at `import httpx` and all 17 tests fail legibly and fast (stdout
EOF is detected immediately, no 10 s waits). T05–T07 turn this suite green;
the legacy fastmcp server must NEVER pass it.

Fleet standard F9: EVERY read from a spawned server — including the two reads
inside test 17's REFERENCE §7 verbatim skeleton — goes through
`read_line_with_timeout`. pytest-timeout is NOT installed in $PYH, so the
wall-clock guard lives in this helper, not in plugin config: RED must be a
legible assert-failure, not a hang.

All tests are hermetic: no live network (the one tools/call probe short-circuits
in the client's `_normalize_country_code` before any HTTP; see test 11).
"""

import json
import select
import subprocess

from conftest import GOLDEN, SERVER_ARGS, V2

ERA = "2026-07-28"
TRIPLE = {"resultType": "complete", "ttlMs": 0, "cacheScope": "private"}

# One-shot spawn cases: communicate-style ceiling. A live server answers in
# well under a second; only a wedged/silent one ever spends the budget.
REQ_TIMEOUT = 10


def read_line_with_timeout(f, sec):
    """select()-based deadline on a pipe line (F9). None == timeout.

    A bare readline() wedges the suite when a silent server never answers,
    and pytest-timeout is not installed in $PYH — the deadline lives HERE.
    """
    if not select.select([f], [], [], sec)[0]:
        return None
    return f.readline()


def _require(f, sec, why):
    """Read one line with the F9 guard and turn silence/EOF into assert text."""
    line = read_line_with_timeout(f, sec)
    assert line is not None, f"no response within {sec}s ({why}) — server silent or wedged"
    assert line.strip(), f"stdout closed ({why}) — server died before answering"
    return json.loads(line)


class Server:
    """One spawned production server over stdin/stdout pipes (stderr=DEVNULL)."""

    def __init__(self):
        self.p = subprocess.Popen(
            [V2] + SERVER_ARGS,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        assert self.p.stdin is not None and self.p.stdout is not None  # PIPEs requested

    def send(self, text):
        self.p.stdin.write(text + "\n")
        self.p.stdin.flush()

    def rpc(self, obj, sec=REQ_TIMEOUT):
        """Send one id-bearing request, return the parsed response (F9-guarded)."""
        self.send(json.dumps(obj))
        return self.read(sec, f"method={obj.get('method')!r}")

    def read(self, sec=REQ_TIMEOUT, why="next response"):
        return _require(self.p.stdout, sec, why)

    def close(self):
        if self.p.poll() is None:
            self.p.kill()
        self.p.wait()


def test_discover_era_shape():
    s = Server()
    try:
        resp = s.rpc({"jsonrpc": "2.0", "id": 1, "method": "server/discover", "params": {}})
        assert "error" not in resp, resp
        result = resp["result"]
        assert result["supportedVersions"] == [ERA]
        assert "tools" in result["capabilities"]
        for key, value in TRIPLE.items():
            assert result[key] == value, f"{key} must be {value!r} (D3 triple)"
    finally:
        s.close()


def test_discover_paramless_request():
    """REFERENCE §2: the request may arrive with no params key at all."""
    s = Server()
    try:
        resp = s.rpc({"jsonrpc": "2.0", "id": 2, "method": "server/discover"})
        result = resp["result"]
        assert result["supportedVersions"] == [ERA]
        assert "tools" in result["capabilities"]
        for key, value in TRIPLE.items():
            assert result[key] == value
    finally:
        s.close()


def test_initialize_returns_32601_and_never_hangs_or_closes():
    """PLAN D2: no initialize branch — catch-all -32601, SAME pipes stay open."""
    s = Server()
    try:
        resp = s.rpc({
            "jsonrpc": "2.0", "id": 3, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "era-suite", "version": "0"}},
        })
        assert resp["id"] == 3
        assert resp["error"]["code"] == -32601, f"initialize must reject with -32601, got {resp}"
        # liveness: discover on the SAME pipes must still answer (no hang, no close).
        resp2 = s.rpc({"jsonrpc": "2.0", "id": 4, "method": "server/discover", "params": {}})
        assert resp2["id"] == 4
        assert resp2["result"]["supportedVersions"] == [ERA]
    finally:
        s.close()


def test_notifications_initialized_swallowed():
    """REFERENCE §6: notifications get NO response (§1 correlation rule)."""
    s = Server()
    try:
        s.send(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        resp = s.rpc({"jsonrpc": "2.0", "id": 42, "method": "ping"})
        # id 42 on the FIRST response line proves exactly one response was
        # written (a phantom reply to the notification would arrive first).
        assert resp["id"] == 42, f"notification must be silent, first response was {resp}"
    finally:
        s.close()


def test_ping_returns_empty_result():
    """REFERENCE §6: answer ping with the `{}` EmptyResult form."""
    s = Server()
    try:
        resp = s.rpc({"jsonrpc": "2.0", "id": 5, "method": "ping"})
        assert resp["id"] == 5
        assert "result" in resp, f"ping must be a result, not an error: {resp}"
        assert resp["result"] == {}, f"ping result must be exactly {{}}, got {resp['result']!r}"
    finally:
        s.close()


def test_unknown_method_minus_32601():
    s = Server()
    try:
        resp = s.rpc({"jsonrpc": "2.0", "id": 9, "method": "resources/list", "params": {}})
        assert resp["id"] == 9
        assert resp["error"]["code"] == -32601
        assert isinstance(resp["error"]["message"], str) and resp["error"]["message"]
    finally:
        s.close()


def test_tools_list_carries_triple():
    s = Server()
    try:
        resp = s.rpc({"jsonrpc": "2.0", "id": 6, "method": "tools/list", "params": {}})
        result = resp["result"]
        for key, value in TRIPLE.items():
            assert result[key] == value, f"tools/list must carry {key}={value!r}"
        assert isinstance(result["tools"], list) and result["tools"]
    finally:
        s.close()


def test_tools_list_has_no_outputSchema():
    """THE D3 TRAP: golden HAS outputSchema on all 7 — the era listing must NOT."""
    s = Server()
    try:
        resp = s.rpc({"jsonrpc": "2.0", "id": 7, "method": "tools/list", "params": {}})
        tools = resp["result"]["tools"]
        assert all("outputSchema" not in t for t in tools), \
            "no tool may declare outputSchema (§4: structuredContent would become mandatory)"
    finally:
        s.close()


def test_tools_list_golden_byte_identity():
    """Track A golden (recipe 3/D4) UNCONDITIONALLY — no skipif silent-skip.

    GOLDEN is conftest's __file__-derived path, so a clone run reads the
    CLONE's fixture. Per tool: name+description+inputSchema byte-identical
    after json.dumps(..., sort_keys=True); golden outputSchema and _meta
    stripped pre-diff; order-independent.
    """
    golden_tools = json.load(open(GOLDEN))
    s = Server()
    try:
        resp = s.rpc({"jsonrpc": "2.0", "id": 8, "method": "tools/list", "params": {}})
        tools = resp["result"]["tools"]
        assert len(tools) == 7, f"golden has 7 tools, listing has {len(tools)}"
        assert len(golden_tools) == 7

        def freeze(tool):
            stripped = {k: tool[k] for k in ("name", "description", "inputSchema")
                        if k in tool}
            return json.dumps(stripped, sort_keys=True)

        by_name = {t["name"]: freeze(t) for t in tools}
        for gt in golden_tools:
            golden_one = dict(gt)
            golden_one.pop("outputSchema", None)  # D3: stripped from golden pre-diff
            golden_one.pop("_meta", None)         # fastmcp framework residue
            assert golden_one["name"] in by_name, f"missing tool {golden_one['name']}"
            assert by_name[golden_one["name"]] == freeze(golden_one), \
                f"tool {golden_one['name']} drifted from the frozen golden"
        assert set(by_name) == {t["name"] for t in golden_tools}
    finally:
        s.close()


def test_tools_call_unknown_tool_is_error_result():
    """REFERENCE §5: tool-level errors are RESULTS with isError:true, not -3260x."""
    s = Server()
    try:
        resp = s.rpc({
            "jsonrpc": "2.0", "id": 10, "method": "tools/call",
            "params": {"name": "no_such_tool", "arguments": {}},
        })
        assert "error" not in resp, f"unknown tool must NOT be a JSON-RPC error: {resp}"
        result = resp["result"]
        assert result.get("isError") is True, "unknown tool must carry isError: true"
        text = result["content"][0]["text"]
        assert "no_such_tool" in text, f"error text must mention the tool name: {text!r}"
        for key, value in TRIPLE.items():
            assert result[key] == value
    finally:
        s.close()


def test_tools_call_network_free_short_circuit():
    """country:"" raises ValueError in _normalize_country_code BEFORE any HTTP."""
    # The exact literal request line pinned by the card (one line, \n-terminated
    # by send()). The legacy server folded identically (tag server.py l.124:
    # `except ValueError: return f"Error: {e}"`), so the expected text is the
    # ported logic's own — pinned verbatim from client.py:27's message string
    # (this suite must not import the package: $PYH is not the production
    # runtime, the spawn under $V2 is).
    error_text = "Error: Country code must be a non-empty string"
    s = Server()
    try:
        s.send('{"jsonrpc": "2.0", "id": 11, "method": "tools/call", "params": '
               '{"name": "get_holidays", "arguments": {"country": ""}}}')
        resp = s.read(why="tools/call get_holidays country=''")
        assert resp["id"] == 11
        assert "error" not in resp, "a tool-level error is a RESULT, not a JSON-RPC error (§5)"
        result = resp["result"]
        text = result["content"][0]["text"]
        assert text.startswith("Error:"), f"expected legacy fold text, got {text!r}"
        assert error_text in text
        for key, value in TRIPLE.items():
            assert result[key] == value
        # isError absent-or-False only: legacy success-text carried isError:false;
        # the era shape drops the key entirely (live probe + _chain fact).
        assert result.get("isError") is not True, \
            "error-TEXT results are SUCCESS results (legacy parity) — isError must not be true"
    finally:
        s.close()


def test_tools_call_missing_params_minus_32602():
    """REFERENCE §5: bad params is -32602 upfront — never a -32603 KeyError."""
    s = Server()
    try:
        resp = s.rpc({"jsonrpc": "2.0", "id": 12, "method": "tools/call"})
        assert resp["id"] == 12
        assert resp["error"]["code"] == -32602, f"missing params must be -32602, got {resp}"
        resp2 = s.rpc({"jsonrpc": "2.0", "id": 13, "method": "tools/call",
                       "params": {"name": 123}})
        assert resp2["id"] == 13
        assert resp2["error"]["code"] == -32602, f"non-string name must be -32602, got {resp2}"
    finally:
        s.close()


# --------------------------- regression (5) --------------------------------
# A.1 code-quality-round server-killers, pinned as regressions per REFERENCE §1/§7.


def test_garbage_lines_do_not_kill_the_server():
    s = Server()
    try:
        s.send("not json")
        resp = s.rpc({"jsonrpc": "2.0", "id": 20, "method": "server/discover", "params": {}})
        assert resp["id"] == 20, f"garbage line must not consume/renumber a response: {resp}"
        assert resp["result"]["supportedVersions"] == [ERA]
    finally:
        s.close()


def test_non_dict_json_lines_do_not_kill_the_server():
    """§1 isinstance(req, dict) guard: valid JSON non-objects are skipped."""
    s = Server()
    try:
        for junk in ("5", "null", '"str"', "[1,2]"):
            s.send(junk)
        resp = s.rpc({"jsonrpc": "2.0", "id": 21, "method": "server/discover", "params": {}})
        assert resp["id"] == 21, f"non-dict lines must be skipped silently: {resp}"
    finally:
        s.close()


def test_non_string_method_routes_unknown():
    """§1 method-coercion guard: null/42 must route as unknown, not crash startswith."""
    s = Server()
    try:
        for rid in (7, 8):
            method = None if rid == 7 else 42
            resp = s.rpc({"jsonrpc": "2.0", "id": rid, "method": method})
            assert resp["id"] == rid
            assert resp["error"]["code"] == -32601, \
                f"non-string method {method!r} must route -32601, got {resp}"
    finally:
        s.close()


def test_id_less_unknown_gets_no_response():
    """§1: no id => notification — never respond, even for unknown methods."""
    s = Server()
    try:
        s.send(json.dumps({"jsonrpc": "2.0", "method": "totally/unknown"}))
        resp = s.rpc({"jsonrpc": "2.0", "id": 99, "method": "ping"})
        assert resp["id"] == 99, \
            f"a phantom response to the id-less request corrupts correlation: {resp}"
    finally:
        s.close()


def test_binary_garbage_line_does_not_kill_the_server():
    """REFERENCE §7 skeleton, copied VERBATIM (source: REFERENCE §7 — fleet
    repos were reset; no commits in other repos are cited).

    F9 note: the two stdout reads are wrapped in read_line_with_timeout. That
    is the fleet-standard timeout guard, NOT a skeleton deviation — the
    skeleton's bytes and semantics (G→D→G→L, id correlation, liveness, rc=0
    EOF exit) stand unchanged.
    """
    PROD_PY = V2
    SERVER = SERVER_ARGS
    discover_line_json = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "server/discover", "params": {}})
    tools_list_line_json = json.dumps(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    p = subprocess.Popen([PROD_PY] + SERVER, stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        p.stdin.write(b"\xff\xfe\x00garbage\n")                     # G — pre-stream invalid UTF-8
        p.stdin.write(discover_line_json.encode() + b"\n"); p.stdin.flush()  # D
        line1 = read_line_with_timeout(p.stdout, REQ_TIMEOUT)
        assert line1, "no response to discover after pre-stream garbage (F9)"
        resp = json.loads(line1)
        assert resp["id"] == 1 and resp["result"]["supportedVersions"] == ["2026-07-28"]
        p.stdin.write(b"\x00\xff\n")                                # G — mid-stream garbage
        p.stdin.write(tools_list_line_json.encode() + b"\n"); p.stdin.flush()  # L
        line2 = read_line_with_timeout(p.stdout, REQ_TIMEOUT)
        assert line2, "no response to tools/list after mid-stream garbage (F9)"
        resp2 = json.loads(line2)                                   # id==2 proves no phantom response to G
        assert resp2["id"] == 2 and "result" in resp2
        assert p.poll() is None
        p.stdin.close(); assert p.wait(timeout=5) == 0              # clean EOF exit
    finally:
        if p.poll() is None:
            p.kill(); p.wait()
