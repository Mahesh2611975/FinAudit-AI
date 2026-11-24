# FinAudit AI — Dashboard, Administration Overview and Sidebar Audit

**Audit date:** 2026-11-24  
**Scope:** Financial Intelligence Dashboard, Administration Overview, shared sidebar/navigation, backend APIs, permissions and persisted SQLite data.  
**Mode:** Inspection only. **No fixes implemented.**

## 0. Scope, safety and method

- No source file, `.env`, database record or git state was modified.
- No backend or frontend server was started.
- No tests were run.
- No files were uploaded and no records were inserted, updated or deleted.
- The working tree already contained uncommitted user changes; those were left untouched.
- `backend/data/finaudit.db` was opened only through a read-only SQLite URI (`mode=ro`) with `PRAGMA query_only=ON`.
- Line numbers refer to the files as inspected on 2026-11-24. The working tree includes pre-existing uncommitted changes.

**Important caveat:** raw database counts in this report are DB-at-rest facts. Protected endpoints apply owner-scoped filters through `backend/app/auth.py:594-606` and `backend/app/ownership.py:268-293`, so an authenticated API response for a non-admin user may legitimately be a subset.

---

## 1. Existing component and route inventory

### 1.1 Financial Intelligence Dashboard

| Area | Component | Source | View / route | Data source |
|---|---|---|---|---|
| Dashboard shell | `Dashboard` | `frontend/src/Dashboard.jsx:41` | `overview`, `user-dashboard` (`main.jsx:3415-3420`) | `GET /api/dashboard/overview` |
| Transaction workspace | `Transactions` | `frontend/src/Transactions.jsx` | `transactions` (`main.jsx:3430-3432`) | `/api/transactions`, `/api/audits` |
| Reconciliation | `ReconciliationPage` | `frontend/src/main.jsx` | `reconciliation` (`main.jsx:3442-3449`) | `/api/reconciliations` |
| Risk Intelligence | `RiskIntelligence` | `frontend/src/RiskIntelligence.jsx` | `risk-intelligence` (`main.jsx:3453-3458`) | `/api/risk/*` |
| Geographic Intelligence | `GeographicRiskIntelligence` | `frontend/src/GeographicRiskIntelligence.jsx` | `geographic-intelligence` (`main.jsx:3460-3462`) | `/api/geo/*` |
| Vendor Intelligence | `VendorIntelligencePage` | `frontend/src/main.jsx:2007` | `vendor-intelligence` (`main.jsx:3483`) | invoice APIs |
| Investigation Cases | `CasesPage` | `frontend/src/main.jsx` | `cases` (`main.jsx:3439-3441`) | `/api/investigations` |
| AI Investigation Assistant | `Assistant` | `frontend/src/Assistant.jsx` | `assistant` (`main.jsx:3463-3465`) | `/api/assistant`, `/api/documents` |
| Agent Execution History | `AgentsPage` | `frontend/src/main.jsx:3608` | `agents` (`main.jsx:3466-3468`) | `/api/agents/runs` |
| Reports & Exports | `ReportsPage` | `frontend/src/main.jsx:3847` | `reports` (`main.jsx:3469-3471`) | `/api/reports/*` |
| Settings | `SettingsPage` | `frontend/src/main.jsx:4275` | `settings` (`main.jsx:3475-3477`) | `/api/system/health` |

The dashboard itself has no router library. Routing is a hand-rolled hash router:

- Navigation model: `frontend/src/main.jsx:105-165`
- Aliases and hash parsing: `main.jsx:218-232`
- Route registry: `ALL_NAV_ITEMS` / `ROUTABLE_VIEW_IDS`, `main.jsx:153-165`
- Navigation setter: `main.jsx:2956-2963`
- Hash/history restoration: `main.jsx:2976-3019`
- Render switch: `main.jsx:3415-3485`

### 1.2 Administration Overview

| Area | Component | Source | View / route | Data source |
|---|---|---|---|---|
| Administration Overview | `AdminDashboardPage` | `frontend/src/main.jsx:2691-2723` | `admin-dashboard` (`main.jsx:3421-3423`) | `GET /api/auth/users` |
| User Management | `AdminUsersPage` | `frontend/src/main.jsx:2556-2689` | `admin-users` (`main.jsx:3424-3426`) | `GET/POST/PATCH /api/auth/users` |
| System Settings | `SettingsPage` | `frontend/src/main.jsx:4275-4339` | `settings` | `GET /api/system/health` |
| Reports & Exports shortcut | `AdminDashboardPage` | `main.jsx:2718` | `reports` | `/api/reports/summary` |

