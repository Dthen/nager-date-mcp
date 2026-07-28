# Nager.Date Public Holidays API — Research Findings

> Researched: 2026-07-28 | API base: `https://date.nager.at/api/v3/`
> Alternate domain: `https://nagerholidays.com/api/v3/` (same backend, also works)

---

## 1. API Endpoints

### 1.1 PublicHolidays
**`GET /api/v3/PublicHolidays/{year}/{countryCode}`**

Returns all public holidays for a country in a given year.

| Param | Type | Location | Description |
|-------|------|----------|-------------|
| `year` | int | path | Year (1976–2076 supported) |
| `countryCode` | string | path | ISO 3166-1 alpha-2 code (e.g. `GB`, `US`) |

**Example request:**
```
GET https://date.nager.at/api/v3/PublicHolidays/2025/GB
```

**Example response (200):**
```json
[
  {
    "date": "2025-01-01",
    "localName": "New Year's Day",
    "name": "New Year's Day",
    "countryCode": "GB",
    "fixed": false,
    "global": false,
    "counties": ["GB-ENG", "GB-NIR", "GB-SCT", "GB-WLS"],
    "launchYear": null,
    "types": ["Public"]
  },
  {
    "date": "2025-04-18",
    "localName": "Good Friday",
    "name": "Good Friday",
    "countryCode": "GB",
    "fixed": false,
    "global": true,
    "counties": null,
    "launchYear": null,
    "types": ["Public"]
  }
]
```

**Errors:**
- `400` — Year out of range: `{"title":"One or more validation errors occurred.","status":400,"errors":{"year":["The specified year '1800' is not supported."]}}`
- `404` — Invalid country: `{"title":"Unknown country code","status":404,"detail":"The country code provided is invalid or not recognized."}`

---

### 1.2 NextPublicHolidays
**`GET /api/v3/NextPublicHolidays/{countryCode}`**

Returns upcoming holidays for a country (from today forward, spanning into next year).

| Param | Type | Location | Description |
|-------|------|----------|-------------|
| `countryCode` | string | path | ISO 3166-1 alpha-2 code |

**Example response (200):** Array of holiday objects (same shape as PublicHolidays). Returns ~13 items spanning remainder of current year + start of next.

---

### 1.3 NextPublicHolidaysWorldwide
**`GET /api/v3/NextPublicHolidaysWorldwide`**

Returns all holidays happening today worldwide (no parameters).

**Example response (200):** Array of holiday objects. On 2026-07-28 returned 57 holidays (ES regional, FO, PE, etc.).

---

### 1.4 CountryInfo
**`GET /api/v3/CountryInfo/{countryCode}`**

Returns metadata about a country including bordering countries.

| Param | Type | Location | Description |
|-------|------|----------|-------------|
| `countryCode` | string | path | ISO 3166-1 alpha-2 code |

**Example response (200):**
```json
{
  "commonName": "United Kingdom",
  "nativeName": "United Kingdom",
  "officialName": "United Kingdom of Great Britain and Northern Ireland",
  "countryCode": "GB",
  "region": "Europe",
  "borders": [
    {
      "commonName": "Ireland",
      "nativeName": "Éire",
      "officialName": "Republic of Ireland",
      "countryCode": "IE",
      "region": "Europe"
    }
  ]
}
```

**Errors:** `404` for invalid country code.

---

### 1.5 AvailableCountries
**`GET /api/v3/AvailableCountries`**

Returns all supported countries. No parameters.

**Response:** Array of `{countryCode, name}` objects. **202 countries** supported.

```json
[
  {"countryCode": "AD", "name": "Andorra"},
  {"countryCode": "AG", "name": "Antigua and Barbuda"},
  ...
]
```

---

### 1.6 LongWeekend
**`GET /api/v3/LongWeekend/{year}/{countryCode}`**

Returns all long weekends for a country in a given year.

| Param | Type | Location | Description |
|-------|------|----------|-------------|
| `year` | int | path | Year (1976–2076) |
| `countryCode` | string | path | ISO 3166-1 alpha-2 code |

**Example response (200) for US/2025:**
```json
[
  {
    "startDate": "2025-01-18",
    "endDate": "2025-01-20",
    "dayCount": 3,
    "needBridgeDay": false,
    "bridgeDays": []
  },
  {
    "startDate": "2025-06-19",
    "endDate": "2025-06-22",
    "dayCount": 4,
    "needBridgeDay": true,
    "bridgeDays": ["2025-06-20"]
  }
]
```

