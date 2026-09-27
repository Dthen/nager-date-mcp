"""Shared spawn-test constants for the era suite (T04+) — PLAN D11 / recipe 7 v2-venv pattern.

The production interpreter for spawn tests is V2, NEVER sys.executable:
pytest runs under $PYH (hermes venv) which does not have the package
installed (D8). Beware: $PYH's site-packages carries a stale
__editable__.nager_date_mcp-0.1.0.pth pointing at this repo's src/, so a
sys.executable fallback would silently import the repo tree instead of the
production venv — a trap, testing a different runtime than production.
Spawn tests must use the resolved V2 verbatim.

V2 is resolved per-machine by _prod_py() below (env var, else a gitignored
pointer file) so no absolute host path is committed to this repo.

Why the $V2 server spawn fails TODAY (honest, expected): src/nager_date_mcp/
server.py:7 still `import httpx` (one line before `from fastmcp import
FastMCP` at :8) and v2 is deliberately zero-deps (--no-deps until T10
rebuilds it after deps=[]); server importability arrives with the T05-T07
rewrite. `import nager_date_mcp.client` under $V2 already succeeds — the
post-T02 client is stdlib-only.
"""

import os
import pathlib

# Derived from __file__, NOT the absolute repo path: the T11 Step C
# fresh-clone gate runs inside a clone and must read the CLONE's golden
# fixture, not the original repo's.
REPO = pathlib.Path(__file__).resolve().parents[1]


def _prod_py() -> str:
    """Resolve the production (v2, zero-dependency) interpreter path.

    Deliberately has NO sys.executable fallback: the suite interpreter still
    carries fastmcp 3.4.7, so falling back to it would substitute the wrong
    interpreter and make the zero-deps/stateless gates vacuous. Raises at
    import rather than skipping — a skip would silently replace a real proof
    with a no-op.
    """
    env = os.environ.get("PROD_PY") or os.environ.get("PROD_PY_NAGER_DATE")
    if env:
        return env
    local = REPO / ".prod_py"  # gitignored, untracked, machine-local
    if local.is_file():
        return local.read_text().strip()
    raise RuntimeError(
        "Production interpreter not configured. Set $PROD_PY, or write the path to "
        f"{local} (gitignored). Tests must not fall back to sys.executable: the suite "
        "interpreter still carries fastmcp 3.4.7 while the server under test must be the "
        "zero-dependency v2 interpreter, so that fallback would substitute the wrong "
        "interpreter and make the zero-deps/stateless gates vacuous."
    )


# Target production interpreter (v2 venv created T03, rebuilt T10).
V2 = _prod_py()

SERVER_ARGS = ["-m", "nager_date_mcp.server"]

# Inherits REPO's __file__ derivation (see _chain shared-constants block).
GOLDEN = REPO / "golden/nager-date.tools.json"
