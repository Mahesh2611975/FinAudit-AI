import math
import os
import tempfile

import pandas as pd

TEST_DB_DIR = tempfile.mkdtemp(prefix="finaudit-reconciliation-tests-")
os.environ["FINAUDIT_DATABASE_URL"] = f"sqlite:///{os.path.join(TEST_DB_DIR, 'test.db')}"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.reconciliation import ReconciliationError, reconcile_frames  # noqa: E402

client = TestClient(app)


def frame(rows, columns=None):
    return pd.DataFrame(rows, columns=columns)


def result_for(outcome, **matches):
    for item in outcome.results:
        if all(item.get(key) == value for key, value in matches.items()):
            return item
    raise AssertionError(f"No result matched {matches}: {outcome.results}")


def _csv_upload(name, content):
    return (name, content.encode("utf-8"), "text/csv")


def test_exact_reference_matching_priority():
    bank = frame([{"date": "2026-04-01", "type": "debit", "amount": "100.00", "reference": "REF-1", "description": "A"}])
    ledger = frame([{"date": "2026-04-01", "type": "debit", "amount": "100.00", "reference": "REF-1", "description": "A"}])
    outcome = reconcile_frames([("bank.csv", bank)], [("ledger.csv", ledger)])
    assert outcome.summary["matched_count"] == 1
    item = outcome.results[0]
    assert item["status"] == "matched"
    assert item["match_method"] in {"exact_reference_amount", "reference_amount_tolerance"}
    assert item["bank_source_file"] == "bank.csv"
    assert item["ledger_source_file"] == "ledger.csv"
    assert item["bank_source_row"] == 1
    assert item["ledger_source_row"] == 1


def test_exact_amount_and_date_matching_with_compatible_description():
    bank = frame([{"date": "2026-04-02", "type": "credit", "amount": "250.00", "description": "Client payment invoice 9"}])
    ledger = frame([{"date": "2026-04-03", "type": "credit", "amount": "250.00", "narration": "Client payment inv 9"}])
    outcome = reconcile_frames([("bank.csv", bank)], [("ledger.csv", ledger)], date_tolerance_days=1)
    assert outcome.summary["matched_count"] == 1
    item = [r for r in outcome.results if r["status"] == "matched"][0]
    assert item["match_method"] == "amount_date_description"
    assert item["date_difference_days"] == 1
    assert "explanation" in item and item["explanation"]


def test_date_tolerance_boundary():
    bank = frame([{"date": "2026-04-07", "amount": "20.00", "reference": "R-D", "type": "debit"}])
    ledger_ok = frame([{"date": "2026-04-10", "amount": "20.00", "reference": "R-D", "type": "debit"}])
    ledger_bad = frame([{"date": "2026-04-11", "amount": "20.00", "reference": "R-D", "type": "debit"}])
    within = reconcile_frames([("b.csv", bank)], [("l.csv", ledger_ok)], date_tolerance_days=3)
    outside = reconcile_frames([("b.csv", bank)], [("l.csv", ledger_bad)], date_tolerance_days=3)
    assert any(r["status"] == "matched" for r in within.results)
    assert any(r["status"] == "date_mismatch" for r in outside.results)


def test_amount_tolerance_allows_reference_match():
    bank = frame([{"date": "2026-04-01", "amount": "100.00", "reference": "R-TOL", "type": "debit"}])
    ledger = frame([{"date": "2026-04-01", "amount": "100.40", "reference": "R-TOL", "type": "debit"}])
    outcome = reconcile_frames([("b.csv", bank)], [("l.csv", ledger)], amount_tolerance=0.50)
    matched = [r for r in outcome.results if r["status"] == "matched"]
    assert len(matched) == 1
    assert matched[0]["match_method"] in {"reference_amount_tolerance", "exact_reference_amount"}