**Long weekend calculation logic:**
- A long weekend occurs when a holiday falls on a Friday or Monday (creating a 3-day weekend with Sat/Sun)
- `needBridgeDay: true` means there's a gap — a single working day between the holiday and the weekend that could be taken off to extend the break
- `bridgeDays` lists the specific date(s) that would need to be taken as leave
- `dayCount` is the total span from startDate to endDate inclusive

---

### 1.7 IsTodayPublicHoliday
**`GET /api/v3/IsTodayPublicHoliday/{countryCode}`**

Checks if today is a public holiday in the given country. Returns **no body**.

| Param | Type | Location | Description |
|-------|------|----------|-------------|
| `countryCode` | string | path | ISO 3166-1 alpha-2 code |
| `countyCode` | string | query (optional) | ISO 3166-2 subdivision code (e.g. `GB-ENG`) |
| `offset` | int | query (optional) | UTC timezone offset for "today" calculation |

**Response codes:**
- `200` — Today IS a public holiday
- `204` — Today is NOT a public holiday (no body)

Tested: GB on 2026-07-28 → `204` (not a holiday). PE on 2026-07-28 → `200` (Independence Day).

---

## 2. Response Field Reference

### Holiday Object
| Field | Type | Description |
|-------|------|-------------|
| `date` | string (ISO 8601 date) | Holiday date, format `YYYY-MM-DD` |
| `localName` | string | Name in local language (e.g. `元日` for JP, `Neujahr` for DE) |
| `name` | string | English name |
| `countryCode` | string | ISO 3166-1 alpha-2 country code |
| `fixed` | boolean | Whether the holiday is on a fixed date every year (NOTE: observed as `false` even for Jan 1 — seems unreliable) |
| `global` | boolean | `true` = nationwide; `false` = regional (check `counties`) |
| `counties` | string[] \| null | ISO 3166-2 subdivision codes where holiday applies. `null` when `global=true` |
| `launchYear` | int \| null | Year the holiday was established (usually `null`) |
| `types` | string[] | Holiday type(s) — see section 3 |

### LongWeekend Object
| Field | Type | Description |
|-------|------|-------------|
| `startDate` | string (date) | First day of the long weekend |
| `endDate` | string (date) | Last day of the long weekend |
| `dayCount` | int | Total days in the long weekend span |
| `needBridgeDay` | boolean | Whether a bridge day is needed to connect holiday to weekend |
| `bridgeDays` | string[] | Date(s) of bridge day(s) to take off |

### CountryInfo Object
| Field | Type | Description |
|-------|------|-------------|
| `commonName` | string | Common English name |
| `nativeName` | string | Name in native language |
| `officialName` | string | Official/formal name |
| `countryCode` | string | ISO 3166-1 alpha-2 |
| `region` | string | Geographic region (e.g. "Europe", "Americas") |
| `borders` | object[] | Neighboring countries (same shape minus `borders`) |

---

## 3. Holiday Types

Six types observed across all countries:

| Type | Meaning | Countries observed | Notes |
|------|---------|-------------------|-------|
| **Public** | Official public/national holiday | 19+ (all tested) | Most common; the default type |
| **Bank** | Bank holiday (banks/financial institutions closed) | US, BE, BR, IE | Often co-occurs with Public |
| **School** | School holiday (schools closed) | US, AT, IE, NL | e.g. Truman Day (US) |
| **Authorities** | Government authorities closed | US, NL | e.g. Truman Day (US) |
| **Optional** | Optional holiday (not mandatory) | US, BR, PT | e.g. Good Friday in US |
| **Observance** | Observance (not a day off) | US, CH, NL | e.g. Lincoln's Birthday (US) |

**US examples showing type combinations:**
- `New Year's Day` → `["Public", "Bank"]`
- `Lincoln's Birthday` → `["Observance"]`
- `Good Friday` → `["Optional"]`
- `Truman Day` → `["School", "Authorities"]`
- `Columbus Day` → `["Bank"]` (not Public!)

A holiday can have multiple types simultaneously.

---

## 4. Country Codes & Regional Support

- **Format:** ISO 3166-1 alpha-2 (uppercase, 2 letters)
- **Total countries:** 202
- **Subdivision format:** ISO 3166-2 (e.g. `GB-ENG`, `DE-BY`, `CA-ON`, `US-CA`)

