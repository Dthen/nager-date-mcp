"""Shared spawn-test constants for the era suite (T04+) — PLAN D11 / recipe 7 v2-venv pattern.

The production interpreter for spawn tests is V2, NEVER sys.executable:
pytest runs under $PYH (hermes venv) which does not have the package
installed (D8). Beware: $PYH's site-packages carries a stale
__editable__.nager_date_mcp-0.1.0.pth pointing at this repo's src/, so a
sys.executable fallback would silently import the repo tree instead of the
production venv — a trap, testing a different runtime than production.
Spawn tests must use the V2 literal verbatim.

Why the $V2 server spawn fails TODAY (honest, expected): src/nager_date_mcp/
server.py:7 still `import httpx` (one line before `from fastmcp import
FastMCP` at :8) and v2 is deliberately zero-deps (--no-deps until T10
rebuilds it after deps=[]); server importability arrives with the T05-T07
rewrite. `import nager_date_mcp.client` under $V2 already succeeds — the
post-T02 client is stdlib-only.
"""

import pathlib

# Derived from __file__, NOT the absolute repo path: the T11 Step C
# fresh-clone gate runs inside a clone and must read the CLONE's golden
# fixture, not the original repo's.
REPO = pathlib.Path(__file__).resolve().parents[1]

# Target production interpreter (v2 venv created T03, rebuilt T10). Absolute
# verbatim — it is a machine-level path, identical in a clone.
V2 = "/mnt/HC_Volume_105667182/kimbo/mcp-venvs/nager-date-mcp-v2/bin/python3"

SERVER_ARGS = ["-m", "nager_date_mcp.server"]

# Inherits REPO's __file__ derivation (see _chain shared-constants block).
GOLDEN = REPO / "golden/nager-date.tools.json"