def test_debit_credit_normalization_and_direction_not_cross_matched():
    bank = frame([
        {"txn_date": "2026-04-08", "withdrawal": "40.00", "deposit": "", "utr": "S-1"},
        {"txn_date": "2026-04-08", "withdrawal": "", "deposit": "80.00", "utr": "S-2"},
    ])
    ledger = frame([
        {"transaction_date": "2026-04-08", "debit": "40.00", "credit": "", "reference": "S-1"},
        {"transaction_date": "2026-04-08", "debit": "", "credit": "80.00", "reference": "S-2"},
    ])
    outcome = reconcile_frames([("bank.csv", bank)], [("ledger.csv", ledger)])
    assert outcome.summary["matched_count"] == 2
    assert {r["bank_direction"] for r in outcome.results if r["status"] == "matched"} == {"debit", "credit"}

    cross_bank = frame([{"date": "2026-04-04", "type": "debit", "amount": "75.00"}])
    cross_ledger = frame([{"date": "2026-04-04", "type": "credit", "amount": "75.00"}])
    cross = reconcile_frames([("b.csv", cross_bank)], [("l.csv", cross_ledger)])
    assert cross.summary["matched_count"] == 0
    assert {r["status"] for r in cross.results} == {"bank_only", "ledger_only"}


def test_different_csv_column_names_supported():
    bank = frame([{"Txn Date": "2026-05-01", "Narration": "Coffee", "Withdrawal": "12.50", "UTR": "U1"}])
    ledger = frame([{"transaction_date": "2026-05-01", "details": "Coffee shop", "debit": "12.50", "reference": "U1"}])
    outcome = reconcile_frames([("bank.csv", bank)], [("ledger.csv", ledger)])
    assert outcome.summary["matched_count"] == 1


def test_duplicate_rows_detected_and_preserved():
    bank = frame([
        {"date": "2026-04-05", "amount": "50.00", "reference": "DUP", "description": "Same", "type": "debit"},
        {"date": "2026-04-05", "amount": "50.00", "reference": "DUP", "description": "Same", "type": "debit"},
    ])
    ledger = frame([{"date": "2026-04-05", "amount": "50.00", "reference": "DUP", "description": "Same", "type": "debit"}])
    outcome = reconcile_frames([("bank.csv", bank)], [("ledger.csv", ledger)])
    assert outcome.summary.get("duplicate_candidate_count", 0) >= 1
    assert any(r["status"] == "duplicate_candidate" for r in outcome.results)
    dup = [r for r in outcome.results if r["status"] == "duplicate_candidate"][0]
    assert dup["bank_source_file"] == "bank.csv"
    assert dup["bank_source_row"] in {1, 2}


def test_ambiguous_matches_require_human_review():
    bank = frame([
        {"date": "2026-04-05", "type": "debit", "amount": "500.00", "description": "Transfer A"},
        {"date": "2026-04-05", "type": "debit", "amount": "500.00", "description": "Transfer B"},
    ])
    ledger = frame([{"date": "2026-04-05", "type": "debit", "amount": "500.00", "description": "Transfer X"}])
    outcome = reconcile_frames([("bank.csv", bank)], [("ledger.csv", ledger)])
    assert outcome.summary["matched_count"] == 0
    assert outcome.summary["ambiguous_count"] >= 1
    assert all(r["status"] != "matched" for r in outcome.results)
    ambiguous = [r for r in outcome.results if r["status"] == "ambiguous"]
    assert ambiguous
    assert all("human review" in (r.get("explanation") or r.get("note") or "").lower() or r.get("review_status") == "pending" for r in ambiguous)


def test_one_to_one_matching_constraints_in_engine():
    bank = frame([{"date": "2026-04-06", "amount": "30.00", "reference": "ONE", "type": "debit"}])
    ledger = frame([
        {"date": "2026-04-06", "amount": "30.00", "reference": "ONE", "type": "debit"},
        {"date": "2026-04-06", "amount": "30.00", "reference": "ONE-B", "type": "debit"},
    ])
    outcome = reconcile_frames([("b.csv", bank)], [("l.csv", ledger)])
    # same reference pair matched once; extra ledger remains unmatched
    matched = [r for r in outcome.results if r["status"] == "matched"]
    assert len(matched) == 1
    assert outcome.summary["ledger_only_count"] + outcome.summary["ambiguous_count"] >= 1


