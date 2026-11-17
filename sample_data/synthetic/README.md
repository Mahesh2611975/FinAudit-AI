# Synthetic test data (fictional records)

All files in this directory are **synthetic, fictional test data**. They are for
manual testing only and are never loaded automatically by the application.

- `fictional_geo_transactions.csv` contains invented transactions with city,
  state, country and coordinate columns, canonical payment channels, inferred
  channel descriptions, duplicate rows, unusual same-day frequency and a range
  of transaction amounts.
- Transaction IDs use the `SYN-` prefix so synthetic rows are easy to identify.
- Upload this file only when you explicitly want to test geographic ingestion.
  It is not mixed with real uploaded records by any background process.