`admin-dashboard` is routable (`main.jsx:160`) and is the backend-assigned admin landing view (`auth.py:123-134`, `main.jsx:172-179`), but it has no sidebar item.

### 1.3 Current sidebar groups

Source: `frontend/src/main.jsx:105-148`.

| Current group | Current items |
|---|---|
| Workspace | Dashboard, Transactions, Reconciliation |
| Risk & Intelligence | Risk Intelligence, Geographic Intelligence, Vendor Intelligence |
| Investigation Center | Investigation Cases, AI Investigation Assistant, Agent Execution History |
| Governance | Reports & Exports |
| Administration | User Management, Settings |

---

## 2. Frontend-to-backend-to-database mapping

### 2.1 Dashboard endpoint

All ten inspected dashboard elements come from a single payload:

- Frontend fetch: `frontend/src/Dashboard.jsx:50-63`
- API: `GET /api/dashboard/overview`
- Backend route: `backend/app/main.py:1567-1569`
- Calculation: `backend/app/dashboard.py:91-273`
- Payload: `metrics`, `charts`, `recent_activity`, `scope` (`dashboard.py:192-273`)

There are no per-widget dashboard API calls.

### 2.2 Per-element mapping

| # | Element / component | API | DB table(s) and logic | Reporting period | History / duplicates / currency | States |
|---|---|---|---|---|---|---|
| 1 | Transactions KPI — `Dashboard.jsx:112`, `147-159` | `/api/dashboard/overview` | `transaction_records`; `len(transactions)` (`dashboard.py:93,169`) | All persisted rows; no date filter | All imports combined; no dashboard dedupe; currency ignored, hardcoded INR (`Dashboard.jsx:20-21`) | Shared load/error; no card-level empty state |
| 2 | Total spending — `Dashboard.jsx:113` | same | `transaction_records`; absolute debit-like amounts (`dashboard.py:32-44,95-104`) | All persisted rows | All imports; no dedupe; no FX conversion | Shared |
| 3 | Net cash flow — `Dashboard.jsx:114` | same | `total_received - total_spending` (`dashboard.py:172`) | All persisted rows | All imports; no dedupe; no FX conversion | Shared |
| 4 | Risk alerts + severity distribution — `Dashboard.jsx:115`, `99-104`, `194-205` | same | `risk_alerts`; grouped by `severity`, summed (`dashboard.py:149-154,187,200-203`) | All persisted alerts; no `detected_at` range | All analysis runs; generator fingerprint dedupe exists upstream (`risk_engine.py:1015-1021`) but not in dashboard; alert currency ignored | Shared; blank pie on `[]` |
| 5 | Transaction activity — `Dashboard.jsx:163-180` | same | `transaction_records`; `_monthly_series()` (`dashboard.py:55-81`) | Last 6 **populated** `YYYY-MM` buckets, not a rolling calendar window | Includes future-dated rows; no dedupe; currency ignored; `count`/`net` computed but not plotted despite subtitle | Shared; blank chart on `[]` |
| 6 | Spending trend — `Dashboard.jsx:182-192` | same | `transaction_records`; second `_monthly_series()` call (`dashboard.py:196-199`) | Same 6 populated months | Same as #5 | Shared; blank chart on `[]` |
| 7 | Reconciliation status + match rate — `Dashboard.jsx:106-109,207-220` | same | `reconciliation_runs`, `reconciliation_results`; latest run by `id DESC` (`dashboard.py:106-147`) | **Latest run only** | No history accumulation; `duplicate_candidate` shown as a status but excluded from auto-matching (`reconciliation.py:582-586`); no currency columns | Shared; blank chart on `[]` |
| 8 | Category mix — `Dashboard.jsx:222-234` | same | `transaction_records`; `str(category or "Uncategorized")`, top 10 (`dashboard.py:84-88,217-223`) | All persisted rows | All imports; no dedupe; currency ignored | Shared; blank chart on `[]` |
| 9 | Recent database activity — `Dashboard.jsx:238-253` | same | `transaction_records` + `risk_alerts`; 8 newest of each concatenated (`dashboard.py:156-166,225-253`) | No range filter; top 8 per source | Transaction sort is by **string date**; alert date is `detected_at`; no dedupe; both amounts formatted as INR | Explicit empty state (`Dashboard.jsx:240`); no per-row type badge |
| 10 | Metric definitions — `Dashboard.jsx:254-260` | same | `scope` object built in `dashboard.py:254-272` | Documents each scope | Accurately describes most rules, but does not explain future dates, manual reconciliation links or mixed record classes | Maps raw object entries directly |

