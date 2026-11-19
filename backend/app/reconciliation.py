from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pandas as pd

from .csv_normalizer import _normalise_type, _parse_amount, _parse_date, _text, safe_filename

DATE_ALIASES = ("date", "transaction_date", "txn_date")
AMOUNT_ALIASES = ("amount",)
SPLIT_AMOUNT_ALIASES = (
    ("debit", "debit"),
    ("withdrawal", "debit"),
    ("credit", "credit"),
    ("deposit", "credit"),
)
REFERENCE_ALIASES = ("reference", "ref", "upi_ref", "transaction_id", "utr")
DESCRIPTION_ALIASES = ("description", "narration", "details", "recipient", "vendor")
TYPE_ALIASES = (
    "type",
    "direction",
    "transaction_type",
    "txn_type",
    "dr_cr",
    "debit_credit",
)

STATUSES = (
    "matched",
    "bank_only",
    "ledger_only",
    "amount_mismatch",
    "date_mismatch",
    "ambiguous",
    "duplicate_candidate",
    "invalid_records",
)

MAPPING_KEYS = {"date", "amount", "debit", "credit", "withdrawal", "deposit", "reference", "description", "type", "direction"}

AMOUNT_EPSILON = 1e-9


class ReconciliationError(ValueError):
    def __init__(self, errors: list[str], preview: dict | None = None):
        self.errors = list(errors)
        self.preview = preview
        super().__init__(" ".join(self.errors))


@dataclass
class ParsedRecord:
    side: str
    source_file: str
    source_row: int
    original: dict[str, Any]
    date: date | None = None
    amount: float | None = None
    direction: str = "unknown"
    reference: str = ""
    reference_key: str = ""
    description: str = ""
    valid: bool = True
    duplicate_candidate: bool = False
    duplicate_reason: str | None = None
    issues: list[str] = field(default_factory=list)

    @property
    def date_text(self) -> str | None:
        return self.date.isoformat() if self.date else None

    @property
    def amount_value(self) -> float | None:
        return round(self.amount, 2) if self.amount is not None else None

    def identity(self) -> tuple[str, str, int]:
        return (self.side, self.source_file, self.source_row)


@dataclass
class ReconciliationOutcome:
    results: list[dict] = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    bank_files: list[str] = field(default_factory=list)
    ledger_files: list[str] = field(default_factory=list)
    date_tolerance_days: int = 3
    amount_tolerance: float = 0.005
    preview: dict = field(default_factory=dict)


def _clean_column(name) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(name).strip().lower()).strip("_")


def _column_map(df: pd.DataFrame) -> dict[str, str]:
    columns: dict[str, str] = {}
    for column in df.columns:
        key = _clean_column(column)
        if key and key not in columns:
            columns[key] = column
    return columns


def _pick_column(columns: dict[str, str], aliases: tuple[str, ...]) -> str | None:
    for alias in aliases:
        if alias in columns:
            return columns[alias]
    return None


def _row_text(row: pd.Series, columns: list[str]) -> str:
    for column in columns:
        value = _text(row.get(column, ""))
        if value:
            return value
    return ""


def _source_row(index, position: int) -> int:
    try:
        number = int(index)
    except (TypeError, ValueError):
        return position + 1
    return number + 1 if number >= 0 else position + 1


def _normalise_reference(value) -> str:
    return _text(value).casefold()


def _normalise_description(value) -> str:
    text = _text(value).casefold()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _amount_close(left: float | None, right: float | None, tolerance: float) -> bool:
    if left is None or right is None:
        return False
    return abs(left - right) <= (tolerance + AMOUNT_EPSILON)


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _original_row(row: pd.Series) -> dict[str, Any]:
    return {str(key): _json_safe(val) for key, val in row.items()}


