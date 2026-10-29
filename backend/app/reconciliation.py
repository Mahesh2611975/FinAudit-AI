from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from .csv_normalizer import (
    _normalise_type,
    _parse_amount,
    _parse_date,
    _text,
    safe_filename,
)

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
)

AMOUNT_TOLERANCE = 0.005


class ReconciliationError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = list(errors)
        super().__init__(" ".join(self.errors))


@dataclass
class _Transaction:
    side: str
    source_file: str
    source_row: int
    date: date
    amount: float
    direction: str
    reference: str
    reference_key: str
    description: str


@dataclass
class ReconciliationOutcome:
    results: list[dict] = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    bank_files: list[str] = field(default_factory=list)
    ledger_files: list[str] = field(default_factory=list)
    date_tolerance_days: int = 3


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
    return number + 1


def _normalise_reference(value) -> str:
    return _text(value).casefold()


def _signature(item: _Transaction) -> tuple:
    return (
        item.date.isoformat(),
        round(item.amount, 2),
        item.direction,
        item.description.casefold(),
    )


def _parse_side(
    frames: list[tuple[str, pd.DataFrame]],
    side: str,
) -> tuple[list[_Transaction], list[str], list[str], list[str]]:
    rows: list[_Transaction] = []
    warnings: list[str] = []
    errors: list[str] = []
    files: list[str] = []

    if not frames:
        return rows, warnings, [f"Upload a {side} CSV file."], files

    for filename, df in frames:
        source_file = safe_filename(filename)
        files.append(source_file)

        if not isinstance(df, pd.DataFrame):
            errors.append(f"{source_file}: could not read CSV data.")
            continue

        columns = _column_map(df)
        date_column = _pick_column(columns, DATE_ALIASES)
        amount_column = _pick_column(columns, AMOUNT_ALIASES)
        split_columns = [
            (columns[alias], direction)
            for alias, direction in SPLIT_AMOUNT_ALIASES
            if alias in columns
        ]
        reference_columns = [
            columns[alias] for alias in REFERENCE_ALIASES if alias in columns
        ]
        description_columns = [
            columns[alias] for alias in DESCRIPTION_ALIASES if alias in columns
        ]
        type_columns = [columns[alias] for alias in TYPE_ALIASES if alias in columns]

        missing = []
        if date_column is None:
            missing.append("transaction date (accepted: " + ", ".join(DATE_ALIASES) + ")")
        if amount_column is None and not split_columns:
            missing.append(
                "amount (accepted: amount, debit, credit, withdrawal, deposit)"
            )
        if missing:
            errors.append(f"{source_file}: Missing required columns: " + "; ".join(missing))
            continue

        work = df.copy()
        if not work.empty:
            blank = work.apply(
                lambda row: all(_text(value) == "" for value in row.values),
                axis=1,
            )
            skipped = int(blank.sum())
            if skipped:
                warnings.append(f"{source_file}: skipped {skipped} empty row(s).")
            work = work.loc[~blank]

        if work.empty:
            errors.append(f"{source_file}: CSV contains no transaction rows.")
            continue

        references: dict[str, list[_Transaction]] = defaultdict(list)

        for position, (index, row) in enumerate(work.iterrows()):
            source_row = _source_row(index, position)
            raw_date = row.get(date_column, "")
            date_text = _parse_date(raw_date)
            if _text(raw_date) == "":
                errors.append(f"{source_file}, row {source_row}: missing date.")
                continue
            if date_text is None:
                errors.append(f"{source_file}, row {source_row}: invalid date.")
                continue
            try:
                parsed_date = date.fromisoformat(date_text)
            except ValueError:
                errors.append(f"{source_file}, row {source_row}: invalid date.")
                continue

            direction = "unknown"
            amount: float | None = None

            if amount_column is not None:
                raw_amount = row.get(amount_column, "")
                parsed_amount = _parse_amount(raw_amount)
                if _text(raw_amount) == "":
                    errors.append(f"{source_file}, row {source_row}: missing amount.")
                    continue
                if parsed_amount is None:
                    errors.append(f"{source_file}, row {source_row}: invalid amount.")
                    continue

                raw_type = _row_text(row, type_columns)
                _, direction = _normalise_type(raw_type)
                if direction == "unknown":
                    if parsed_amount < 0:
                        direction = "debit"
                    elif parsed_amount > 0:
                        direction = "credit"
                amount = abs(parsed_amount)
            else:
                candidates: list[tuple[str, float]] = []
                for column, split_direction in split_columns:
                    raw_value = row.get(column, "")
                    if _text(raw_value) == "":
                        continue
                    parsed_amount = _parse_amount(raw_value)
                    if parsed_amount is None:
                        errors.append(
                            f"{source_file}, row {source_row}: invalid amount in {column}."
                        )
                        continue
                    candidates.append((split_direction, parsed_amount))

                if not candidates:
                    errors.append(f"{source_file}, row {source_row}: missing amount.")
                    continue

                non_zero = [item for item in candidates if abs(item[1]) > 1e-9]
                usable = non_zero or candidates
                if len(usable) > 1:
                    errors.append(
                        f"{source_file}, row {source_row}: multiple debit/credit "
                        "amount columns contain values."
                    )
                    continue

                direction, parsed_amount = usable[0]
                amount = abs(parsed_amount)

            reference = _row_text(row, reference_columns)
            description = _row_text(row, description_columns)
            item = _Transaction(
                side=side,
                source_file=source_file,
                source_row=source_row,
                date=parsed_date,
                amount=float(amount),
                direction=direction,
                reference=reference,
                reference_key=_normalise_reference(reference),
                description=description,
            )
            rows.append(item)
            if item.reference_key:
                references[item.reference_key].append(item)

        for items in references.values():
            if len(items) < 2:
                continue
            if len({_signature(item) for item in items}) > 1:
                display = items[0].reference
                locations = ", ".join(
                    f"row {item.source_row}" for item in items[:5]
                )
                if len(items) > 5:
                    locations += ", ..."
                errors.append(
                    f"{source_file}: reference '{display}' refers to conflicting "
                    f"transactions ({locations})."
                )

    return rows, warnings, errors, files


