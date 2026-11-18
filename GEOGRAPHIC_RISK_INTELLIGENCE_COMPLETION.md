# Geographic Risk Intelligence Map — Completion Report

## Delivered

- Added an interactive Leaflet world map to the existing Risk Intelligence dashboard.
- Markers are generated only from stored transactions that have valid coordinates:
  - Red: high or critical risk.
  - Amber: medium risk.
  - Green: low risk / no linked anomaly alert.
  - Marker size reflects transaction value.
- Nearby markers are clustered with `leaflet.markercluster`.
- Clicking a marker opens a detail panel with transaction ID, date, amount/currency, risk score, risk level, explanation, city, state, country, source file, source row, channel, coordinates, location source and linked alert IDs.
- Added geographic filters for date range, country, risk level, transaction channel and minimum amount. The map, KPIs, country panels, city rankings and trend update together.
- Added analytics for:
  - Mapped transaction count and unlocated count.
  - High-risk transactions and high-risk value.
  - Flagged value.
  - Countries and cities with valid coordinates.
  - Country risk distribution and flagged value by country.
  - Highest-risk cities.
  - Unusually active locations using a mean + 2 standard deviation activity threshold.
  - Geographic activity trend by date and risk level.
- Empty states explicitly show `Location unavailable` or the backend-provided reason. No coordinates, locations or demo counts are fabricated.
- The parent dashboard Refresh button and Run risk analysis both refresh the geographic panel. The panel also has its own Reload map button.

## Backend files

- `backend/app/geo_analytics.py` — geographic filtering, marker payloads, country/city aggregates, high-risk locations, unusual activity, trend and summaries.
- `backend/app/geocoding.py` — optional OpenStreetMap Nominatim client with SQLite caching and strict rate limiting.
- `backend/app/risk_api.py` — new geographic endpoints; existing `/api/risk/geography` is unchanged for backward compatibility.
- `backend/app/models.py` — nullable `TransactionRecord.currency`; additive `geocode_cache` table.
- `backend/app/csv_normalizer.py` — optional `currency`, `currency_code` and `ccy` column normalization.
- `backend/app/main.py` — persists normalized currency on uploaded transaction records.
- `backend/app/migrations.py` — existing additive migration path automatically creates the new column/table without deleting data.
- `backend/tests/test_geo_api.py` — geographic API, filters, empty states, marker details, cache and validation tests.

## Frontend files

- `frontend/src/GeographicRiskIntelligence.jsx` — Leaflet map, custom risk markers, clustering, detail panel, filters, KPI cards, country/city/unusual-activity panels and trend chart.
- `frontend/src/RiskIntelligence.jsx` — integrates the new component, refreshes it with dashboard actions and replaces the old scatter-plot geography section.
- `frontend/src/risk.css` — dark-theme map, marker, cluster, detail panel, filter and analytics styling with responsive breakpoints.
- `frontend/package.json` / `frontend/package-lock.json` — adds `leaflet` and `leaflet.markercluster`.

## Dependencies

| Dependency | Purpose |
|---|---|
| `leaflet` 1.9.4 | Interactive map |
| `leaflet.markercluster` 1.5.3 | Nearby-marker clustering |
| OpenStreetMap standard tiles | Map basemap; public tile server with attribution, no API key required |
| OpenStreetMap Nominatim | Optional free geocoding, disabled by default |

Install with:

```bash
cd frontend
npm install
```

## Database changes

The migration is additive and non-destructive:

- Added nullable `transaction_records.currency` (`VARCHAR(3)`).
- Added `geocode_cache` table with unique `query_key`, place names, display name, coordinates, provider, status and timestamps.
- Successful and unsuccessful geocoder results are cached in `geocode_cache`.
- Network failures are not cached as permanent misses and can be retried later.
- No records are deleted, no tables are recreated and no SQLite database is reset.

Verification against the existing local database:

- `transaction_records`: 1,650
- `risk_alerts`: 496
- Records with non-empty `city`, `country` or non-null `latitude`: 0

Therefore the current production/local database correctly shows the empty state until location columns are imported or optional geocoding is enabled. No fake location data was inserted.

## API endpoints

All new endpoints accept the same optional filters:

`start_date`, `end_date`, `country`, `risk_level`, `channel`, `min_amount`, `max_markers` and `geocode` where applicable.

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/risk/geography` | Existing coordinate-only endpoint, unchanged |
| GET | `/api/risk/geography/summary` | Full dashboard payload: summary, markers, countries, cities, high-risk locations, unusual activity, trend, filters and options |
| GET | `/api/risk/geography/map` | Marker payload and map summary |
| GET | `/api/risk/geography/countries` | Country statistics and high-risk countries |
| GET | `/api/risk/geography/cities` | City statistics, high-risk cities and unusual activity |
| GET | `/api/risk/geography/high-risk-locations` | Ranked high-risk and unusually active locations |
| GET | `/api/risk/geography/trend` | Geographic activity trend |
| GET | `/api/risk/geography/options` | Country, state, city and channel filter options plus geocoding status |

Example:

```bash
curl "http://127.0.0.1:8000/api/risk/geography/summary?country=India&risk_level=high&min_amount=10000"
```

Invalid dates, reversed date ranges, unsupported risk levels and negative amounts return HTTP 422 with a clear detail message.

## Configuration

Geocoding is optional, free and disabled by default:

| Variable | Default | Meaning |
|---|---|---|
| `FINAUDIT_GEOCODING_ENABLED` | `false` | Enables Nominatim lookups for named locations without coordinates |
| `FINAUDIT_GEOCODING_MAX_LOOKUPS` | `10` | Maximum network lookups per API request; hard-capped at 100 |
| `FINAUDIT_GEOCODING_URL` | Nominatim search URL | Optional provider URL override |
| `FINAUDIT_GEOCODING_USER_AGENT` | `FinAuditAI/1.0 (...)` | User agent sent to Nominatim |

The service rate-limits requests to approximately one per second and caches every completed lookup. Stored latitude/longitude values always take precedence. No IP-based location inference is performed.

## Tests

From `backend/`:

```bash
python3 -m pytest -q tests/test_geo_api.py
python3 -m pytest -q
```

Results:

- Geographic tests: **6 passed**
- Full backend suite: **107 passed**

Geographic tests cover:

- Stored-coordinate markers and marker details.
- Alert-derived risk levels and explanations.
- Country filtering when the location exists only on the linked alert.
- Country and city statistics.
- Date, country, risk-level, channel and minimum-amount filters.
- Empty states for missing locations.
- Cached geocode use and repeated-lookup avoidance.
- Network-error retry behavior.
- Specific endpoints and validation errors.

Frontend production build:

```bash
cd frontend
npm run build
```

Result: **passed**. The build reports only existing bundle-size and `"use client"` directive warnings; there are no build errors.

## Run commands

Backend:

```bash
cd backend
python3 -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Frontend:

```bash
cd frontend
npm run dev
```

Production frontend build:

```bash
cd frontend
npm run build
npm run preview
```

## Notes and limitations

- Alerts are anomaly indicators and investigation leads, not proof of fraud. Human review is required before any financial action.
- The map shows only transactions with stored or cached valid coordinates. Existing records without location fields remain honestly unplotted.
- To populate coordinates, import optional `latitude`/`longitude` (and `city`, `state`, `country`) columns, or enable cached geocoding for existing named locations.
- Map basemap tiles require network access in the browser. The analytics and empty-state behavior still work without tile access.