def parse_side_records(
    frames: list[tuple[str, pd.DataFrame]],
    side: str,
) -> tuple[list[ParsedRecord], list[ParsedRecord], list[ParsedRecord], list[str], list[str], list[str]]:
    valid: list[ParsedRecord] = []
    invalid: list[ParsedRecord] = []
    duplicates: list[ParsedRecord] = []
    warnings: list[str] = []
    errors: list[str] = []
    files: list[str] = []

    if not frames:
        errors.append(f"Upload a {side} CSV file.")
        return valid, invalid, duplicates, warnings, errors, files

    for filename, df in frames:
        source_file = safe_filename(filename)
        files.append(source_file)
        if not isinstance(df, pd.DataFrame):
            errors.append(f"{source_file}: could not read CSV data.")
            continue
        if df.empty and len(df.columns) == 0:
            errors.append(f"{source_file}: CSV is empty or structurally invalid.")
            continue

        columns = _column_map(df)
        date_column = _pick_column(columns, DATE_ALIASES)
        amount_column = _pick_column(columns, AMOUNT_ALIASES)
        split_columns = [(columns[alias], direction) for alias, direction in SPLIT_AMOUNT_ALIASES if alias in columns]
        reference_columns = [columns[alias] for alias in REFERENCE_ALIASES if alias in columns]
        description_columns = [columns[alias] for alias in DESCRIPTION_ALIASES if alias in columns]
        type_columns = [columns[alias] for alias in TYPE_ALIASES if alias in columns]

        missing = []
        if date_column is None:
            missing.append("transaction date")
        if amount_column is None and not split_columns:
            missing.append("amount or debit/credit columns")
        if missing:
            errors.append(
                f"{source_file}: Missing required columns for {side}: " + ", ".join(missing) + "."
            )
            continue

        work = df.copy()
        file_records: list[ParsedRecord] = []
        for position, (index, row) in enumerate(work.iterrows()):
            source_row = _source_row(index, position)
            original = _original_row(row)
            raw_values = [_text(value) for value in row.values]
            if all(value == "" for value in raw_values):
                warnings.append(f"{source_file}, row {source_row}: empty row preserved as invalid.")
                rec = ParsedRecord(
                    side=side,
                    source_file=source_file,
                    source_row=source_row,
                    original=original,
                    valid=False,
                    issues=["Empty row."],
                )
                invalid.append(rec)
                continue

            rec = ParsedRecord(
                side=side,
                source_file=source_file,
                source_row=source_row,
                original=original,
                reference=_row_text(row, reference_columns),
                description=_row_text(row, description_columns),
            )
            rec.reference_key = _normalise_reference(rec.reference)

            raw_date = row.get(date_column, "") if date_column else ""
            date_text = _parse_date(raw_date)
            if _text(raw_date) == "":
                rec.issues.append("Missing date.")
            elif date_text is None:
                rec.issues.append(f"Invalid date value '{_text(raw_date)}'.")
            else:
                try:
                    rec.date = date.fromisoformat(date_text)
                except ValueError:
                    rec.issues.append(f"Invalid date value '{_text(raw_date)}'.")

            if amount_column is not None:
                raw_amount = row.get(amount_column, "")
                parsed_amount = _parse_amount(raw_amount)
                raw_type = _row_text(row, type_columns)
                _, direction = _normalise_type(raw_type)
                if _text(raw_amount) == "":
                    rec.issues.append("Missing amount.")
                elif parsed_amount is None:
                    rec.issues.append(f"Invalid amount value '{_text(raw_amount)}'.")
                else:
                    if direction == "unknown":
                        direction = "debit" if parsed_amount < 0 else ("credit" if parsed_amount > 0 else "unknown")
                    rec.direction = direction
                    rec.amount = abs(float(parsed_amount))
            else:
                candidates: list[tuple[str, float, str]] = []
                for column, split_direction in split_columns:
                    raw_value = row.get(column, "")
                    if _text(raw_value) == "":
                        continue
                    parsed_amount = _parse_amount(raw_value)
                    if parsed_amount is None:
                        rec.issues.append(f"Invalid amount in column '{column}'.")
                        continue
                    candidates.append((split_direction, float(parsed_amount), column))
                non_zero = [item for item in candidates if abs(item[1]) > AMOUNT_EPSILON]
                usable = non_zero or candidates
                if not candidates and not any(issue.startswith("Invalid amount") for issue in rec.issues):
                    rec.issues.append("Missing amount (no debit/credit value provided).")
                elif len(usable) > 1:
                    rec.issues.append("Multiple debit/credit amount columns contain values.")
                elif usable:
                    rec.direction, raw_value, _ = usable[0]
                    rec.amount = abs(float(raw_value))

            if rec.issues:
                rec.valid = False
                invalid.append(rec)
            else:
                file_records.append(rec)
                valid.append(rec)

        exact_groups: dict[tuple, list[ParsedRecord]] = defaultdict(list)
        for rec in file_records:
            exact_groups[
                (
                    rec.date_text,
                    round(rec.amount or 0.0, 2),
                    rec.direction,
                    rec.reference_key,
                    _normalise_description(rec.description),
                )
            ].append(rec)
        for group in exact_groups.values():
            if len(group) < 2:
                continue
            for rec in group[1:]:
                rec.duplicate_candidate = True
                rec.duplicate_reason = "Exact duplicate row detected in the same source file."

    return valid, invalid, duplicates, warnings, errors, files