**Shared states** (`Dashboard.jsx:42-91,118-145`):

- Initial load: full-page spinner.
- Initial failure: `role="alert"` plus Retry button.
- Failed refresh after success: stale values retained with “Showing the last successful values”.
- Refresh button uses `aria-busy`; no per-card skeletons or error boundaries.
- Charts use `data={... || []}`, so empty data yields a blank chart rather than a dedicated message.

### 2.3 Focused data findings

Read-only queries against `backend/data/finaudit.db` produced:

```text
transaction_records: 515
dates > 2026-11-24: 3
currency: NULL 415, INR 100
categories: Uncategorized 441, Transfers 33, Transport and travel 21,
            Utilities and bills 11, Shopping 7, Healthcare 1, Food and dining 1
risk_alerts severity: high 494, critical 23, medium 12
risk_alerts review_status: open 529
latest reconciliation run: id=8, bank=410, ledger=5, results=416, matched=1
latest statuses: bank_only 398, duplicate_candidate 12, ledger_only 5, matched 1
```

#### Why recent activity shows dates after 2026-11-24

Three stored transactions are future-dated:

| id | transaction_id | date | source_file | source_row |
|---:|---|---|---|---:|
| 217 | `GEN-bac1f28e334f3b838c09` | 2026-12-01 | `transactions (5).csv` | 207 |
| 246 | `GEN-5b081ddb9cc9bf11df67` | 2026-12-02 | `transactions (5).csv` | 236 |
| 247 | `GEN-61ed41d499e1fd0dd17c` | 2026-12-02 | `transactions (5).csv` | 237 |

They belong to audit run 12, created 2026-10-30, and are sorted to the top by `dashboard.py:162-166`. Adjacent source rows are 2026-10-01, 2026-01-13, 2026-02-12 and 2026-02-13, which is consistent with a historical date-parsing defect. `DATE_PARSING_CORRECTION_REPORT.md:10-29` confirms a legacy `dayfirst=True` ISO swap and explicitly states **no DB backfill was performed** because the original CSV could not be found. The exact original raw values remain unverified; no correction should be applied without that source.

#### Why Uncategorized dominates

- Import calls `categorize_transaction(description, vendor, upi_ref)` at `backend/app/main.py:447-453`.
- `backend/app/category.py:36-42` lowercases and concatenates those fields, uses first keyword match from `category.py:20-33`, and falls back to `("Uncategorized", "uncertain")`.
- Dashboard also maps NULL/empty stored categories to `Uncategorized` (`dashboard.py:84-88`).
- 441 of 515 rows are `Uncategorized`; 234 of those have both blank vendor and blank description. Others such as `Acme Office Supplies` contain no matching keyword.
- This is a real stored label, not only a UI artifact.

#### Is 0.48% correctly calculated?

**Mathematically yes, under the implemented definition.** `dashboard.py:139-145` computes:

```text
matched / ((bank_record_count + ledger_record_count) / 2) * 100
= 1 / ((410 + 5) / 2) * 100
= 1 / 207.5 * 100
= 0.4819... -> 0.48
```

The frontend correctly treats `0.48` as an already-scaled percentage (`Dashboard.jsx:106-109`). It is **not** `1 / 416 result rows`.

**Semantic caution:** the sole “match” is `reconciliation_result` id 2911, `match_method='manual_link'`, `review_status='confirmed'`, with `amount_difference=-225.5` and `date_difference_days=494`. It was created through `POST /api/reconciliations/{id}/manual-link` (`main.py:1212-1345`) and is counted because `dashboard.py:129` accepts `status='matched'`. The rate therefore describes one manually linked row over the average side size, not an automated amount/date match rate.

#### Risk severity totals vs unresolved alerts

They are **not distinguished**:

