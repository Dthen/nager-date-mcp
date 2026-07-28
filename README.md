# Nager.Date MCP Server

MCP server wrapping [Nager.Date](https://github.com/nager/Nager.Date) — public holidays for 150+ countries with local names, types, and long weekend detection.

## Why

- Zero existing MCP coverage
- Tiny, clean REST API — quick win, could be a first MCP build
- Genuinely practical for scheduling, planning, and calendar workflows

## API Notes

- **Auth:** None. Free. No key.
- **Base URL:** `https://date.nager.at/api/v3/`
- **Key endpoints:**
  - `PublicHolidays/{year}/{countryCode}` — holidays for a country/year
  - `NextPublicHolidays/{countryCode}` — upcoming holidays
  - `NextPublicHolidaysWorldwide` — next holidays globally
  - `CountryInfo/{countryCode}` — country details, regions, borders
  - `AvailableCountries` — list of supported countries
  - `LongWeekend/{year}/{countryCode}` — long weekend periods
  - `IsTodayPublicHoliday/{countryCode}` — check today
- **Rate limits:** Not published, but it's a small open-source project — be polite
- **Holiday types:** Public, Bank, School, Authorities, Optional, Observance

## Rough Tool Ideas

- `get_holidays(country, year)` — all holidays for a country/year
- `next_holidays(country)` — upcoming holidays
- `next_holidays_worldwide()` — what's coming up globally
- `long_weekends(country, year)` — long weekend periods
- `is_public_holiday(country, date?)` — check a specific date
- `country_info(country)` — regions, borders, country details
- `available_countries()` — list supported countries

## Status

⚠️ **Before proceeding:** This needs proper research and planning before any code is written. Use the `plan` skill for a thorough execution plan and `subagent-driven-development` for implementation. Research first, build second.

### Research TODO
- [ ] Confirm all endpoints, parameters, and response shapes
- [ ] Test edge cases (regional holidays, partial-day holidays)
- [ ] Check rate limits and reliability
- [ ] Verify country code format (ISO 3166-1 alpha-2)
- [ ] Investigate self-hosting option (it's open source .NET)
- [ ] Decide: TypeScript or Python?