def _result_record(
    bank: ParsedRecord | None,
    ledger: ParsedRecord | None,
    status: str,
    match_method: str | None,
    explanation: str,
    review_status: str = "pending",
) -> dict:
    bank_amount = bank.amount_value if bank else None
    ledger_amount = ledger.amount_value if ledger else None
    amount_difference = (
        round(bank_amount - ledger_amount, 2)
        if bank_amount is not None and ledger_amount is not None
        else None
    )
    date_difference_days = (
        abs((bank.date - ledger.date).days)
        if bank and ledger and bank.date and ledger.date
        else None
    )
    return {
        "status": status,
        "match_method": match_method,
        "explanation": explanation,
        "note": explanation,
        "bank_date": bank.date_text if bank else None,
        "bank_amount": bank_amount,
        "bank_direction": bank.direction if bank else None,
        "bank_reference": bank.reference if bank else None,
        "bank_description": bank.description if bank else None,
        "bank_source_file": bank.source_file if bank else None,
        "bank_source_row": bank.source_row if bank else None,
        "ledger_date": ledger.date_text if ledger else None,
        "ledger_amount": ledger_amount,
        "ledger_direction": ledger.direction if ledger else None,
        "ledger_reference": ledger.reference if ledger else None,
        "ledger_description": ledger.description if ledger else None,
        "ledger_source_file": ledger.source_file if ledger else None,
        "ledger_source_row": ledger.source_row if ledger else None,
        "amount_difference": amount_difference,
        "date_difference_days": date_difference_days,
        "review_status": review_status,
        "reviewed_by": None,
        "reviewed_at": None,
        "reviewer_comment": None,
        "resolution_note": None,
        "is_confirmed_match": 0,
    }


def _invalid_result(record: ParsedRecord) -> dict:
    issues = "; ".join(record.issues) or "Invalid row."
    explanation = f"Invalid {record.side} record: {issues}"
    if str(record.side).startswith("bank"):
        result = _result_record(record, None, "invalid_records", "validation", explanation, review_status="open")
    else:
        result = _result_record(None, record, "invalid_records", "validation", explanation, review_status="open")
    return result


def _duplicate_result(record: ParsedRecord) -> dict:
    explanation = record.duplicate_reason or "Duplicate candidate detected."
    if str(record.side).startswith("bank"):
        result = _result_record(record, None, "duplicate_candidate", "duplicate_detection", explanation, review_status="pending")
    else:
        result = _result_record(None, record, "duplicate_candidate", "duplicate_detection", explanation, review_status="pending")
    return result


def _descriptions_compatible(bank: ParsedRecord, ledger: ParsedRecord) -> bool:
    b = _normalise_description(bank.description)
    l = _normalise_description(ledger.description)
    if not b or not l:
        return True
    if b == l or b in l or l in b:
        return True
    b_tokens = {token for token in b.split() if len(token) > 2}
    l_tokens = {token for token in l.split() if len(token) > 2}
    if not b_tokens or not l_tokens:
        return True
    overlap = b_tokens & l_tokens
    if overlap:
        return True
    # tolerate short alnum-like refs in descriptions if present
    return False