def test_invalid_rows_and_empty_rows_preserved():
    bank = frame([
        {"date": "2026-04-09", "amount": "not-a-number", "reference": "BAD"},
        {"date": "", "amount": "", "reference": ""},
        {"date": "2026-04-09", "amount": "10.00", "reference": "OK", "type": "debit"},
    ])
    ledger = frame([{"date": "2026-04-09", "amount": "10.00", "reference": "OK", "type": "debit"}])
    outcome = reconcile_frames([("bank.csv", bank)], [("ledger.csv", ledger)])
    assert outcome.summary.get("invalid_record_count", 0) >= 1
    invalid = [r for r in outcome.results if r["status"] == "invalid_records"]
    assert invalid
    assert all(r.get("bank_source_file") == "bank.csv" or r.get("ledger_source_file") == "ledger.csv" for r in invalid)
    assert any(r.get("bank_source_row") for r in invalid)
    assert outcome.summary["matched_count"] == 1


def test_empty_or_structurally_invalid_files_raise_clear_validation_error():
    try:
        reconcile_frames([("bank.csv", frame([], columns=[]))], [("ledger.csv", frame([{"date": "2026-04-09", "amount": "10.00"}]))])
        assert False, "expected ReconciliationError"
    except ReconciliationError as exc:
        assert exc.errors

    try:
        reconcile_frames([("bank.csv", frame([{"amount": "10.00"}]))], [("ledger.csv", frame([{"date": "2026-04-09", "amount": "10.00"}]))])
        assert False, "expected ReconciliationError"
    except ReconciliationError as exc:
        assert any("Missing required columns" in e or "date" in e.lower() for e in exc.errors)


