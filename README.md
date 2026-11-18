# Nager.Date

Public-holiday lookups via the `nager-date` CLI or MCP. Python 3.10+; no runtime
dependencies, API key or configuration file.

## Install

Linux/POSIX shell:

```sh
git clone https://github.com/Dthen/nager-date-mcp.git
cd nager-date-mcp
python3 -m venv .venv
.venv/bin/python -m pip install --no-deps .
. .venv/bin/activate
nager-date countries --query united
```

Queries need internet access. Installation may download build tools.
Without activation, use `.venv/bin/nager-date`.

## Commands

Use two-letter country codes, such as `GB`. Run `nager-date COMMAND --help`
for filters and options.

| CLI | MCP tool |
|---|---|
| `holidays COUNTRY` | `get_holidays` |
| `next-holidays COUNTRY` | `get_next_holidays` |
| `long-weekends COUNTRY` | `get_long_weekends` |
| `is-today-holiday COUNTRY` | `is_today_holiday` |
| `countries` | `list_countries` |
| `country-info COUNTRY` | `get_country_info` |
| `worldwide` | `get_worldwide_holidays` |

```sh
nager-date holidays GB --year 2026 --json
nager-date next-holidays US --county US-CA
nager-date holidays DE --type Public
```

- Human output is the default; warnings and errors go to stderr.
- `--json` returns `{data, warnings, error}`. Errors and warnings contain `code`
  and `message`; branch on codes. On failure, `data` is null.
- Empty results, a negative holiday check and warnings are successful outcomes.
  A country with no API coverage returns `no_data` instead.
- `worldwide` means upcoming holidays, not just today's.
- `--timeout SECONDS` defaults to 5 **per request**, not per command. Some
  commands make a follow-up request. No automatic retries.
- Common flags may precede or follow the command. `--` ends option parsing.

Exit codes: **0** success/help/version; **1** operation/output failure;
**2** invalid invocation; **130** Ctrl-C.

## MCP setup

Use the installed environment's absolute Python path:

```yaml
mcp_servers:
  nager-date:
    command: /absolute/path/to/nager-date-mcp/.venv/bin/python
    args: ["-m", "nager_date_mcp.server"]
    protocol: stateless
```

Requires a client supporting stateless MCP `2026-07-28`; the legacy
`initialize` handshake is unsupported. MCP result shapes differ from the CLI's
JSON envelope.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for tests and wheel builds.
Report bugs with the command, Python version, exit code and output.

[API](https://date.nager.at/api/v3/) · [Issues](https://github.com/Dthen/nager-date-mcp/issues) · [0BSD license](LICENSE)