- Dashboard sums every severity with no `review_status` filter (`dashboard.py:149-154,187`).
- The risk subsystem defines unresolved as `open + investigating` (`risk_engine.py:1209`).
- Current data has all 529 alerts `open`, so total alerts equals unresolved **by coincidence only**.
- If alerts are later marked `investigating`, `resolved` or reviewed, the dashboard total will continue to include them while `/api/risk/overview` will not.

#### Are transaction and risk-alert records mixed?

Yes, in **Recent database activity** only. `dashboard.py:225-253` concatenates 8 transactions and 8 alerts into one list. `kind` is used solely as a React key (`Dashboard.jsx:243`); there is no type badge, no separate columns and no explanation that alert dates are detection timestamps rather than business-event dates. Both amounts are rendered with `currency()`, which hardcodes INR.

KPIs and charts are otherwise table-specific (transactions vs `risk_alerts`), but they share one payload and inconsistent scopes.

#### Consistent period and currency?

No.

| Scope used | Elements |
|---|---|
| All persisted rows / all imports | Transactions, Total spending, Net cash flow, Risk alerts, Category mix |
| Last 6 populated months | Transaction activity, Spending trend |
| Latest reconciliation run only | Reconciliation status and match rate |
| Top 8 per record type | Recent database activity |

Currency is stored at `TransactionRecord.currency` (`models.py:162`) and `RiskAlert.currency` (`models.py:626`) but is never read by the dashboard. `Dashboard.jsx:20-21` hardcodes INR. Current data is effectively INR-only (415 transaction currencies are NULL, 100 are INR), so display looks consistent today, but a non-INR import would be summed and labelled INR without conversion.

---

## 3. Administration Overview mapping

| Item | Frontend | Backend | Evidence / finding |
|---|---|---|---|
| Account count | `AdminDashboardPage` fetches `/api/auth/users`, sets `total: users.length` (`main.jsx:2694-2702,2711`) | `list_users()` returns all `User` rows, admin-only (`auth.py:1009-1016`) | Includes disabled, approved, pending and legacy users. Counts are never refreshed after in-page actions. Fetch failure is swallowed by `.catch(() => {})` (`main.jsx:2704`), leaving `0 / 0` as if real. |
| Pending approval count | Filters `approval_status === 'pending_approval'` (`main.jsx:2701`, also `2600`) | `approval_status()` derives state from `is_active` / `is_provisioned` (`auth.py:338-350`) | Reject sets both flags false (`auth.py:1092-1093`), which maps back to `pending_approval`. Pending count does not decrease and no terminal rejected state exists. |
| Role management | Dropdowns: `ADMIN_ROLE_OPTIONS` and `APPROVAL_ROLE_OPTIONS` (`main.jsx:2553-2554,2639-2646`) | Registration maps account type to `user`/`lender` (`auth.py:107-112,802-808,949`); approval validates against `APPROVABLE_ROLES` (`auth.py:115-121,1078-1085`); PATCH validates against `ALL_ROLES` (`auth.py:1114-1119`) | “Account type” column is derived from mutable `role` (`main.jsx:2635`); `User` has no `account_type` column (`models.py:710-727`). Admin can be assigned to a pending user via PATCH, then activated by a later approval that omits `role` (`auth.py:1078`), bypassing the “approval never grants admin” rule at `auth.py:1070-1072`. |
| Approve action | `POST /api/auth/users/{id}/approval` with `{approved:true, role:draft}` (`main.jsx:2656`) | `set_user_approval()`, admin-only (`auth.py:1061-1098`) | Validation: 404 missing, 422 invalid role, 403 non-admin. Writes `is_active`, `is_provisioned`, `role`, `updated_at`. |
| Reject action | `POST /api/auth/users/{id}/approval` with `{approved:false}` (`main.jsx:2664`) | same endpoint | UI shows “Rejected”, backend persists pending state (see above). |
| Save role | `PATCH /api/auth/users/{id}` with `{role:draft}` (`main.jsx:2674`) | `update_user()`, admin-only (`auth.py:1101-1131`) | Role changes take effect immediately for authorization because permissions are resolved from DB on each request (`auth.py:572,292-293`), but cached `currentUser` is not refreshed (`main.jsx:3198-3205`), so header/sidebar role can be stale. |
| Shortcuts | User Management, System Settings, Reports & Exports (`main.jsx:2715-2718`) | Routes exist (`main.jsx:137,144,145,153-165,3424-3426,3469-3471,3475-3477`) | Admin overview is the only page with all three shortcuts. Reports is a financial DATA & REPORTS surface; this blurs the admin/financial boundary. |
| Backend permission checks | UI guard `isViewAllowed()` (`main.jsx:200-206`) | All admin endpoints use `Depends(require_roles(ROLE_ADMIN))` (`auth.py:1012,1023,1066,1106`); `require_roles` includes active-user gate (`auth.py:707-715`) | Auth router is mounted without the central policy dependency (`main.py:183`) but endpoint-level guards are present. `/api/auth/roles` and `/api/auth/me` are active-user readable, not admin-only. |
| Audit trail | No admin audit UI | Python `logger.info` only (`auth.py:963-966,1015,1054-1057,1088-1091,1113,1130`) | No persistent admin-action table exists in `models.py`; no reason, IP, request ID or structured before/after record is stored. `reconciliation_review_actions` is reconciliation-specific, not admin-wide. |

