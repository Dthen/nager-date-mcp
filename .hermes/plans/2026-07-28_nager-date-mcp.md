# Nager.Date MCP Server — Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Build an MCP server wrapping the Nager.Date public holidays API — 7 tools, no auth, Python/FastMCP.

**Architecture:** Single Python file using the `mcp` package (FastMCP). In-memory TTL cache. No database. Calls the public API at `https://date.nager.at/api/v3/`.

**Tech Stack:** Python 3.11, `mcp` SDK (FastMCP), `httpx` for HTTP calls.

---

### Task 1: Project scaffolding

**Objective:** Set up project structure, dependencies, and a runnable skeleton.

**Files:**
- Create: `src/nager_date_mcp/server.py` — main MCP server
- Create: `pyproject.toml` — project metadata + deps
- Create: `tests/test_server.py` — test file

**Steps:**
1. Create `pyproject.toml` with deps: `mcp>=1.0`, `httpx`
2. Create `src/nager_date_mcp/server.py` with FastMCP skeleton (server name, one placeholder tool)
3. Create `tests/test_server.py` with a basic import test
4. Install deps: `pip install -e .`
5. Verify: `python -c "from nager_date_mcp.server import mcp; print(mcp.name)"`
6. Commit

### Task 2: API client with caching

**Objective:** Build the HTTP client layer with TTL cache.

**Files:**
- Create: `src/nager_date_mcp/client.py` — API client
- Test: `tests/test_client.py`

**Steps:**
1. Write tests for cache TTL behaviour (mock httpx)
2. Implement `NagerDateClient` class with:
   - `httpx.AsyncClient` base
   - In-memory dict cache with per-key TTL
   - `get(path, params, cache_ttl)` method
   - Error mapping: 404 → ValueError("Country not found"), 400 → ValueError("Year out of range")
3. Run tests
4. Commit

### Task 3: Core tools — get_holidays, get_next_holidays

**Objective:** Implement the two highest-priority tools.

**Files:**
- Modify: `src/nager_date_mcp/server.py`
- Test: `tests/test_tools.py`

**Steps:**
1. `get_holidays(country, year?, type?, county?)` — calls PublicHolidays, defaults year to current, filters by type/county client-side, adds day-of-week
2. `get_next_holidays(country, county?)` — calls NextPublicHolidays, optional county filter
3. Country code normalization (lowercase → uppercase)
4. Test with real API calls
5. Commit

### Task 4: Planning tools — get_long_weekends, is_today_holiday

**Objective:** Implement the planning-oriented tools.

**Files:**
- Modify: `src/nager_date_mcp/server.py`

**Steps:**
1. `get_long_weekends(country, year?)` — calls LongWeekend endpoint
2. `is_today_holiday(country, county?)` — calls IsTodayPublicHoliday, returns `{isHoliday: bool}` (handle 204 no-body)
3. Test with real API calls
4. Commit

### Task 5: Discovery tools — list_countries, get_country_info, get_worldwide_holidays

**Objective:** Implement the remaining three tools.

**Files:**
- Modify: `src/nager_date_mcp/server.py`

**Steps:**
1. `list_countries()` — calls AvailableCountries, cache 7 days
2. `get_country_info(country)` — calls CountryInfo
3. `get_worldwide_holidays()` — calls NextPublicHolidaysWorldwide
4. Test with real API calls
5. Commit

### Task 6: Integration test and README

**Objective:** End-to-end verification and documentation.

**Steps:**
1. Run full test suite
2. Test server via MCP inspector or direct tool calls
3. Update README.md with install/config instructions
4. Final commit