### Regional holiday examples:
- **GB:** 4 subdivisions — `GB-ENG`, `GB-NIR`, `GB-SCT`, `GB-WLS`. Scotland gets 2 January; NI gets St Patrick's Day and Battle of the Boyne.
- **DE:** 16 Bundesländer — `DE-BW`, `DE-BY`, `DE-BE`, `DE-BB`, `DE-HB`, `DE-HH`, `DE-HE`, `DE-MV`, `DE-NI`, `DE-NW`, `DE-RP`, `DE-SL`, `DE-SN`, `DE-ST`, `DE-SH`, `DE-TH`. Epiphany only in BW/BY/ST.
- **CA:** 13 provinces/territories — `CA-AB` through `CA-YT`. Louis Riel Day only in MB. **31 holidays total** (most of any tested country).
- **AU:** 25 holidays with state-level subdivisions.

### Holiday counts by country (2025):
| Country | Holidays | Notes |
|---------|----------|-------|
| CA | 31 | Most holidays (provincial) |
| AU | 25 | State-level |
| PH | 21 | |
| DE | 20 | 16 states |
| CO | 18 | |
| US | 17 | Federal + observances |
| JP | 16 | All national |
| AR | 16 | |
| KR | 16 | |
| BR | 15 | |
| GB | 13 | 4 nations |
| CN | 6 | Fewest of major countries |

---

## 5. Year Range

- **Supported:** 1976–2076 (100-year window)
- Below 1976: `400` error
- Above 2076: `400` error
- Future years work fine (tested 2050, 2076)

---

## 6. Rate Limits & Reliability

### Rate limiting
- **No observable rate limiting.** 10 rapid sequential requests all returned `200`.
- No `X-RateLimit-*` headers present.
- No API key required for the public API.

### Caching headers
```
cache-control: public,max-age=604800    (7 days)
cf-cache-status: HIT                    (Cloudflare CDN)
age: 569796                             (~6.6 days cached)
last-modified: Wed, 22 Jul 2026 07:40:22 GMT
server: cloudflare
```

- Responses are cached at Cloudflare edge for **7 days**
- `Vary: Accept-Encoding`
- HTTP/2 and HTTP/3 supported (`alt-svc: h3=":443"`)

### Reliability
- Cloudflare-fronted, highly available
- `api-supported-versions: 3.0` header present
- Security headers: CSP, HSTS, X-Frame-Options, X-Content-Type-Options

---

## 7. Error Handling

| Scenario | Status | Response |
|----------|--------|----------|
| Invalid country code | 404 | `{"title":"Unknown country code","status":404,"detail":"..."}` |
| Year out of range | 400 | `{"title":"One or more validation errors occurred.","status":400,"errors":{"year":["..."]}}` |
| CountryInfo invalid | 404 | RFC 9110 problem detail with traceId |
| IsTodayPublicHoliday + not a holiday | 204 | No body |

Error format follows **RFC 9110 Problem Details** (`application/problem+json` style).

---

## 8. Self-Hosting

