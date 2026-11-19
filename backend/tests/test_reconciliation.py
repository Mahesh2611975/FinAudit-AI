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