def _pair_status(bank: ParsedRecord, ledger: ParsedRecord, date_tolerance_days: int, amount_tolerance: float) -> tuple[str, str, str]:
    amount_diff = abs((bank.amount or 0.0) - (ledger.amount or 0.0))
    date_diff = abs((bank.date - ledger.date).days) if bank.date and ledger.date else 999999
    direction_conflict = bank.direction != ledger.direction and bank.direction != "unknown" and ledger.direction != "unknown"

    if bank.reference_key and ledger.reference_key and bank.reference_key == ledger.reference_key:
        if direction_conflict or amount_diff > (amount_tolerance + AMOUNT_EPSILON):
            return "amount_mismatch", "reference_amount_mismatch", "Reference matched but amount or direction differs."
        if amount_diff <= AMOUNT_EPSILON and date_diff <= date_tolerance_days:
            return "matched", "exact_reference_amount", "Matched using exact reference and exact amount."
        if amount_diff <= (amount_tolerance + AMOUNT_EPSILON) and date_diff <= date_tolerance_days:
            return "matched", "reference_amount_tolerance", "Matched using exact reference within configured amount tolerance."
        if amount_diff <= (amount_tolerance + AMOUNT_EPSILON):
            return "date_mismatch", "reference_date_mismatch", f"Reference and amount matched but dates differ by {date_diff} day(s) beyond tolerance."

    return "no_match", "", ""


def _summarise(bank_records: list[ParsedRecord], ledger_records: list[ParsedRecord], results: list[dict], date_tolerance_days: int, amount_tolerance: float) -> dict:
    counts = {status: 0 for status in STATUSES}
    for item in results:
        counts[item.get("status")] = counts.get(item.get("status"), 0) + 1

    bank_total = round(sum(float(item.amount or 0.0) for item in bank_records), 2)
    ledger_total = round(sum(float(item.amount or 0.0) for item in ledger_records), 2)
    matched_results = [item for item in results if item.get("status") == "matched" or item.get("is_confirmed_match")]
    # confirmed manual links treated as reconciled by is_confirmed_match; auto matched rows included
    reconciled_bank = sum(float(item.get("bank_amount") or 0.0) for item in matched_results if item.get("bank_amount") is not None)
    reconciled_ledger = sum(float(item.get("ledger_amount") or 0.0) for item in matched_results if item.get("ledger_amount") is not None)
    matched_total = round(reconciled_bank, 2)

    bank_unmatched_total = round(
        sum(float(item.get("bank_amount") or 0.0) for item in results if item.get("status") not in {"matched", "invalid_records", "duplicate_candidate"} and not item.get("is_confirmed_match") and item.get("bank_amount") is not None),
        2,
    )
    ledger_unmatched_total = round(
        sum(float(item.get("ledger_amount") or 0.0) for item in results if item.get("status") not in {"matched", "invalid_records", "duplicate_candidate"} and not item.get("is_confirmed_match") and item.get("ledger_amount") is not None),
        2,
    )
    difference = round(bank_total - ledger_total, 2)
    unreconciled_value = round(abs(bank_total - reconciled_bank) + abs(ledger_total - reconciled_ledger), 2)
    unmatched_count = counts.get("bank_only", 0) + counts.get("ledger_only", 0)
    exception_count = sum(counts.get(status, 0) for status in ("amount_mismatch", "date_mismatch", "ambiguous", "duplicate_candidate", "invalid_records"))

    summary = {
        "bank_record_count": len(bank_records),
        "ledger_record_count": len(ledger_records),
        "invalid_record_count": counts.get("invalid_records", 0),
        "duplicate_candidate_count": counts.get("duplicate_candidate", 0),
        "bank_total": bank_total,
        "ledger_total": ledger_total,
        "matched_total": matched_total,
        "bank_unmatched_total": bank_unmatched_total,
        "ledger_unmatched_total": ledger_unmatched_total,
        "unmatched_total": round(bank_unmatched_total + ledger_unmatched_total, 2),
        "unmatched_count": unmatched_count,
        "exception_count": exception_count,
        "unreconciled_value": unreconciled_value,
        "difference": 0.0 if abs(difference) < AMOUNT_EPSILON else difference,
        "reconciliation_difference": 0.0 if abs(difference) < AMOUNT_EPSILON else difference,
        "total_difference": 0.0 if abs(difference) < AMOUNT_EPSILON else difference,
        "result_count": len(results),
        "date_tolerance_days": date_tolerance_days,
        "amount_tolerance": amount_tolerance,
    }
    for status in STATUSES:
        summary[f"{status}_count"] = counts.get(status, 0)
    # backward-compatible aliases
    summary["match_rate"] = round((counts.get("matched", 0) / max(1, (len(bank_records) + len(ledger_records)) / 2)) * 100, 2)
    return summary