**Separation:** Administration Overview is a distinct component and queries only `/api/auth/users`; it does not consume `/api/dashboard/overview`. However, it is absent from the sidebar, its active nav state is wrong, and it links into Reports & Exports.

---

## 4. Verified defects and evidence

| ID | Severity | Defect | Evidence |
|---|---|---|---|
| V1 | High | Dashboard mixes all-time, last-6-populated-months, latest-run-only and top-N reporting scopes without a single period selector or consistent card labels. | `dashboard.py:93,55-81,114,149-154,162-166`; `scope` at `254-272`; `Dashboard.jsx:111-115`. |
| V2 | High | Currency is hardcoded to INR; stored currency fields are ignored and no FX conversion/grouping exists. | `Dashboard.jsx:20-21,247`; `models.py:162,626`; DB: 415 NULL / 100 INR transaction currencies. |
| V3 | High | Recent activity surfaces three transactions dated after the current date because rows are sorted by date string, not import time; historical `dayfirst` defect likely but unverified. | `dashboard.py:162-166`; DB ids 217/246/247; `DATE_PARSING_CORRECTION_REPORT.md:10-29,55-60`. |
| V4 | Medium | Uncategorized dominates Category Mix (441/515 = 85.6%) due to keyword-only categorization and missing descriptions; no drilldown or recategorization affordance. | `main.py:447-453`; `category.py:20-42`; `dashboard.py:84-88`; DB distribution. |
| V5 | Medium | 0.48% is arithmetically correct but misleading: denominator is average side size and the single match is a manual link with large amount/date differences. | `dashboard.py:129,139-145`; DB run 8; `main.py:1255-1286`. |
| V6 | High | Total risk alerts are not the same as unresolved alerts; dashboard has no review-status filter and equals unresolved only because all current rows are `open`. | `dashboard.py:149-154,187`; `risk_engine.py:1209`; DB: 529 open. |
| V7 | Medium | Recent activity mixes transaction and alert records in one table with no type badge; alert dates are detection timestamps. | `dashboard.py:225-253`; `Dashboard.jsx:241-249`. |
| V8 | Medium | Dashboard counts duplicate transaction rows. Import-time dedupe exists but dashboard does not de-duplicate; 13 natural-key duplicate groups remain. | `dashboard.py:93`; `csv_normalizer.py:608-629`; `main.py:301-324,420-427`; DB duplicate-key groups. |
| V9 | High | Admin overview KPIs go stale after Approve/Reject/Save role and silently display `0 / 0` on fetch failure. | `main.jsx:2694-2706,2720`; `runAction` reloads only child state at `2592`. |
| V10 | High | Reject does not create a rejected state; backend maps `approved=false` back to `pending_approval`. | `auth.py:1092-1093` + `auth.py:338-350`; UI text at `main.jsx:2664`. |
| V11 | High | Account type and role validation are inconsistent: registration accepts only `user`/`lender`, approval excludes `admin`, PATCH allows `admin` on pending users, and a later role-less approval can activate that admin. | `auth.py:107-112,802-808,949,1078-1085,1114-1119`; `main.jsx:2553-2554,2635`. |
| V12 | High | Administrative actions are only written to application logs; no persistent admin audit table or UI exists. | `auth.py:963-966,1015,1054-1057,1088-1091,1113,1130`; table list in `models.py`; no audit view in `main.jsx`. |
| V13 | Medium | Administration Overview is the admin landing view but has no sidebar item; Financial “Dashboard” remains highlighted when it is open. | `main.jsx:105-148,160,172-179,3159-3162,2960-2961,3115-3118,3421-3423`; `auth.py:123-134`. |
| V14 | Medium | Frontend visibility is role/view-based, not capability-based despite backend `permissions` being available; lenders see Vendor Intelligence under Intelligence and all roles see Settings under Administration. | `main.jsx:183-205,3192-3196`; `auth.py:216-285,742-775`; no frontend read of `user.permissions`. |
| V15 | Medium | Sidebar accessibility gaps: no `aria-expanded` on group toggles, no `aria-current="page"`, no mobile Escape/backdrop/focus management, no workspace `:focus-visible` rule. | `main.jsx:3289-3377`; `style.css:103-351,422-477,1313-1385`; focus rule only in `landing.css:42-62`. |
| V16 | Low | Transaction Activity subtitle says “count, spending and receipts” but only `spending` and `received` are plotted; `transactions` and `net` are computed but unused. | `Dashboard.jsx:163,176-177`; `dashboard.py:71-81`. |

