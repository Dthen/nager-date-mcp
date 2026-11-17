# Nager.Date MCP Server and `nager-date` CLI

MCP server and native command-line client wrapping the
[Nager.Date](https://github.com/nager/Nager.Date) public-holiday API: holidays
for 200+ countries with local names, types, long weekends and regional
subdivisions.

Both interfaces share one application layer (`nager_date_mcp.operations`) and
one HTTP client/transport. The CLI is a plain argparse program: it does not
start an MCP session, spawn a server process or require a source checkout.

- **CLI command:** `nager-date`
- **MCP server:** `python -m nager_date_mcp.server` (newline-delimited
  JSON-RPC 2.0 over stdio, stateless 2026-07-28 era)
- **Distribution:** `nager-date-mcp` 0.3.0, Python >= 3.10, zero runtime
  dependencies

## Interfaces

| Operation | MCP tool | CLI command | CLI flags |
|---|---|---|---|
| Holidays for a country/year | `get_holidays` | `nager-date holidays COUNTRY` | `--year YEAR`, `--type TEXT`, `--county CODE` |
| Upcoming holidays | `get_next_holidays` | `nager-date next-holidays COUNTRY` | `--county CODE` |
| Long weekends | `get_long_weekends` | `nager-date long-weekends COUNTRY` | `--year YEAR` |
| Is today a holiday? | `is_today_holiday` | `nager-date is-today-holiday COUNTRY` | `--county CODE`, `--offset HOURS` |
| Supported countries | `list_countries` | `nager-date countries` | `--query TEXT` |
| Country metadata | `get_country_info` | `nager-date country-info COUNTRY` | none |
| Worldwide upcoming holidays | `get_worldwide_holidays` | `nager-date worldwide` | none |

Common CLI options, accepted before or after the command: `--json`,
`--timeout SECONDS`, `-h`/`--help`, `--version`. Command-specific flags belong
after the command. Long-option abbreviation is disabled; extra, unknown,
repeated value options and missing values are usage errors (status 2). A
literal `--` ends option parsing; `--json` after it does not select JSON mode.
`--json` repeated is harmless.

`worldwide` and the MCP `get_worldwide_holidays` tool both describe
**upcoming** holidays from the existing `NextPublicHolidaysWorldwide` v3
endpoint, not only today's. The MCP tool description and handler docstring were
corrected to say "upcoming"; the endpoint, tool schemas, tool names/order and
returned data are unchanged. That wording-only change is the single delta from
the previously frozen `tools/list` capture, and the golden records it.

## Install (local unpublished candidate)

This pilot has no public PyPI release. Install the built wheel from a local
path, fully offline: without dependency resolution or index access (the
package has no runtime dependencies):

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --no-deps --no-index /path/to/nager_date_mcp-0.3.0-py3-none-any.whl
.venv/bin/nager-date --version      # nager-date 0.3.0
```

The two options are deliberate and serve different purposes: `--no-deps`
skips runtime dependency resolution (there are no runtime dependencies), while
`--no-index` disables package-index lookup and pip's implied version check, so
the install resolves nothing remotely. The same wheel installs on Python 3.10
and 3.11.

## CLI usage

```bash
nager-date holidays GB --year 2026
nager-date holidays DE --type Public --json
nager-date next-holidays US --county US-CA
nager-date long-weekends FR --year 2026
nager-date is-today-holiday GB --offset 0 --json
nager-date countries --query united
nager-date country-info JP
nager-date worldwide --json
```

Human output is the default. Country lists print `code  name`; holiday lists
print date, weekday, name and coverage; long weekends print start, end, day
count and bridge-day information; country metadata uses labelled fields; the
today check prints an explicit `COUNTRY: yes/no` sentence. Warnings and
failures go to stderr with their codes; failures leave stdout empty.

### JSON envelope

`--json` emits exactly one JSON object plus one newline. Expected operation
and usage outcomes are fully represented there; stderr stays empty except for
unexpected internal diagnostics.

```json
{
  "data": [
    {"date": "2026-01-01", "name": "New Year's Day", "day_of_week": "Thursday"}
  ],
  "warnings": [],
  "error": null
}
```

Illustrative shape only (fields abbreviated); live records retain all upstream
fields and their order. On success `error` is `null`; on failure `data` is
`null` and `error` is `{"code": "...", "message": "..."}`. Warnings are objects
with `code` and `message`; branch on codes, not English text.

Per-command `data`:

| Commands | `data` |
|---|---|
| `holidays`, `next-holidays`, `worldwide` | array of holiday objects (upstream fields plus `day_of_week`) |
| `long-weekends` | array of long-weekend objects (`startDate`, `endDate`, `dayCount`, `needBridgeDay`, `bridgeDays`, ...) |
| `countries` | array of `{"code": ..., "name": ...}` |
| `country-info` | country-metadata object (upstream keys, e.g. `commonName`, `region`, `borders`) |
| `is-today-holiday` | `{"country": "GB", "is_holiday": true}` |

An empty result is a successful empty array. A supported country's negative
today answer is `"is_holiday": false`. Neither becomes a `no_data` failure.
A country the API has no data for is `no_data` (status 1) with an explicit
message. `worldwide` 204 is a successful empty array; `is-today-holiday` 200
with an empty body means true.

`country-info` with an upstream `{}` is also a success: JSON `data` is `{}`,
`warnings` is `[]` and `error` is `null`, and human output is exactly
`No country metadata returned.` An empty object is not a claim that the
country is unsupported. A null or non-object metadata payload (e.g. 204 or a
bare string) remains `invalid_response`.

### Warnings

| Code | Meaning |
|---|---|
| `type_no_matches` | The `--type` filter removed every holiday from a non-empty list; the country may use another type label. |
| `county_national_only` | No regional holiday matched `--county`; results are limited to country-wide/global holidays, which may be zero after other filters. This does not prove the subdivision code is invalid. |

Both may appear, type first. A warning-bearing success still has status 0.

### Exit status and error codes

| Status | Meaning |
|---|---|
| 0 | Success, including empty lists, `"is_holiday": false`, warnings, help and version |
| 1 | Operation/output/internal failure |
| 2 | Invalid invocation or malformed typed value |
| 130 | Interrupted by Ctrl-C |

| `error.code` | Origin |
|---|---|
| `usage_error` | Parser/input boundary rejected the invocation (status 2) |
| `no_data` | The operation determined no holiday data for the country |
| `not_found` | Upstream HTTP 404 |
| `invalid_request` | Upstream HTTP 400 (e.g. unsupported year/offset) |
| `rate_limited` | Upstream HTTP 429; no automatic retry |
| `upstream_error` | Other unsuccessful upstream HTTP status |
| `timeout` | Typed request timeout |
| `network_error` | Other typed URL/connection/TLS failure |
| `invalid_response` | JSON/encoding failure, unusable payload shape, or non-finite number |
| `internal_error` | Unexpected application failure |
| `interrupted` | Ctrl-C before a result was emitted (status 130) |

HTTP 400/404 detection uses explicit status metadata, not message text. HTML
error pages are never dumped into CLI output.

## State, timeouts and cancellation

- One process owns one client and one in-memory TTL cache. A fresh invocation
  starts with an empty cache; there is no disk cache or session file.
- No configuration file, API key, credential or authentication is used. The
  CLI never reads stdin.
- `--timeout SECONDS` sets the blocking timeout for **each request**, default
  5 seconds. It is not a whole-command deadline. Commands that need a
  follow-up lookup (`is-today-holiday` on an ambiguous 204, `long-weekends`
  on an empty list) can issue a second request, so elapsed time may exceed one
  timeout interval. DNS, redirects and slow response delivery add time too.
- No automatic retry or sleep is performed.
- JSON console bytes are UTF-8 even when the terminal encoding is ASCII or
  Latin-1; lone surrogates are emitted as `\uXXXX` text. Human output uses the
  stream encoding and backslash-escapes unrepresentable characters. Remote
  names are never interpreted as terminal control sequences.
- Output is serialized before writing, but arbitrary output devices cannot be
  atomic. A closed pipe or device failure stops quietly with nonzero status,
  without a second envelope, retry or traceback. A signal after output begins
  can leave partial bytes: status 130, no replacement envelope. Ctrl-C before
  emission emits the structured `interrupted` error in JSON mode and a short
  `interrupted` line in human mode.

## MCP server setup

The MCP server keeps its stdio JSON-RPC interface and stateless era. Point
clients at the installed wheel environment's interpreter:

```yaml
mcp_servers:
  nager-date:
    command: <venv-root>/bin/python
    args: ["-m", "nager_date_mcp.server"]
    protocol: stateless
```

```json
{
  "mcpServers": {
    "nager-date": {
      "command": "<venv-root>/bin/python",
      "args": ["-m", "nager_date_mcp.server"]
    }
  }
}
```

`python -m nager_date_mcp.server` remains unchanged: one shared client,
tolerant tool schemas, era metadata on every result, and the historical
list-versus-`{"holidays": ..., "note": ...}` and friendly-string outcomes.
The server reads newline-delimited JSON-RPC on stdin and writes protocol-only
JSON on stdout.

## Python support

`requires-python` is `>=3.10`; the CLI, package import, wheel install and the
source suite were measured on CPython 3.10.22 and 3.11.15. This is the
measured extent of the floor claim, not a promise about untested versions.

## Development

From the checkout, install pytest in a test environment and run:

```bash
python3 -m pip install pytest
python3 -m pytest tests/ -v
```

Spawn-based tests require `PROD_PY` to name a distinct interpreter that
imports this checkout (stdlib-only, no third-party runtime dependencies), or
a gitignored `.prod_py` file containing that path. The wheel checks are
separate from the source suite.

## API details

- **Source:** https://date.nager.at/api/v3/ (v3 endpoints; no v4 migration)
- **Auth:** none
- **Rate limits:** none observed (Cloudflare CDN, 7-day cache headers)
- **Year range:** 1976–2076 (upstream rejects unsupported integers)
- **Countries:** 200+ (ISO 3166-1 alpha-2); the live catalogue count can change
- **Subdivisions:** ISO 3166-2 (e.g. GB-ENG, GB-SCT, DE-BY, CA-ON)
- **Holiday types:** Public, Bank, School, Authorities, Optional, Observance
- **License:** 0BSD (see `LICENSE`)