def _result(
    bank: _Transaction | None,
    ledger: _Transaction | None,
    status: str,
    note: str,
) -> dict:
    bank_amount = round(bank.amount, 2) if bank else None
    ledger_amount = round(ledger.amount, 2) if ledger else None
    amount_difference = (
        round(bank_amount - ledger_amount, 2)
        if bank_amount is not None and ledger_amount is not None
        else None
    )
    date_difference_days = (
        abs((bank.date - ledger.date).days)
        if bank is not None and ledger is not None
        else None
    )
    return {
        "status": status,
        "bank_date": bank.date.isoformat() if bank else None,
        "bank_amount": bank_amount,
        "bank_direction": bank.direction if bank else None,
        "bank_reference": bank.reference if bank else None,
        "bank_description": bank.description if bank else None,
        "bank_source_file": bank.source_file if bank else None,
        "bank_source_row": bank.source_row if bank else None,
        "ledger_date": ledger.date.isoformat() if ledger else None,
        "ledger_amount": ledger_amount,
        "ledger_direction": ledger.direction if ledger else None,
        "ledger_reference": ledger.reference if ledger else None,
        "ledger_description": ledger.description if ledger else None,
        "ledger_source_file": ledger.source_file if ledger else None,
        "ledger_source_row": ledger.source_row if ledger else None,
        "amount_difference": amount_difference,
        "date_difference_days": date_difference_days,
        "note": note,
    }


def _reference_pair_status(
    bank: _Transaction,
    ledger: _Transaction,
    tolerance: int,
) -> tuple[str, str]:
    date_difference = abs((bank.date - ledger.date).days)
    amount_difference = abs(bank.amount - ledger.amount)
    direction_conflict = (
        bank.direction != ledger.direction
        and bank.direction != "unknown"
        and ledger.direction != "unknown"
    )

    if amount_difference > AMOUNT_TOLERANCE or direction_conflict:
        return (
            "amount_mismatch",
            "Reference matched but amount or direction differs.",
        )
    if date_difference > tolerance:
        return (
            "date_mismatch",
            f"Reference and amount matched but dates differ by {date_difference} day(s).",
        )
    return "matched", "Matched using transaction reference."