---

## 5. Potential issues requiring runtime verification

These are code-visible or data-visible concerns whose user impact needs a running system:

1. Authenticated API counts may differ from the raw DB counts because of owner scoping (`auth.py:594-606`, `ownership.py:268-293`).
2. The original raw values for the three future-dated CSV rows are unavailable; a verified mapping requires the authoritative `transactions (5).csv`.
3. Empty charts render blank plot areas; visual adequacy should be confirmed in a browser.
4. Direct hash navigation to a disallowed view may render briefly before the redirect effect at `main.jsx:3120-3126`.
5. Admin Overview issues two independent `GET /api/auth/users` calls: parent at `2696` and compact child at `2720` / `2568`.
6. Legacy mixed-case `users.role` values could break exact string permission checks (`auth.py:134,289,711`) while write endpoints normalize case.
7. Approval has no optimistic locking; concurrent admin actions are last-commit-wins (`auth.py:1074-1096`).
8. There is no last-admin/self-demotion guard on PATCH (`auth.py:1114-1119`).
9. Log sink and retention for `logger.info` depend on deployment configuration; source alone does not establish persistence.
10. Whether counting a manual reconciliation link as a “matched transaction” is intended is a business decision requiring confirmation.

---

## 6. Recommended UI improvements

### Financial Intelligence Dashboard

- Add an explicit reporting-period control and show the active scope on every KPI: `All stored imports`, `Last 6 populated months`, or `Latest reconciliation run #N`.
- Distinguish `Total alerts` from `Open/unresolved alerts`; add review-status filtering and a `Manual link` badge for reconciliation matches.
- Show reconciliation count, denominator and formula in a tooltip, e.g. “1 confirmed match / average of 410 bank + 5 ledger records”.
- Add a record-type column/badge to Recent Database Activity, or split it into two tabs/tables: Transactions and Risk Alerts.
- Show transaction event date and alert detection date in separate labelled columns; flag or filter future-dated rows rather than silently ranking them first.
- Group values by currency or display a per-KPI currency badge; never assume INR when `currency` is NULL or non-INR.
- Add an “Uncategorized” drilldown with source-file/row provenance and a recategorization action; show “Top 10 + Other”.
- Offer “raw rows” and “unique rows” counts, with duplicate candidates linked to the reconciliation view.
- Give charts explicit empty-state messages and per-card error boundaries/skeletons.
- Correct the Transaction Activity subtitle or plot `count` and `net` as well.
- Render Metric Definitions with human-readable labels rather than raw object keys.

### Administration Overview

- Lift user state to the parent or pass an `onChanged` callback so counts refresh after every action.
- Show a real error state instead of silently falling back to `0 / 0`.
- Add a terminal `rejected` state distinct from `pending_approval` and `disabled`.
- Replace derived “Account type” with a persisted, validated field or remove the column and label it “Role class”.
- Refresh `currentUser` after role changes so header/sidebar permissions are not stale.
- Add confirmation, reason capture and structured before/after records for approve, reject and role changes.
- Add a read-only Audit Logs view once a persistent trail exists.
- Move the Reports & Exports shortcut under a DATA & REPORTS section or remove it from the admin overview to preserve separation.

---

## 7. Recommended sidebar changes

The proposed grouping is mostly a renaming/regrouping exercise; almost all items already exist.