### GitHub Repository
- **Repo:** [github.com/nager/Nager.Date](https://github.com/nager/Nager.Date)
- **Language:** C# (.NET)
- **License:** MIT
- **Stars:** ~1,394 | Forks: ~217
- **Description:** "Easily access public holidays for 200+ countries via .NET, REST API, Docker, or NuGet for online and offline use."

### Docker
- **Image:** `nager/nager-date` on Docker Hub
- **Pulls:** ~777,000
- **Tags:** `latest`, `webapi-v2-build-NNN` (actively built, last updated 2026-07-28)
- **⚠️ License key required** for self-hosted Docker/NuGet (per README). Available via [GitHub Sponsors](https://github.com/sponsors/nager).

### NuGet
- Package: `Nager.Date` (library) / `Nager.Holiday` (API client)
- Also requires license key for offline use

### Important note
The public API at `date.nager.at` is **free and requires no API key**. Self-hosting via Docker or NuGet requires a sponsor license key. For an MCP server, using the public API is the simplest path.

---

## 9. API Versions & Domains

- **v3** is the current public API version (`api-supported-versions: 3.0`)
- Two domains serve the same API:
  - `https://date.nager.at/api/v3/` (original)
  - `https://nagerholidays.com/api/v3/` (newer official domain)
- README references a **v4** API docs page at `nagerholidays.com/scalar/#community-api-v4` but `/api/v4/` returns 404 — v4 may be sponsor-only or not yet public
- No Swagger/OpenAPI JSON endpoint found on the public API

---

## 10. MCP Server Design Considerations

### Recommended Tools

| Tool Name | Endpoint | Priority | Rationale |
|-----------|----------|----------|-----------|
| `get_holidays` | PublicHolidays | **High** | Core use case — "what are the holidays in X for year Y?" |
| `get_next_holidays` | NextPublicHolidays | **High** | "When is the next holiday in Japan?" |
| `get_long_weekends` | LongWeekend | **High** | "Any long weekends coming up?" — great for planning |
| `is_today_holiday` | IsTodayPublicHoliday | **Medium** | Quick yes/no check |
| `get_country_info` | CountryInfo | **Medium** | Country metadata, borders, region |
| `list_countries` | AvailableCountries | **Low** | Discovery — could be cached statically |
| `get_worldwide_holidays` | NextPublicHolidaysWorldwide | **Low** | Niche — "what holidays are happening today globally?" |

### Caching Strategy

Holidays are **static data** — they don't change for a given year/country combination.

| Data | Cache TTL | Rationale |
|------|-----------|-----------|
| PublicHolidays (year/country) | **24 hours** (or even 7 days) | Matches API's own `max-age=604800`. Historical years could be cached indefinitely. |
| NextPublicHolidays | **1 hour** | Changes daily as "today" advances |
| NextPublicHolidaysWorldwide | **1 hour** | Changes daily |
| LongWeekend (year/country) | **24 hours** | Static per year |
| CountryInfo | **7 days** | Rarely changes |
| AvailableCountries | **7 days** | Almost never changes |
| IsTodayPublicHoliday | **No cache** (or 5 min) | Must reflect current date; cheap call |

**Implementation:** In-memory cache (e.g. `Map` with TTL) is sufficient. Could also use `Cache-Control` / `ETag` headers from the API for conditional requests.

### Design Notes

1. **No auth needed** — public API, no key required
2. **Country code normalization** — accept lowercase input, uppercase before calling API
3. **Year defaults** — default to current year if not specified
4. **County filtering** — for countries with regional holidays (GB, DE, CA, AU), consider an optional `county`/`subdivision` parameter to filter results client-side
5. **Type filtering** — allow filtering by holiday type (e.g. only "Public" holidays, exclude "Observance")
6. **Error mapping** — map 404 → "Country not found", 400 → "Year out of range (1976-2076)"
7. **Rate limiting** — not needed client-side (no observed limits), but be polite; the CDN handles it
8. **Response enrichment** — could add day-of-week to holiday responses for better UX
9. **Batch queries** — "holidays in US and GB" would need 2 API calls; consider parallel fetching

### Example MCP Tool Signatures

```typescript
// Core tool
get_holidays(country: string, year?: number, type?: string, county?: string)
  → Holiday[]

// Forward-looking
get_next_holidays(country: string, county?: string)
  → Holiday[]

// Planning
get_long_weekends(country: string, year?: number)
  → LongWeekend[]

// Quick check
is_today_holiday(country: string, county?: string)
  → { isHoliday: boolean }

// Discovery
list_countries()
  → { code: string, name: string }[]

// Metadata
get_country_info(country: string)
  → CountryInfo
```

---

## 11. Edge Cases & Gotchas

1. **`fixed` field is unreliable** — New Year's Day (Jan 1) shows `fixed: false` for GB. Don't rely on this field.
2. **`launchYear` is almost always null** — rarely populated.
3. **`counties` vs `global`** — when `global: true`, `counties` is `null` (not an empty array). When `global: false`, `counties` always has values.
4. **GB Boxing Day** — `localName: "Boxing Day"` but `name: "St. Stephen's Day"` — the English name field uses the international name.
5. **JP localName** — uses Japanese characters (元日, 成人の日, etc.) — ensure UTF-8 handling.
6. **US has non-Public holidays** — Columbus Day is `["Bank"]` only, Lincoln's Birthday is `["Observance"]` only. If filtering for "days off", check for `Public` in types.
7. **CA has the most holidays** (31) due to provincial variations — responses can be large.
8. **Year range 1976–2076** — hard limit, returns 400 outside this range.
9. **IsTodayPublicHoliday returns 204 (no body)** for "not a holiday" — must check status code, not parse body.
10. **Two domains** — `date.nager.at` and `nagerholidays.com` both work; use `date.nager.at` as primary (more established).