def test_api_preview_and_create_persist_run_history_and_report_totals():
    bank_csv = "date,type,amount,reference,description\n2026-04-13,debit,100.00,REF-API-1,Office\n2026-04-14,credit,50.00,REF-API-2,Client\n"
    ledger_csv = "date,type,amount,reference,description\n2026-04-13,debit,100.00,REF-API-1,Office\n2026-04-14,credit,45.00,REF-API-2,Client\n2026-04-15,debit,25.00,REF-API-3,Supplies\n"

    preview = client.post(
        "/api/reconciliations/preview",
        data={"date_tolerance_days": "3", "amount_tolerance": "0.01"},
        files={"bank_file": _csv_upload("bank-api.csv", bank_csv), "ledger_file": _csv_upload("ledger-api.csv", ledger_csv)},
    )
    assert preview.status_code == 200, preview.text
    pdata = preview.json()
    assert "proposed_summary" in pdata
    assert "preview" in pdata

    response = client.post(
        "/api/reconciliations",
        data={"date_tolerance_days": "3", "amount_tolerance": "0.01"},
        files={"bank_file": _csv_upload("bank-api.csv", bank_csv), "ledger_file": _csv_upload("ledger-api.csv", ledger_csv)},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    run_id = data["reconciliation_id"]
    assert data["summary"]["matched_count"] >= 1
    assert data["summary"]["amount_mismatch_count"] >= 1
    assert data["summary"]["ledger_only_count"] >= 1
    assert data["summary"]["reconciliation_difference"] == data["summary"]["difference"]
    assert "unreconciled_value" in data["summary"]
    assert "match_rate" in data["summary"]
    assert all("explanation" in r and "match_method" in r for r in data["results"])

    detail = client.get(f"/api/reconciliations/{run_id}")
    assert detail.status_code == 200
    d = detail.json()
    assert d["run_id"] == run_id
    assert "review_actions" in d
    assert d["summary"]["bank_record_count"] >= 2
    assert d["summary"]["ledger_record_count"] >= 3

    listing = client.get("/api/reconciliations")
    assert listing.status_code == 200
    assert any(item.get("id") == run_id or item.get("reconciliation_id") == run_id for item in listing.json())

    exported = client.get(f"/api/reconciliations/{run_id}/export.csv")
    assert exported.status_code == 200
    assert "REF-API-1" in exported.text

    # history/report totals use persisted run
    report = client.get("/api/reports/summary")
    assert report.status_code == 200
    rdata = report.json()
    assert rdata["reconciliation"] is not None
    assert rdata["reconciliation"]["summary"]["difference"] == data["summary"]["difference"]


def test_manual_confirmation_rejected_matches_and_one_to_one_constraints():
    bank_csv = "date,type,amount,reference,description\n2026-06-01,debit,100.00,AMB-1,One\n2026-06-01,debit,100.00,AMB-2,Two\n"
    ledger_csv = "date,type,amount,reference,description\n2026-06-01,debit,100.00,AMB-X,One ledger\n2026-06-01,debit,100.00,AMB-Y,Two ledger\n"
    run = client.post(
        "/api/reconciliations",
        data={"date_tolerance_days": "0", "amount_tolerance": "0.00"},
        files={"bank_file": _csv_upload("b.csv", bank_csv), "ledger_file": _csv_upload("l.csv", ledger_csv)},
    )
    assert run.status_code == 200, run.text
    payload = run.json()
    run_id = payload["reconciliation_id"]
    # manual link by picking one bank and one ledger candidate
    bank_candidates = [r for r in payload["results"] if r.get("bank_source_file") and not r.get("ledger_source_file")]
    ledger_candidates = [r for r in payload["results"] if r.get("ledger_source_file") and not r.get("bank_source_file")]
    assert bank_candidates and ledger_candidates

    link = client.post(
        f"/api/reconciliations/{run_id}/manual-link",
        json={"reviewer": "tester", "bank_result_id": bank_candidates[0]["id"], "ledger_result_id": ledger_candidates[0]["id"], "note": "verified"},
    )
    assert link.status_code == 200, link.text
    linked = link.json()["result"]
    assert linked["status"] == "matched"
    assert linked["review_status"] == "confirmed"
    assert linked["is_confirmed_match"] is True
    assert linked["match_method"] == "manual_link"

    # one-to-one constraint: same bank record cannot be confirmed in another manual link
    conflict = client.post(
        f"/api/reconciliations/{run_id}/manual-link",
        json={"reviewer": "tester", "bank_result_id": bank_candidates[0]["id"], "ledger_result_id": ledger_candidates[1]["id"]},
    )
    assert conflict.status_code in {400, 409}

    # reject a suggested/exception result
    reject_target = None
    refreshed = client.get(f"/api/reconciliations/{run_id}").json()
    for r in refreshed["results"]:
        if r["status"] in {"ambiguous", "amount_mismatch", "date_mismatch"} and r.get("bank_source_file") and r.get("ledger_source_file"):
            reject_target = r
            break
    if reject_target is None:
        # paired not available; reject any non-confirmed ambiguous single-sided? use confirm endpoint on manual link created pair rejected flow not applicable, find another
        reject_target = next((r for r in refreshed["results"] if r["status"] in {"ambiguous", "amount_mismatch", "date_mismatch"} and r["id"] != linked["id"]), None)
    if reject_target:
        rej = client.post(f"/api/reconciliations/results/{reject_target['id']}/confirm", json={"decision": "rejected", "reviewer": "tester"})
        assert rej.status_code == 200
        assert rej.json()["review_status"] == "rejected"

    # review action history recorded
    detail = client.get(f"/api/reconciliations/{run_id}").json()
    assert len(detail.get("review_actions", [])) >= 1


def test_reconciliation_history_preserves_previous_runs():
    def run_once(ref):
        bank_csv = f"date,type,amount,reference\n2026-07-01,debit,10.00,{ref}\n"
        ledger_csv = f"date,type,amount,reference\n2026-07-01,debit,10.00,{ref}\n"
        resp = client.post(
            "/api/reconciliations",
            data={"date_tolerance_days": "0", "amount_tolerance": "0"},
            files={"bank_file": _csv_upload(f"b-{ref}.csv", bank_csv), "ledger_file": _csv_upload(f"l-{ref}.csv", ledger_csv)},
        )
        assert resp.status_code == 200
        return resp.json()["reconciliation_id"]

    id1 = run_once("H1")
    id2 = run_once("H2")
    listing = client.get("/api/reconciliations").json()
    ids = {item.get("id") or item.get("reconciliation_id") for item in listing}
    assert id1 in ids and id2 in ids
    r1 = client.get(f"/api/reconciliations/{id1}")
    r2 = client.get(f"/api/reconciliations/{id2}")
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["summary"]["matched_count"] == 1
    assert r2.json()["summary"]["matched_count"] == 1


# ---------------------------------------------------------------------------
# Controlled reconciliation fixtures with pre-defined expected results.
#
# Each fixture below states the expected outcome before implementation changes.
# The amounts/references/dates are synthetic and only live in isolated DataFrames
# (and, for API runs, the temporary SQLite database created in conftest.py).
# ---------------------------------------------------------------------------


def _row(reference="", amount="100.00", day="2026-08-01", side="debit", description="Vendor A"):
    return {
        "date": day,
        "amount": amount,
        "reference": reference,
        "description": description,
        "type": side,
    }


def _run(bank_rows, ledger_rows, *, date_tolerance=1, amount_tolerance=0.50):
    return reconcile_frames(
        [("fixture-bank.csv", pd.DataFrame(bank_rows))],
        [("fixture-ledger.csv", pd.DataFrame(ledger_rows))],
        date_tolerance_days=date_tolerance,
        amount_tolerance=amount_tolerance,
    )


def test_fixture_same_reference_same_amount_is_matched():
    """Expected: one matched pair, exact-reference method, no exception.

    This is the positive control: amount validation must not prevent a genuine
    exact match. Auto-matched rows are not human-confirmed (is_confirmed_match=0).
    """
    outcome = _run([_row("FIX-1", "100.00")], [_row("FIX-1", "100.00")])
    assert outcome.summary["matched_count"] == 1
    assert outcome.summary["amount_mismatch_count"] == 0
    item = outcome.results[0]
    assert item["status"] == "matched"
    assert item["match_method"] == "exact_reference_amount"
    assert item["amount_difference"] == 0
    assert item["is_confirmed_match"] == 0
    assert item["review_status"] == "pending"


def test_fixture_same_reference_amount_within_tolerance_is_matched():
    """Expected: reference match accepted only because 0.40 <= 0.50 tolerance.

    The reason must state the observed amount difference and the configured
    tolerance so a reviewer can tell why the pair was confirmed automatically.
    """
    outcome = _run([_row("FIX-2", "100.00")], [_row("FIX-2", "100.40")], amount_tolerance=0.50)
    assert outcome.summary["matched_count"] == 1
    item = outcome.results[0]
    assert item["status"] == "matched"
    assert item["match_method"] == "reference_amount_tolerance"
    assert abs(item["amount_difference"]) == 0.4
    assert "tolerance" in item["explanation"].lower()
    assert "0.40" in item["explanation"]


def test_fixture_same_reference_amount_outside_tolerance_is_never_matched():
    """Expected: 50.00 difference with 0.50 tolerance is an amount mismatch.

    This is the primary regression fixture: a matching reference must not turn
    into a confirmed financial match when amounts differ beyond tolerance.
    The pair is still preserved as an exception with a meaningful reason.
    """
    outcome = _run([_row("FIX-3", "100.00")], [_row("FIX-3", "150.00")], amount_tolerance=0.50)
    assert outcome.summary["matched_count"] == 0
    assert outcome.summary["amount_mismatch_count"] == 1
    assert len(outcome.results) == 1
    item = outcome.results[0]
    assert item["status"] == "amount_mismatch"
    assert item["is_confirmed_match"] == 0
    assert item["review_status"] == "pending"
    assert item["match_method"] == "reference_amount_mismatch"
    assert item["amount_difference"] == -50.0
    explanation = item["explanation"].lower()
    assert "tolerance" in explanation
    assert "50.00" in explanation
    assert "not confirmed" in explanation


def test_fixture_same_reference_direction_conflict_is_not_an_amount_mismatch():
    """Expected: equal amounts with opposing directions are not a matched pair.

    The exception must be labelled as a direction problem, not falsely reported
    as an amount mismatch (amount difference is zero).
    """
    outcome = _run(
        [_row("FIX-DIR", "100.00", side="debit")],
        [_row("FIX-DIR", "100.00", side="credit")],
    )
    assert outcome.summary["matched_count"] == 0
    item = outcome.results[0]
    assert item["status"] == "amount_mismatch"
    assert item["match_method"] == "reference_direction_mismatch"
    assert item["amount_difference"] == 0
    assert "direction" in item["explanation"].lower()
    assert "not confirmed" in item["explanation"].lower()


def test_fixture_different_reference_same_amount_and_date_is_not_confirmed():
    """Expected: conflicting references prevent an automatic match.

    Business rule preserved: even with identical amount/date/description, two
    different non-empty references are kept as separate unmatched records.
    """
    outcome = _run([_row("FIX-BANK", "250.00")], [_row("FIX-LEDGER", "250.00")])
    assert outcome.summary["matched_count"] == 0
    assert outcome.summary["bank_only_count"] == 1
    assert outcome.summary["ledger_only_count"] == 1
    assert {item["status"] for item in outcome.results} == {"bank_only", "ledger_only"}
    assert all(not item["is_confirmed_match"] for item in outcome.results)


def test_fixture_missing_reference_is_handled_safely():
    """Expected: blank references never create a reference match.

    (a) identical amount/date/description without references may be matched by
    the existing amount/date/description fallback;
    (b) a different amount without references must stay unmatched, even when it
    is inside the configured tolerance (the fallback requires equal amounts);
    (c) one blank and one populated reference must not be treated as a ref match.
    """
    matched = _run([_row("", "80.00")], [_row("", "80.00")])
    assert matched.summary["matched_count"] == 1
    assert matched.results[0]["match_method"] == "amount_date_description"

    different_amount = _run([_row("", "80.00")], [_row("", "80.40")], amount_tolerance=0.50)
    assert different_amount.summary["matched_count"] == 0
    assert {item["status"] for item in different_amount.results} == {"bank_only", "ledger_only"}

    one_reference = _run([_row("", "80.00")], [_row("FIX-ONLY-LEDGER", "80.00")])
    assert one_reference.summary["matched_count"] == 1
    assert one_reference.results[0]["match_method"] == "amount_date_description"


def test_fixture_duplicate_payment_is_preserved_and_not_double_matched():
    """Expected: an exact duplicate bank row is preserved as an exception.

    The first copy may match the single ledger row; the duplicate copy remains
    a duplicate_candidate and is never used in a second confirmed match.
    """
    bank = [
        _row("FIX-DUP", "75.00", description="Duplicate vendor"),
        _row("FIX-DUP", "75.00", description="Duplicate vendor"),
    ]
    outcome = _run(bank, [_row("FIX-DUP", "75.00", description="Duplicate vendor")])
    assert outcome.summary["matched_count"] == 1
    assert outcome.summary["duplicate_candidate_count"] == 1
    assert outcome.summary["bank_record_count"] == 2
    duplicates = [item for item in outcome.results if item["status"] == "duplicate_candidate"]
    assert len(duplicates) == 1
    assert duplicates[0]["bank_source_file"] == "fixture-bank.csv"
    assert duplicates[0]["is_confirmed_match"] == 0
    matched_rows = [
        (item["bank_source_row"], item["ledger_source_row"])
        for item in outcome.results
        if item["status"] == "matched"
    ]
    assert matched_rows == [(1, 1)]


def test_fixture_ambiguous_candidate_requires_human_review():
    """Expected: one bank row with two identical ledger candidates is ambiguous.

    No automatic match may consume either candidate; all rows stay pending
    review with is_confirmed_match=0.
    """
    outcome = _run(
        [_row("", "200.00", description="Transfer")],
        [
            _row("", "200.00", description="Transfer ledger one"),
            _row("", "200.00", description="Transfer ledger two"),
        ],
    )
    assert outcome.summary["matched_count"] == 0
    assert outcome.summary["ambiguous_count"] == 3
    assert all(item["status"] == "ambiguous" for item in outcome.results)
    assert all(item["review_status"] == "pending" for item in outcome.results)
    assert all(not item["is_confirmed_match"] for item in outcome.results)


def test_fixture_multiple_possible_matches_are_never_confirmed():
    """Expected: a 2x2 candidate grid produces four ambiguous results.

    Every source row appears exactly once; no transaction is reused across
    matches and no pair is auto-confirmed.
    """
    outcome = _run(
        [
            _row("", "300.00", description="Vendor payment A"),
            _row("", "300.00", description="Vendor payment B"),
        ],
        [
            _row("", "300.00", description="Vendor payment X"),
            _row("", "300.00", description="Vendor payment Y"),
        ],
    )
    assert outcome.summary["matched_count"] == 0
    assert outcome.summary["ambiguous_count"] == 4
    assert len(outcome.results) == 4
    bank_rows = [item["bank_source_row"] for item in outcome.results if item.get("bank_source_row")]
    ledger_rows = [item["ledger_source_row"] for item in outcome.results if item.get("ledger_source_row")]
    assert sorted(bank_rows) == [1, 2]
    assert sorted(ledger_rows) == [1, 2]
    assert all(not item["is_confirmed_match"] for item in outcome.results)


def test_fixture_invalid_transaction_data_is_preserved_without_matching():
    """Expected: malformed/invalid rows are reported and never silently matched.

    Bad amount, blank date, non-finite amount and an empty row are preserved as
    invalid_records; the one good row still matches its ledger counterpart.
    """
    bank = [
        {"date": "2026-08-01", "amount": "not-a-number", "reference": "BAD-AMT", "type": "debit"},
        {"date": "", "amount": "10.00", "reference": "BAD-DATE", "type": "debit"},
        {"date": "2026-08-01", "amount": "inf", "reference": "BAD-INF", "type": "debit"},
        {"date": "", "amount": "", "reference": "", "type": ""},
        _row("FIX-VALID", "10.00"),
    ]
    outcome = _run(bank, [_row("FIX-VALID", "10.00")])
    assert outcome.summary["invalid_record_count"] == 4
    assert outcome.summary["matched_count"] == 1
    invalid = [item for item in outcome.results if item["status"] == "invalid_records"]
    assert len(invalid) == 4
    assert all(item["issues"] if "issues" in item else item["explanation"] for item in invalid)
    assert any("Invalid amount" in item["explanation"] for item in invalid)
    assert any("date" in item["explanation"].lower() for item in invalid)


def test_fixture_repeated_runs_are_deterministic_and_history_is_preserved():
    """Expected: identical inputs produce identical results on repeated runs.

    API runs create new persisted history entries and do not rewrite the first
    run's results.
    """
    bank = pd.DataFrame([_row("FIX-REPEAT", "100.00"), _row("FIX-REPEAT-2", "45.00")])
    ledger = pd.DataFrame([_row("FIX-REPEAT", "100.00"), _row("FIX-REPEAT-2", "90.00")])
    first = reconcile_frames([("b.csv", bank)], [("l.csv", ledger)], amount_tolerance=0.50)
    second = reconcile_frames([("b.csv", bank.copy())], [("l.csv", ledger.copy())], amount_tolerance=0.50)
    assert first.results == second.results
    assert first.summary == second.summary

    bank_csv = (
        "date,amount,reference,description,type\n"
        "2026-08-01,100.00,FIX-REPEAT,One,debit\n"
    )
    ledger_csv = (
        "date,amount,reference,description,type\n"
        "2026-08-01,150.00,FIX-REPEAT,One,debit\n"
    )
    ids = []
    for _ in range(2):
        response = client.post(
            "/api/reconciliations",
            data={"date_tolerance_days": "1", "amount_tolerance": "0.50"},
            files={
                "bank_file": _csv_upload("repeat-bank.csv", bank_csv),
                "ledger_file": _csv_upload("repeat-ledger.csv", ledger_csv),
            },
        )
        assert response.status_code == 200, response.text
        ids.append(response.json()["reconciliation_id"])
    assert ids[0] != ids[1]
    old_detail = client.get(f"/api/reconciliations/{ids[0]}").json()
    new_detail = client.get(f"/api/reconciliations/{ids[1]}").json()
    assert [item["status"] for item in old_detail["results"]] == ["amount_mismatch"]
    assert [item["status"] for item in new_detail["results"]] == ["amount_mismatch"]
    assert old_detail["results"] != [] and new_detail["results"] != []
    listing_ids = {item.get("id") or item.get("reconciliation_id") for item in client.get("/api/reconciliations").json()}
    assert set(ids).issubset(listing_ids)


def test_fixture_amount_gate_blocks_even_a_faulty_match_classifier(monkeypatch):
    """Expected: the final validation gate rejects a 'matched' classifier result.

    This simulates a future/buggy classifier that treats any shared reference
    as a match. The outcome must still be an amount_mismatch, never confirmed.
    """
    import app.reconciliation as reconciliation

    original = reconciliation._pair_status

    def unsafe_pair_status(bank, ledger, date_tolerance_days, amount_tolerance):
        status, method, explanation = original(bank, ledger, date_tolerance_days, amount_tolerance)
        return "matched", method or "unsafe_reference_match", explanation or "reference matched"

    monkeypatch.setattr(reconciliation, "_pair_status", unsafe_pair_status)
    outcome = _run([_row("FIX-GATE", "100.00")], [_row("FIX-GATE", "999.00")])
    assert outcome.summary["matched_count"] == 0
    item = outcome.results[0]
    assert item["status"] == "amount_mismatch"
    assert item["match_method"] == "reference_amount_mismatch"
    assert "not confirmed" in item["explanation"].lower()


def test_fixture_manual_confirmation_of_an_amount_mismatch_still_works():
    """Expected: a human reviewer may still explicitly confirm an exception.

    Automatic matching never confirms an amount mismatch, but the existing
    review workflow remains available and records the reviewer's decision.
    """
    bank_csv = "date,amount,reference,type\n2026-08-01,100.00,FIX-MANUAL,debit\n"
    ledger_csv = "date,amount,reference,type\n2026-08-01,150.00,FIX-MANUAL,debit\n"
    run = client.post(
        "/api/reconciliations",
        data={"date_tolerance_days": "1", "amount_tolerance": "0.01"},
        files={"bank_file": _csv_upload("b.csv", bank_csv), "ledger_file": _csv_upload("l.csv", ledger_csv)},
    )
    assert run.status_code == 200, run.text
    payload = run.json()
    assert payload["summary"]["matched_count"] == 0
    mismatch = next(item for item in payload["results"] if item["status"] == "amount_mismatch")
    confirmed = client.post(
        f"/api/reconciliations/results/{mismatch['id']}/review",
        json={"action": "confirm_match", "reviewer": "senior-reviewer", "comment": "balance adjusted with approval"},
    )
    assert confirmed.status_code == 200, confirmed.text
    result = confirmed.json()["result"]
    assert result["status"] == "matched"
    assert result["is_confirmed_match"] is True
    assert result["review_status"] == "confirmed"
    # Reviewer identity comes from authenticated user, not client payload.
    assert result["reviewed_by"] == "test-admin"
    assert confirmed.json()["run_summary"]["matched_count"] == 1


def test_fixture_non_finite_amount_tolerance_cannot_disable_amount_validation():
    """Expected: NaN/Infinity tolerance is rejected, never used to bypass checks.

    An infinite tolerance would accept any amount difference for a matching
    reference; NaN makes comparison-based validation silently fail. Both must
    be treated as invalid configuration by the engine and by the API.
    """
    bank = pd.DataFrame([_row("FIX-TOL", "100.00")])
    ledger = pd.DataFrame([_row("FIX-TOL", "900.00")])
    for bad_tolerance in (float("nan"), float("inf"), float("-inf"), "nan", "inf"):
        try:
            reconcile_frames(
                [("b.csv", bank)], [("l.csv", ledger)], amount_tolerance=bad_tolerance
            )
            assert False, f"expected ReconciliationError for {bad_tolerance!r}"
        except ReconciliationError as exc:
            assert "tolerance" in str(exc).lower()

    for bad_tolerance in ("nan", "inf", "-inf"):
        response = client.post(
            "/api/reconciliations/preview",
            data={"date_tolerance_days": "1", "amount_tolerance": bad_tolerance},
            files={
                "bank_file": _csv_upload("b.csv", "date,amount,reference,type\n2026-08-01,100.00,A,debit\n"),
                "ledger_file": _csv_upload("l.csv", "date,amount,reference,type\n2026-08-01,100.00,A,debit\n"),
            },
        )
        assert response.status_code == 400, response.text
        assert math.isfinite(float(bad_tolerance)) is False