| Proposed group / item | Status | Recommended action |
|---|---|---|
| WORKSPACE → Financial Overview | Partial; label is `Dashboard`, view `overview` | Rename to “Financial Overview”; keep view `overview`/`user-dashboard`. |
| WORKSPACE → Transactions | Implemented | Keep. |
| WORKSPACE → Reconciliation | Implemented | Keep. |
| INTELLIGENCE → Risk Intelligence | Implemented; group is “Risk & Intelligence” | Rename group to “INTELLIGENCE”. |
| INTELLIGENCE → Geographic Intelligence | Implemented | Keep. |
| INTELLIGENCE → Vendor Intelligence | Implemented but also in `INVOICE_VIEWS`, so lenders see it | Keep as an intelligence item; remove from lender visibility or add a dedicated capability check. |
| INVESTIGATIONS → Investigation Cases | Implemented; group is “Investigation Center” | Rename group to “INVESTIGATIONS”. |
| INVESTIGATIONS → AI Investigation Assistant | Implemented | Keep. |
| INVESTIGATIONS → Agent Execution History | Implemented | Keep. |
| DATA & REPORTS → Documents & Evidence | Not a nav item/view; capabilities exist in APIs and Assistant/Transactions | Do **not** add until a standalone page exists. Backend support is at `main.py:1812-1930`, `auth.py:645-649`. |
| DATA & REPORTS → Reports & Exports | Implemented as “Governance” | Move under “DATA & REPORTS”. |
| ADMINISTRATION → User Management | Implemented | Keep. |
| ADMINISTRATION → System Settings | Implemented as read-only “Settings” | Rename; decide whether non-admin access belongs here or in a personal/preferences area. |
| ADMINISTRATION → Audit Logs | Not implemented | Do **not** add until a persistent audit trail and endpoint exist. |
| ADMINISTRATION → Administration Overview | Routable but missing from sidebar | Add a distinct nav item for `admin-dashboard`; stop highlighting Financial Overview while it is open. |

Additional sidebar improvements:

- Drive visibility from backend `permissions` rather than hardcoded role/view sets.
- Persist group expansion state; add `aria-expanded`, `aria-current="page"` and roving focus/arrow-key support.
- Add an Escape handler, backdrop and focus return for the mobile drawer.
- Ensure the collapsed rail still exposes group identity and active state accessibly.
- Keep Financial Intelligence Dashboard and Administration Overview as separate nav items with separate titles, active states and shortcuts.

---

## 8. Exact files that would need modification

### Required for UI/navigation restructuring

| File | Purpose |
|---|---|
| `frontend/src/main.jsx` | Nav constants (`92-216`), role view sets, navigation setter (`2956-2963`), guard (`3120-3126`), titles (`3158-3185`), visibility filter (`3192-3196`), sidebar JSX (`3270-3365`) and view switch (`3415-3485`). |
| `frontend/src/style.css` | Sidebar/group styles (`103-351,422-477`) and responsive rules (`1313-1403`); add focus/ARIA styles. |
| `frontend/scripts/verify-navigation.mjs` | Update sidebar contract (`35-67`) and the assertion that forbids an Evidence & Documents group (`280-283`). |
| `frontend/src/Dashboard.jsx` | Scope labels, record-type badges, currency handling, empty states, chart subtitle fix and Metric Definitions presentation. |

### Required if backend semantics are changed

| File | Purpose |
|---|---|
| `backend/app/dashboard.py` | Period selector, unresolved-alert filter, currency grouping, duplicate-aware counts, future-date flags and match-rate explanation. |
| `backend/app/auth.py` | Reject state, role/account-type validation consistency, admin audit logging and capability keys. |
| `backend/app/models.py` | Persistent `account_type` and admin-action audit table, if adopted. |
| `backend/app/migrations.py` | Additive migrations only, with backups and dry-run pre-checks. |
| `backend/app/main.py` | New endpoints for Audit Logs / Documents workspace and permission mappings. |
| `backend/app/ownership.py` | Row/RAG scoping for any new owned surfaces. |
| `backend/app/csv_normalizer.py` | Future-date validation at import time, if adopted. |
| `backend/app/category.py` | Categorization rules and fallback handling. |
| `backend/app/risk_engine.py` | Explicit unresolved vs total alert semantics if the API contract changes. |
| `frontend/src/api.js` | Capability helper for genuinely capability-based navigation. |
| `frontend/src/Transactions.jsx` | Surface currency/date validation and provenance in the transaction workspace. |

