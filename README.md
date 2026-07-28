# Nager.Date MCP Server

MCP server wrapping the [Nager.Date](https://github.com/nager/Nager.Date) API — public holidays for 202 countries with local names, types, long weekends, and regional subdivisions.

## Tools

- **get_holidays(country, year?, type?, county?)** — All holidays for a country/year. Filter by type (Public, Bank, School, Observance, etc.) or subdivision (e.g. GB-SCT, DE-BY). Includes day-of-week.
- **get_next_holidays(country, county?)** — Upcoming holidays from today forward.
- **get_long_weekends(country, year?)** — Long weekends with bridge day suggestions.
- **is_today_holiday(country, county?)** — Quick yes/no: is today a public holiday?
- **list_countries()** — All 202 supported countries.
- **get_country_info(country)** — Country metadata: official name, region, borders.
- **get_worldwide_holidays()** — Every holiday happening today, globally.

## Features

- No API key required — uses the free public API
- In-memory TTL cache (24h for static data, 1h for "next" endpoints, 7d for country info)
- Country code normalization (lowercase accepted)
- Day-of-week enrichment on all holiday results
- Client-side filtering by holiday type and subdivision
- Friendly error messages (no crashes on invalid input)

## Install

```bash
cd nager-date-mcp
pip install -e .
```

## Configure (Hermes)

Add to your Hermes `config.yaml` under `mcp_servers`:

```yaml
mcp_servers:
  nager-date:
    command: python
    args: ["-m", "nager_date_mcp.server"]
```

Or for Claude Desktop / other MCP clients:

```json
{
  "mcpServers": {
    "nager-date": {
      "command": "python",
      "args": ["-m", "nager_date_mcp.server"]
    }
  }
}
```

## Development

```bash
pip install -e ".[dev]"
python -m pytest tests/ -v
```

## API Details

- **Source:** https://date.nager.at/api/v3/
- **Auth:** None
- **Rate limits:** None observed (Cloudflare CDN, 7-day cache headers)
- **Year range:** 1976–2076
- **Countries:** 202 (ISO 3166-1 alpha-2)
- **Subdivisions:** ISO 3166-2 (e.g. GB-ENG, GB-SCT, DE-BY, CA-ON)
- **Holiday types:** Public, Bank, School, Authorities, Optional, Observance