def reconcile_frames(
    bank_frames: list[tuple[str, pd.DataFrame]],
    ledger_frames: list[tuple[str, pd.DataFrame]],
    date_tolerance_days: int = 3,
    amount_tolerance: float = 0.005,
) -> ReconciliationOutcome:
    if isinstance(date_tolerance_days, bool) or not isinstance(date_tolerance_days, int) or date_tolerance_days < 0:
        raise ReconciliationError(["Date tolerance must be a non-negative whole number."])
    try:
        amount_tolerance = float(amount_tolerance)
    except Exception:
        raise ReconciliationError(["Amount tolerance must be a non-negative number."])
    if amount_tolerance < 0:
        raise ReconciliationError(["Amount tolerance must be a non-negative number."])

    bank_valid, bank_invalid, _, bank_warnings, bank_errors, bank_files = parse_side_records(bank_frames, "bank statement")
    ledger_valid, ledger_invalid, _, ledger_warnings, ledger_errors, ledger_files = parse_side_records(ledger_frames, "accounting ledger")

    structural_errors = bank_errors + ledger_errors
    if structural_errors:
        raise ReconciliationError(structural_errors)

    # do not abort for row-level invalids; preserve them
    results: list[dict] = []
    for rec in bank_invalid + ledger_invalid:
        results.append(_invalid_result(rec))

    # duplicate candidates are preserved as results but excluded from auto-matching pool
    bank_pool = [r for r in bank_valid if not r.duplicate_candidate]
    ledger_pool = [r for r in ledger_valid if not r.duplicate_candidate]
    for rec in [r for r in bank_valid if r.duplicate_candidate] + [r for r in ledger_valid if r.duplicate_candidate]:
        results.append(_duplicate_result(rec))

    used_bank: set[tuple[str, str, int]] = set()
    used_ledger: set[tuple[str, str, int]] = set()

    # 1) Exact reference + exact amount (or within amount tolerance for priority 2? separate priorities as requested)
    # Priority 1: exact reference + exact amount
    bank_by_ref = defaultdict(list)
    ledger_by_ref = defaultdict(list)
    for r in bank_pool:
        if r.reference_key:
            bank_by_ref[r.reference_key].append(r)
    for r in ledger_pool:
        if r.reference_key:
            ledger_by_ref[r.reference_key].append(r)

    def pop_pair(b: ParsedRecord, l: ParsedRecord):
        used_bank.add(b.identity())
        used_ledger.add(l.identity())

    # handle unique reference pairs first by priority logic
    for ref in sorted(set(bank_by_ref) & set(ledger_by_ref)):
        bgroup = [r for r in bank_by_ref[ref] if r.identity() not in used_bank]
        lgroup = [r for r in ledger_by_ref[ref] if r.identity() not in used_ledger]
        if not bgroup or not lgroup:
            continue
        if len(bgroup) == 1 and len(lgroup) == 1:
            b, l = bgroup[0], lgroup[0]
            status, method, explanation = _pair_status(b, l, date_tolerance_days, amount_tolerance)
            if status == "no_match":
                # exact reference but outside amount tolerance/date rules => mismatch/ambiguous by rule
                amount_diff = abs((b.amount or 0) - (l.amount or 0))
                if amount_diff > (amount_tolerance + AMOUNT_EPSILON) or (b.direction != l.direction and b.direction != 'unknown' and l.direction != 'unknown'):
                    status, method, explanation = "amount_mismatch", "reference_amount_mismatch", "Reference matched but amount or direction differs."
                else:
                    status, method, explanation = "date_mismatch", "reference_date_mismatch", "Reference and amount matched but dates differ beyond configured tolerance."
            results.append(_result_record(b, l, status, method, explanation))
            pop_pair(b, l)
            continue
        # non-unique reference groups: ambiguous unless we can find unique exact matches by amount/date inside group
        # try to pair exact amount+date unique combos to reduce ambiguity
        unmatched_b = bgroup[:]
        unmatched_l = lgroup[:]
        made = True
        while made:
            made = False
            for b in list(unmatched_b):
                candidates = [l for l in unmatched_l if _pair_status(b, l, date_tolerance_days, amount_tolerance)[0] == 'matched' and abs((b.amount or 0)-(l.amount or 0)) <= AMOUNT_EPSILON and abs((b.date-l.date).days) <= date_tolerance_days]
                # prefer true exact amount exact date
                candidates = [l for l in candidates if abs((b.amount or 0)-(l.amount or 0)) <= AMOUNT_EPSILON]
                if len(candidates) == 1:
                    l = candidates[0]
                    status, method, explanation = _pair_status(b, l, date_tolerance_days, amount_tolerance)
                    results.append(_result_record(b, l, status, method, explanation))
                    pop_pair(b, l)
                    unmatched_b.remove(b)
                    unmatched_l.remove(l)
                    made = True
                    break
        # remaining in conflicting/non-unique group -> ambiguous
        if unmatched_b or unmatched_l:
            display = ref
            explanation = (
                f"Reference '{display}' appears {len(bgroup)} time(s) in bank and {len(lgroup)} time(s) in ledger; "
                "one-to-one matching is ambiguous and requires human review."
            )
            for b in unmatched_b:
                if b.identity() not in used_bank:
                    results.append(_result_record(b, None, "ambiguous", "reference_ambiguous", explanation, review_status="pending"))
                    used_bank.add(b.identity())
            for l in unmatched_l:
                if l.identity() not in used_ledger:
                    results.append(_result_record(None, l, "ambiguous", "reference_ambiguous", explanation, review_status="pending"))
                    used_ledger.add(l.identity())

    remaining_bank = [r for r in bank_pool if r.identity() not in used_bank]
    remaining_ledger = [r for r in ledger_pool if r.identity() not in used_ledger]

    # 3) Exact amount + date within configured tolerance + compatible description/reference (and direction compatible)
    # Build candidate edges
    def compatible_pair(b: ParsedRecord, l: ParsedRecord) -> bool:
        if b.direction != l.direction:
            return False
        if not _amount_close(b.amount, l.amount, 0.0):
            return False
        if not b.date or not l.date or abs((b.date - l.date).days) > date_tolerance_days:
            return False
        # if both refs present and different, not compatible
        if b.reference_key and l.reference_key and b.reference_key != l.reference_key:
            return False
        return _descriptions_compatible(b, l)

    adjacency: dict[tuple[str, int], set[tuple[str, int]]] = {("bank", i): set() for i in range(len(remaining_bank))}
    adjacency.update({("ledger", i): set() for i in range(len(remaining_ledger))})
    for bi, b in enumerate(remaining_bank):
        for li, l in enumerate(remaining_ledger):
            if compatible_pair(b, l):
                adjacency[("bank", bi)].add(("ledger", li))
                adjacency[("ledger", li)].add(("bank", bi))

    visited = set()
    for node in list(adjacency.keys()):
        if node in visited or not adjacency[node]:
            continue
        stack = [node]
        component = []
        visited.add(node)
        while stack:
            cur = stack.pop()
            component.append(cur)
            for nb in adjacency[cur]:
                if nb not in visited:
                    visited.add(nb)
                    stack.append(nb)
        banks = [idx for side, idx in component if side == "bank"]
        ledgers = [idx for side, idx in component if side == "ledger"]
        if len(banks) == 1 and len(ledgers) == 1:
            b = remaining_bank[banks[0]]
            l = remaining_ledger[ledgers[0]]
            explanation = "Matched using exact amount, compatible direction/description and date within configured tolerance."
            results.append(_result_record(b, l, "matched", "amount_date_description", explanation))
            used_bank.add(b.identity())
            used_ledger.add(l.identity())
        else:
            explanation = "Multiple compatible candidate records found for amount/date/description matching; requires human review."
            for idx in banks:
                b = remaining_bank[idx]
                if b.identity() not in used_bank:
                    results.append(_result_record(b, None, "ambiguous", "amount_date_ambiguous", explanation, review_status="pending"))
                    used_bank.add(b.identity())
            for idx in ledgers:
                l = remaining_ledger[idx]
                if l.identity() not in used_ledger:
                    results.append(_result_record(None, l, "ambiguous", "amount_date_ambiguous", explanation, review_status="pending"))
                    used_ledger.add(l.identity())

    # leftovers
    for b in remaining_bank:
        if b.identity() not in used_bank:
            results.append(_result_record(b, None, "bank_only", "unmatched", "No matching ledger record was found."))
    for l in remaining_ledger:
        if l.identity() not in used_ledger:
            results.append(_result_record(None, l, "ledger_only", "unmatched", "No matching bank record was found."))

    # deterministic ordering
    def sort_key(item):
        return (
            str(item.get("status") or ""),
            str(item.get("bank_source_file") or item.get("ledger_source_file") or ""),
            int(item.get("bank_source_row") or item.get("ledger_source_row") or 0),
            str(item.get("ledger_source_file") or ""),
            int(item.get("ledger_source_row") or 0),
            int(item.get("id") or 0),
        )

    results = sorted(results, key=sort_key)

    all_bank_records = bank_valid + bank_invalid
    all_ledger_records = ledger_valid + ledger_invalid
    summary = _summarise(all_bank_records, all_ledger_records, results, date_tolerance_days, amount_tolerance)

    preview = {
        "bank": {
            "files": bank_files,
            "record_count": len(all_bank_records),
            "valid_count": len(bank_valid),
            "invalid_count": len(bank_invalid),
            "duplicate_candidate_count": len([r for r in bank_valid if r.duplicate_candidate]),
            "invalid_rows": [
                {
                    "side": r.side,
                    "source_file": r.source_file,
                    "source_row": r.source_row,
                    "issues": r.issues,
                    "original": r.original,
                }
                for r in bank_invalid
            ],
            "duplicate_rows": [
                {
                    "side": r.side,
                    "source_file": r.source_file,
                    "source_row": r.source_row,
                    "reason": r.duplicate_reason,
                    "date": r.date_text,
                    "amount": r.amount_value,
                    "reference": r.reference,
                    "description": r.description,
                }
                for r in bank_valid if r.duplicate_candidate
            ],
        },
        "ledger": {
            "files": ledger_files,
            "record_count": len(all_ledger_records),
            "valid_count": len(ledger_valid),
            "invalid_count": len(ledger_invalid),
            "duplicate_candidate_count": len([r for r in ledger_valid if r.duplicate_candidate]),
            "invalid_rows": [
                {
                    "side": r.side,
                    "source_file": r.source_file,
                    "source_row": r.source_row,
                    "issues": r.issues,
                    "original": r.original,
                }
                for r in ledger_invalid
            ],
            "duplicate_rows": [
                {
                    "side": r.side,
                    "source_file": r.source_file,
                    "source_row": r.source_row,
                    "reason": r.duplicate_reason,
                    "date": r.date_text,
                    "amount": r.amount_value,
                    "reference": r.reference,
                    "description": r.description,
                }
                for r in ledger_valid if r.duplicate_candidate
            ],
        },
        "warnings": bank_warnings + ledger_warnings,
    }

    return ReconciliationOutcome(
        results=results,
        summary=summary,
        warnings=bank_warnings + ledger_warnings,
        bank_files=bank_files,
        ledger_files=ledger_files,
        date_tolerance_days=date_tolerance_days,
        amount_tolerance=amount_tolerance,
        preview=preview,
    )


def reconcile_files(
    bank_file: tuple[str, pd.DataFrame],
    ledger_file: tuple[str, pd.DataFrame],
    date_tolerance_days: int = 3,
    amount_tolerance: float = 0.005,
) -> ReconciliationOutcome:
    return reconcile_frames([bank_file], [ledger_file], date_tolerance_days=date_tolerance_days, amount_tolerance=amount_tolerance)