### New files, only if those shortcuts are implemented

- `frontend/src/DocumentsEvidence.jsx` — standalone Documents & Evidence page.
- `frontend/src/AuditLogs.jsx` — standalone Audit Logs page.

---

## 9. Regression tests needed

### Backend (pytest)

- `backend/tests/test_management_api.py` / new `test_dashboard_api.py`:
  - dashboard totals use the declared period;
  - debit/credit classification and net cash flow;
  - currency grouping or explicit error on mixed currency;
  - duplicate-aware vs raw row counts;
  - unresolved alerts exclude `resolved` and include `investigating`;
  - match rate formula and manual-link labelling;
  - future-dated rows are flagged/filtered, not silently ranked first;
  - empty `transaction_records`, `risk_alerts` and reconciliation tables return safe payloads.
- `backend/tests/test_auth_rbac.py`:
  - all admin endpoints reject non-admins and disabled admins;
  - reject produces a terminal `rejected` state, not `pending_approval`;
  - registration accepts only `user`/`lender` account types;
  - approval cannot grant `admin`;
  - PATCH cannot be used to bypass approval no-admin rules;
  - self-demotion/last-admin guard;
  - admin actions write structured audit records.
- `backend/tests/test_csv_normalizer.py`:
  - ISO timestamps never swap month/day;
  - future dates are rejected or flagged according to the chosen policy.
- `backend/tests/test_category.py`:
  - fallback rate and keyword rules are deterministic;
  - NULL/empty descriptions are handled explicitly.

Use temporary copied databases for any test that writes; never run new tests against `backend/data/finaudit.db` or preserved backups.

### Frontend

- Extend `frontend/scripts/verify-navigation.mjs`:
  - exact group and item labels from the proposed grouping;
  - `admin-dashboard` has its own sidebar item;
  - `documents` and `audit-logs` are absent until implemented;
  - hash aliases and direct URLs resolve correctly;
  - role-based and capability-based visibility expectations.
- If a UI test harness is added (none exists in `frontend/package.json`), add tests for:
  - active nav state for `overview`, `admin-dashboard`, `admin-users`;
  - `aria-expanded` and `aria-current`;
  - mobile Escape/backdrop/focus return;
  - KPI scope labels and empty states;
  - record-type badges in Recent Database Activity.

---

## 10. Safe implementation sequence

All steps below are **future recommendations only**; none were performed in this audit.

1. **Preserve state.** Create a branch/worktree, confirm the existing uncommitted changes, and take a read-only snapshot/checksum of `backend/data/finaudit.db` and preserved backups.
2. **Navigation-only pass.** Rename/regroup sidebar labels in `main.jsx`, add the `admin-dashboard` nav item, update `style.css` and `verify-navigation.mjs`. No backend or data changes. Verify `npm run build` and `npm run verify:navigation`.
3. **Presentation-only dashboard pass.** Add scope labels, record-type badges, chart empty states and the corrected subtitle in `Dashboard.jsx`. Use mocked payloads; do not write to SQLite.
4. **Admin UI pass.** Lift user state, refresh counts after actions, add real error states and refresh `currentUser`. Keep backend behavior unchanged initially.
5. **Backend semantics behind explicit parameters.** Add opt-in period/currency/dedupe/resolved-alert parameters to `dashboard.py`, with tests on copied DB fixtures. Do not silently change existing default numbers without stakeholder approval.
6. **Admin state and validation pass.** Add terminal reject state, consistent account type/role validation and guards. Apply additive migrations only after backup and dry-run review.
7. **Persistent audit trail.** Add an admin-action table, endpoint and Audit Logs page; only then add the `Audit Logs` nav item.
8. **Documents & Evidence page.** Add a standalone page only after permission/ownership scoping is complete; then add the nav item and update the verifier.
9. **Capability-based navigation and accessibility.** Switch frontend visibility to backend permissions, add ARIA/keyboard support and final responsive regression.
10. **Final verification.** Re-run backend pytest against copied DBs, frontend build/navigation checks, and manually verify all KPI scopes, admin actions and route guards in a non-production environment.

**Do not** backfill future dates without the original CSV and explicit approval, as required by `DATE_PARSING_CORRECTION_REPORT.md:29-42,53`.
