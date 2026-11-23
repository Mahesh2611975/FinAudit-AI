# Date Parsing Correction Report — `transactions (5).csv`

**Date**: 2026-11-23  
**Status**: **BLOCKED — NO DB BACKFILL PERFORMED**

## Scope

Investigate reported date parsing issue in `backend/app/csv_normalizer.py::_parse_date` affecting timestamped ISO strings and reported ~52 affected rows in `transactions (5).csv`.

## Confirmed Code Defect

- **File**: `backend/app/csv_normalizer.py`, `_parse_date`
- **Root cause**: ISO datetime/timestamp strings (`YYYY-MM-DDTHH:MM:SS`, `YYYY-MM-DD HH:MM:SS`, etc.) did not match the exact `YYYY-MM-DD` fast path and fell through to `pd.to_datetime(..., dayfirst=True)`. Ambiguous month/day values were swapped.
- **Confirmed repro (before fix)**:
  - `2025-08-05T00:00:00` -> `2025-05-08` (wrong)
  - `2025-02-03T00:00:00` -> `2025-03-02` (wrong)
  - `2025-01-02 00:00:00` -> `2025-02-01` (wrong)
- **Fix applied**: ISO-like `YYYY-MM-DD` prefixes (date or datetime/timestamp) are parsed strictly from the first 10 chars using `%Y-%m-%d`; invalid ISO-like dates return `None` instead of being dayfirst-swapped.
- **Regression tests**: added in `backend/tests/test_csv_normalizer.py` (`test_iso_timestamp_dates_are_parsed_without_day_month_swap`, `test_iso_timestamp_dates_normalize_through_csv_import`).

## Source File Availability

- **Required source**: `transactions (5).csv`
- **Result**: **NOT FOUND** in repository workspace or accessible Downloads search paths during audit.
- **DB evidence**: `transaction_records` contains 410 rows with `source_file='transactions (5).csv'`; all stored `date` values are 10-char ISO dates (`YYYY-MM-DD`), min `2025-01-12`, max `2026-12-02`.

## Record-by-Record Correction Mapping

**Cannot be produced as verified corrections without the original source CSV.** Per instructions, no existing DB records were modified/backfilled.

A verified mapping must include, at minimum:

| source_file | source_row | transaction_record_id | stored_date | original_source_date | proposed_date | confidence | evidence |
|---|---:|---:|---|---|---|---|---|

Required before any backfill:
1. Locate authoritative original `transactions (5).csv`.
2. Extract raw source date value per row.
3. Compare raw source value to `transaction_records.date` using `(source_file, source_row)`.
4. Generate dry-run CSV/MD report with old->new mapping and parser rule used.
5. Obtain explicit approval.
6. Backup DB, apply in transaction, verify row counts/checksums, record rollback plan.

## Heuristic DB Indicators (Unverified — NOT corrections)

These are signals only; they may include legitimate dates and must not be used for automatic correction:

- 410 rows from `transactions (5).csv` in DB.
- 132 rows have month <= 12 and day <= 12 (ambiguous if originally timestamped and misparsed).
- 278 rows have day > 12 (would not be swapped by this specific dayfirst bug).
- A conservative chronological adjacency heuristic found 28 adjacent inversions and 7 simple local-order-restoring swaps. Examples observed in DB (unverified): row 22 `2025-05-06 -> 2025-06-05?`, row 35 `2025-02-07 -> 2025-07-02?`, row 61 `2025-06-08 -> 2025-08-06?`, row 104 `2025-08-10 -> 2025-10-08?`, row 202 `2026-01-05 -> 2026-05-01?`, row 398 `2026-03-04 -> 2026-04-03?`, row 404 `2026-04-07 -> 2026-07-04?`.

**Do not apply heuristic corrections to the database.**

## Safety Statement

- No SQLite database file was deleted/reset/recreated.
- No financial records were deleted or silently overwritten.
- Parser fix prevents recurrence on future imports.
- Historical backfill remains pending verified source mapping and explicit approval.