def _fallback_match(
    bank: _Transaction,
    ledger: _Transaction,
    tolerance: int,
) -> bool:
    if (
        bank.reference_key
        and ledger.reference_key
        and bank.reference_key != ledger.reference_key
    ):
        return False
    if bank.direction != ledger.direction:
        return False
    if abs(bank.amount - ledger.amount) > AMOUNT_TOLERANCE:
        return False
    return abs((bank.date - ledger.date).days) <= tolerance


def _sort_results(results: list[dict]) -> list[dict]:
    def key(item: dict):
        return (
            item.get("bank_source_file") or item.get("ledger_source_file") or "",
            item.get("bank_source_row") or item.get("ledger_source_row") or 0,
            item.get("ledger_source_file") or item.get("bank_source_file") or "",
            item.get("ledger_source_row") or item.get("bank_source_row") or 0,
        )

    return sorted(results, key=key)


def _summarise(
    bank_rows: list[_Transaction],
    ledger_rows: list[_Transaction],
    results: list[dict],
) -> dict:
    counts = {status: 0 for status in STATUSES}
    for item in results:
        counts[item["status"]] = counts.get(item["status"], 0) + 1

    bank_total = round(sum(item.amount for item in bank_rows), 2)
    ledger_total = round(sum(item.amount for item in ledger_rows), 2)
    matched_total = round(
        sum(
            item["bank_amount"]
            for item in results
            if item["status"] == "matched" and item["bank_amount"] is not None
        ),
        2,
    )
    bank_unmatched_total = round(
        sum(
            item["bank_amount"]
            for item in results
            if item["status"] != "matched" and item["bank_amount"] is not None
        ),
        2,
    )
    ledger_unmatched_total = round(
        sum(
            item["ledger_amount"]
            for item in results
            if item["status"] != "matched" and item["ledger_amount"] is not None
        ),
        2,
    )
    difference = round(bank_total - ledger_total, 2)

    summary = {
        "bank_total": bank_total,
        "ledger_total": ledger_total,
        "matched_total": matched_total,
        "bank_unmatched_total": bank_unmatched_total,
        "ledger_unmatched_total": ledger_unmatched_total,
        "unmatched_total": round(bank_unmatched_total + ledger_unmatched_total, 2),
        "difference": 0.0 if difference == 0 else difference,
        "result_count": len(results),
    }
    for status in STATUSES:
        summary[f"{status}_count"] = counts[status]
    return summary


