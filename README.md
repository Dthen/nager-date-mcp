# Nager.Date MCP Server

MCP server wrapping the [Nager.Date](https://github.com/nager/Nager.Date) API — public holidays for 202 countries with local names, types, long weekends, and regional subdivisions.

## Tools

- **get_holidays(country, year?, type?, county?)** — All holidays for a country/year. Filter by type (Public, Bank, School, Observance, etc.) or subdivision (e.g. GB-SCT, DE-BY). Includes day-of-week.
- **get_next_holidays(country, county?)** — Upcoming holidays from today forward.
- **get_long_weekends(country, year?)** — Long weekends with bridge day suggestions.
- **is_today_holiday(country, county?, offset?)** — Quick yes/no: is today a public holiday? Optional UTC offset for timezone.
- **list_countries(query?)** — All 202 supported countries; optional case-insensitive substring filter on name/code.
- **get_country_info(country)** — Country metadata: official name, region, borders.
- **get_worldwide_holidays()** — Every holiday happening today, globally.

## Return shape convention

`get_holidays` and `get_next_holidays` normally return a **plain list** of holiday objects. When a filter produces a result that could mislead, they instead return a **dict** `{"holidays": [...], "note": "..."}` where `note` explains the caveat:

- A `type` filter matched no holiday (the country may use a different type label).
- A `county` filter matched no *regional* holiday, so only national/global holidays are shown (often a typo'd subdivision code).

LLM consumers should handle both shapes: if the result is a dict, surface the `note` and read the holidays from `result["holidays"]`; if it's a list, use it directly. Error and no-data cases return a plain string message.

## Features

- No API key required — uses the free public API
- In-memory TTL cache (24h for static data, 1h for "next" endpoints, 7d for country info)
- Country code normalization (lowercase accepted)
- Day-of-week enrichment on all holiday results
- Client-side filtering by holiday type and subdivision
- Friendly error messages (no crashes on invalid input, including valid-format country codes the API has no data for)

## Install

Zero runtime dependencies — the server is stdlib-only Python (urllib +
newline-delimited JSON-RPC over stdio, no framework). Only a Python ≥ 3.10
interpreter is required; nothing is fetched from PyPI at runtime.

The production interpreter lives in a dedicated venv:

```bash
/mnt/HC_Volume_105667182/kimbo/mcp-venvs/nager-date-mcp-v2/bin/python3 -m nager_date_mcp.server
```

## Configure (Hermes)

Add to your Hermes `config.yaml` under `mcp_servers` (the v2 command is the
production invocation; `-m` is kept):

```yaml
mcp_servers:
  nager-date:
    command: /mnt/HC_Volume_105667182/kimbo/mcp-venvs/nager-date-mcp-v2/bin/python3
    args: ["-m", "nager_date_mcp.server"]
    protocol: stateless
```

Or for Claude Desktop / other MCP clients:

```json
{
  "mcpServers": {
    "nager-date": {
      "command": "/mnt/HC_Volume_105667182/kimbo/mcp-venvs/nager-date-mcp-v2/bin/python3",
      "args": ["-m", "nager_date_mcp.server"]
    }
  }
}
```

## Development

Install pytest into any Python ≥ 3.10 environment (tests never live in the
runtime venv) and run the suite from the repo root:

```bash
python3 -m pip install pytest
python3 -m pytest tests/ -v
```

## API Details

- **Source:** https://date.nager.at/api/v3/
- **Auth:** None
- **Rate limits:** None observed (Cloudflare CDN, 7-day cache headers)
- **Year range:** 1976–2076
- **Countries:** 202 (ISO 3166-1 alpha-2)
- **Subdivisions:** ISO 3166-2 (e.g. GB-ENG, GB-SCT, DE-BY, CA-ON)
- **Holiday types:** Public, Bank, School, Authorities, Optional, Observance