def reconcile_frames(
    bank_frames: list[tuple[str, pd.DataFrame]],
    ledger_frames: list[tuple[str, pd.DataFrame]],
    date_tolerance_days: int = 3,
) -> ReconciliationOutcome:
    if (
        isinstance(date_tolerance_days, bool)
        or not isinstance(date_tolerance_days, int)
        or date_tolerance_days < 0
    ):
        raise ReconciliationError(["Date tolerance must be a non-negative whole number."])

    bank_rows, bank_warnings, bank_errors, bank_files = _parse_side(
        bank_frames, "bank statement"
    )
    ledger_rows, ledger_warnings, ledger_errors, ledger_files = _parse_side(
        ledger_frames, "accounting ledger"
    )

    errors = bank_errors + ledger_errors
    if errors:
        raise ReconciliationError(errors)

    results: list[dict] = []
    used_bank: set[int] = set()
    used_ledger: set[int] = set()

    bank_by_reference: dict[str, list[_Transaction]] = defaultdict(list)
    ledger_by_reference: dict[str, list[_Transaction]] = defaultdict(list)
    for item in bank_rows:
        if item.reference_key:
            bank_by_reference[item.reference_key].append(item)
    for item in ledger_rows:
        if item.reference_key:
            ledger_by_reference[item.reference_key].append(item)

    for reference in sorted(set(bank_by_reference) & set(ledger_by_reference)):
        bank_group = bank_by_reference[reference]
        ledger_group = ledger_by_reference[reference]

        if len(bank_group) == 1 and len(ledger_group) == 1:
            bank_item = bank_group[0]
            ledger_item = ledger_group[0]
            status, note = _reference_pair_status(
                bank_item, ledger_item, date_tolerance_days
            )
            results.append(_result(bank_item, ledger_item, status, note))
            used_bank.add(id(bank_item))
            used_ledger.add(id(ledger_item))
            continue

        display = bank_group[0].reference or ledger_group[0].reference
        note = (
            f"Reference '{display}' appears {len(bank_group)} time(s) in the bank "
            f"statement and {len(ledger_group)} time(s) in the ledger; no safe "
            "one-to-one match was made."
        )
        for item in bank_group:
            results.append(_result(item, None, "ambiguous", note))
            used_bank.add(id(item))
        for item in ledger_group:
            results.append(_result(None, item, "ambiguous", note))
            used_ledger.add(id(item))

    remaining_bank = [item for item in bank_rows if id(item) not in used_bank]
    remaining_ledger = [item for item in ledger_rows if id(item) not in used_ledger]

    adjacency: dict[tuple[str, int], set[tuple[str, int]]] = {
        ("bank", index): set() for index in range(len(remaining_bank))
    }
    adjacency.update(
        {("ledger", index): set() for index in range(len(remaining_ledger))}
    )

    for bank_index, bank_item in enumerate(remaining_bank):
        for ledger_index, ledger_item in enumerate(remaining_ledger):
            if _fallback_match(bank_item, ledger_item, date_tolerance_days):
                adjacency[("bank", bank_index)].add(("ledger", ledger_index))
                adjacency[("ledger", ledger_index)].add(("bank", bank_index))

    visited: set[tuple[str, int]] = set()
    matched_bank: set[int] = set()
    matched_ledger: set[int] = set()
    ambiguous_components: list[list[tuple[str, int]]] = []

    for node in adjacency:
        if node in visited or not adjacency[node]:
            continue
        component: list[tuple[str, int]] = []
        stack = [node]
        visited.add(node)
        while stack:
            current = stack.pop()
            component.append(current)
            for neighbour in adjacency[current]:
                if neighbour not in visited:
                    visited.add(neighbour)
                    stack.append(neighbour)

        if len(component) == 2:
            bank_node = next(item for item in component if item[0] == "bank")
            ledger_node = next(item for item in component if item[0] == "ledger")
            bank_index = bank_node[1]
            ledger_index = ledger_node[1]
            results.append(
                _result(
                    remaining_bank[bank_index],
                    remaining_ledger[ledger_index],
                    "matched",
                    "Matched using amount, direction and date tolerance.",
                )
            )
            matched_bank.add(bank_index)
            matched_ledger.add(ledger_index)
        else:
            ambiguous_components.append(component)

    for component in ambiguous_components:
        for side, index in component:
            note = (
                "Multiple candidate transactions share the same amount, direction "
                "and date range; no safe one-to-one match was made."
            )
            if side == "bank":
                results.append(_result(remaining_bank[index], None, "ambiguous", note))
            else:
                results.append(_result(None, remaining_ledger[index], "ambiguous", note))

    for index, item in enumerate(remaining_bank):
        if index not in matched_bank and not any(
            side == "bank" and position == index
            for component in ambiguous_components
            for side, position in component
        ):
            results.append(
                _result(item, None, "bank_only", "No matching ledger entry was found.")
            )

    for index, item in enumerate(remaining_ledger):
        if index not in matched_ledger and not any(
            side == "ledger" and position == index
            for component in ambiguous_components
            for side, position in component
        ):
            results.append(
                _result(None, item, "ledger_only", "No matching bank entry was found.")
            )

    results = _sort_results(results)
    summary = _summarise(bank_rows, ledger_rows, results)

    return ReconciliationOutcome(
        results=results,
        summary=summary,
        warnings=bank_warnings + ledger_warnings,
        bank_files=bank_files,
        ledger_files=ledger_files,
        date_tolerance_days=date_tolerance_days,
    )


def reconcile_files(
    bank_file: tuple[str, pd.DataFrame],
    ledger_file: tuple[str, pd.DataFrame],
    date_tolerance_days: int = 3,
) -> ReconciliationOutcome:
    return reconcile_frames(
        [bank_file],
        [ledger_file],
        date_tolerance_days=date_tolerance_days,
    )
