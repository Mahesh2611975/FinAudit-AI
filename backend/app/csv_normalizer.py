from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field

import pandas as pd

REQUIRED_COLUMNS = {"date", "amount"}

SAMPLE_COLUMNS = {"transaction_id", "date", "vendor", "amount", "description"}
BANK_COLUMNS = {
    "date",
    "type",
    "amount",
    "account",
    "recipient",
    "upi_ref",
    "balance_after",
    "source_sms",
}

CANONICAL_COLUMNS = [
    "transaction_id",
    "date",
    "amount",
    "currency",
    "signed_amount",
    "type",
    "direction",
    "vendor",
    "description",
    "account",
    "upi_ref",
    "balance_after",
    "source_sms",
    "channel",
    "country",
    "state",
    "city",
    "latitude",
    "longitude",
    "source_file",
    "source_row",
    "duplicate_key",
]

CHANNEL_ALIASES = {
    "onlinebanking": "Online Banking",
    "internetbanking": "Online Banking",
    "netbanking": "Online Banking",
    "mobilebanking": "Mobile Banking",
    "mobileapp": "Mobile Banking",
    "bankingapp": "Mobile Banking",
    "atm": "ATM",
    "atmcash": "ATM",
    "cashwithdrawal": "ATM",
    "upi": "UPI",
    "neft": "NEFT",
    "rtgs": "RTGS",
    "imps": "IMPS",
    "wire": "Wire Transfer",
    "wiretransfer": "Wire Transfer",
    "swift": "Wire Transfer",
    "pos": "POS",
    "pointofsale": "POS",
    "cardpos": "POS",
}

CHANNEL_KEYWORDS = [
    ("Wire Transfer", ("wire transfer", "wire", "swift")),
    ("Online Banking", ("online banking", "internet banking", "net banking", "netbanking")),
    ("Mobile Banking", ("mobile banking", "mobile app", "banking app")),
    ("UPI", ("upi", "vpa", "gpay", "google pay", "phonepe", "paytm")),
    ("NEFT", ("neft",)),
    ("RTGS", ("rtgs",)),
    ("IMPS", ("imps",)),
    ("POS", ("pos", "point of sale", "card swipe")),
    ("ATM", ("atm", "cash withdrawal")),
]

CHANNEL_COLUMNS = ("channel", "payment_channel", "transaction_channel", "payment_mode", "mode")
CURRENCY_COLUMNS = ("currency", "currency_code", "ccy")
LOCATION_COLUMNS = {
    "country": ("country", "country_name"),
    "state": ("state", "state_name", "province", "region"),
    "city": ("city", "city_name", "town", "location_city"),
    "latitude": ("latitude", "lat"),
    "longitude": ("longitude", "lng", "lon", "long"),
}

CREDIT_TYPES = {
    "credit",
    "cr",
    "deposit",
    "inflow",
    "received",
    "receipt",
    "income",
    "refund",
}

DEBIT_TYPES = {
    "debit",
    "dr",
    "withdrawal",
    "withdraw",
    "outflow",
    "payment",
    "paid",
    "expense",
    "spend",
}


@dataclass
class NormalizationResult:
    frame: pd.DataFrame
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    schema: str = "unknown"
    files: list[str] = field(default_factory=list)


def is_normalized(df: pd.DataFrame) -> bool:
    required = {"transaction_id", "date", "amount", "direction", "source_file", "duplicate_key"}
    return required.issubset(set(df.columns))


def safe_filename(filename: str | None) -> str:
    base = re.split(r"[\\/]", str(filename or ""))[-1].strip()
    return (base or "upload.csv")[:255]


def detect_schema(columns: list[str]) -> str:
    cols = set(columns)
    if "date" in cols and "amount" in cols and cols & (BANK_COLUMNS - {"date", "amount"}):
        return "bank"
    if cols & SAMPLE_COLUMNS:
        return "sample"
    return "generic"


def _text(value) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "nat", "<na>"} else text


def _is_blank(value) -> bool:
    return _text(value) == ""


def _first_value(row, names: tuple[str, ...]) -> str:
    for name in names:
        value = _text(row.get(name, ""))
        if value:
            return value
    return ""


def _currency_value(row) -> str:
    value = _first_value(row, CURRENCY_COLUMNS).upper()
    if len(value) == 3 and value.isalpha():
        return value
    return ""


def _canonical_channel(raw: str) -> str | None:
    text = _text(raw)
    if not text:
        return None
    key = re.sub(r"[^a-z]", "", text.lower())
    if key in CHANNEL_ALIASES:
        return CHANNEL_ALIASES[key]
    lowered = text.lower()
    for channel, keywords in CHANNEL_KEYWORDS:
        if any(keyword in lowered for keyword in keywords):
            return channel
    return None


def _infer_channel(row) -> str:
    explicit = _canonical_channel(_first_value(row, CHANNEL_COLUMNS))
    return explicit or "Unknown"


def _location_value(row) -> dict[str, object]:
    values: dict[str, object] = {}
    for field, names in LOCATION_COLUMNS.items():
        raw = _first_value(row, names)
        if field in {"latitude", "longitude"}:
            number = _parse_amount(raw) if raw else None
            if number is None:
                values[field] = None
            elif field == "latitude" and -90 <= number <= 90:
                values[field] = number
            elif field == "longitude" and -180 <= number <= 180:
                values[field] = number
            else:
                values[field] = None
        else:
            values[field] = raw[:100]
    return values


def _parse_amount(value) -> float | None:
    if _is_blank(value):
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if math.isfinite(float(value)):
            return float(value)
        return None

    text = _text(value)
    parenthesized = text.startswith("(") and text.endswith(")")
    text = text.replace(",", "").replace("₹", "").replace("$", "").replace("€", "").replace("£", "")
    text = re.sub(r"(?i)\bINR\b", "", text).strip()
    if parenthesized:
        text = "-" + text.strip("()")
    try:
        amount = float(text)
    except (TypeError, ValueError):
        return None
    return amount if math.isfinite(amount) else None


def _parse_date(value) -> str | None:
    if _is_blank(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.date().isoformat()
    text = _text(value)
    if re.match(r"^\d{4}-\d{2}-\d{2}$", text):
        try:
            parsed = pd.to_datetime(text, errors="coerce", format="%Y-%m-%d")
            if not pd.isna(parsed):
                return parsed.date().isoformat()
        except (TypeError, ValueError, OverflowError):
            pass
    try:
        parsed = pd.to_datetime(text, errors="coerce", dayfirst=True)
    except (TypeError, ValueError, OverflowError):
        return None
    if pd.isna(parsed):
        return None
    return parsed.date().isoformat()


def _normalise_type(value) -> tuple[str, str]:
    raw = _text(value)
    if not raw:
        return "", "unknown"
    key = re.sub(r"[^a-z]", "", raw.lower())
    if key in CREDIT_TYPES:
        return raw, "credit"
    if key in DEBIT_TYPES:
        return raw, "debit"
    return raw, "unknown"


def _drop_empty_rows(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    if df.empty:
        return df, 0
    blank = df.apply(
        lambda row: all(_is_blank(value) for value in row.values),
        axis=1,
    )
    return df.loc[~blank], int(blank.sum())


def _stable_transaction_id(
    filename: str,
    file_index: int,
    row_index: int,
    date: str | None,
    amount: float | None,
    direction: str,
    vendor: str,
    description: str,
    upi_ref: str,
) -> str:
    payload = "|".join(
        [
            filename,
            str(file_index),
            str(row_index),
            date or "",
            f"{amount:.6f}" if amount is not None else "",
            direction,
            vendor.casefold(),
            description.casefold(),
            upi_ref.casefold(),
        ]
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]
    return f"GEN-{digest}"


def _duplicate_key(
    date: str | None,
    amount: float | None,
    direction: str,
    vendor: str,
    description: str,
    upi_ref: str,
    balance_after: float | None,
) -> str:
    if date is None or amount is None:
        return ""
    parts = [
        date,
        direction or "unknown",
        f"{abs(amount):.2f}",
        vendor.casefold().strip(),
        description.casefold().strip(),
        upi_ref.casefold().strip(),
    ]
    if balance_after is not None:
        parts.append(f"{balance_after:.2f}")
    return "|".join(parts)


def normalize_files(frames: list[tuple[str, pd.DataFrame]]) -> NormalizationResult:
    rows: list[dict] = []
    errors: list[str] = []
    warnings: list[str] = []
    schemas: set[str] = set()
    files: list[str] = []
    references: dict[str, dict[str, list[dict]]] = {
        "transaction ID": {},
        "UPI reference": {},
    }

    if not frames:
        return NormalizationResult(
            frame=pd.DataFrame(columns=CANONICAL_COLUMNS),
            errors=["Upload at least one CSV file."],
        )

    for file_index, (filename, df) in enumerate(frames):
        source_file = safe_filename(filename)
        files.append(source_file)

        if not isinstance(df, pd.DataFrame):
            errors.append(f"{source_file}: could not read CSV data.")
            continue

        work = df.copy()
        work.columns = [str(column).strip().lower() for column in work.columns]
        schemas.add(detect_schema(list(work.columns)))

        missing = sorted(REQUIRED_COLUMNS - set(work.columns))
        if missing:
            errors.append(f"{source_file}: Missing required columns: " + ", ".join(missing))
            continue

        work, skipped = _drop_empty_rows(work)
        if skipped:
            warnings.append(f"{source_file}: skipped {skipped} empty row(s).")
        if work.empty:
            errors.append(f"{source_file}: CSV contains no transaction rows.")
            continue

        for row_index, row in work.iterrows():
            data_row = int(row_index) + 1 if isinstance(row_index, (int, float)) else len(rows) + 1

            raw_date = row.get("date")
            date_value = _parse_date(raw_date)
            if _is_blank(raw_date):
                errors.append(f"{source_file}, row {data_row}: missing date.")
            elif date_value is None:
                errors.append(f"{source_file}, row {data_row}: invalid date.")

            raw_amount = row.get("amount")
            amount = _parse_amount(raw_amount)
            if _is_blank(raw_amount):
                errors.append(f"{source_file}, row {data_row}: missing amount.")
            elif amount is None:
                errors.append(f"{source_file}, row {data_row}: invalid amount.")

            raw_type = row.get("type", "")
            type_value, direction = _normalise_type(raw_type)

            vendor = _text(row.get("vendor", "")) or _text(row.get("recipient", ""))
            description = _text(row.get("description", ""))
            account = _text(row.get("account", ""))
            upi_ref = _text(row.get("upi_ref", ""))
            source_sms = _text(row.get("source_sms", ""))

            raw_balance = row.get("balance_after", "")
            balance_after = _parse_amount(raw_balance)
            if not _is_blank(raw_balance) and balance_after is None:
                warnings.append(f"{source_file}, row {data_row}: invalid balance_after ignored.")

            channel = _infer_channel(row)[:60]
            currency = _currency_value(row)
            location = _location_value(row)

            provided_id = _text(row.get("transaction_id", ""))[:100]
            if provided_id:
                transaction_id = provided_id
            else:
                transaction_id = _stable_transaction_id(
                    source_file,
                    file_index,
                    data_row,
                    date_value,
                    amount,
                    direction,
                    vendor,
                    description,
                    upi_ref,
                )

            if amount is None:
                signed_amount = None
            elif direction == "debit":
                signed_amount = -abs(amount)
            elif direction == "credit":
                signed_amount = abs(amount)
            else:
                signed_amount = amount

            item = {
                "transaction_id": transaction_id,
                "date": date_value or "",
                "amount": amount,
                "currency": currency,
                "signed_amount": signed_amount,
                "type": type_value,
                "direction": direction,
                "vendor": vendor,
                "description": description,
                "account": account,
                "upi_ref": upi_ref,
                "balance_after": balance_after,
                "source_sms": source_sms,
                "channel": channel,
                "country": location["country"],
                "state": location["state"],
                "city": location["city"],
                "latitude": location["latitude"],
                "longitude": location["longitude"],
                "source_file": source_file,
                "source_row": data_row,
                "duplicate_key": _duplicate_key(
                    date_value,
                    amount,
                    direction,
                    vendor,
                    description,
                    upi_ref,
                    balance_after,
                ),
            }
            rows.append(item)

            if provided_id:
                references["transaction ID"].setdefault(provided_id, []).append(item)
            if upi_ref:
                references["UPI reference"].setdefault(upi_ref, []).append(item)

    for label, groups in references.items():
        for items in groups.values():
            if len(items) < 2:
                continue
            if len({item["duplicate_key"] for item in items}) > 1:
                locations = ", ".join(
                    f"{item['source_file']} row {item['source_row']}" for item in items[:5]
                )
                if len(items) > 5:
                    locations += ", ..."
                errors.append(
                    f"Duplicate {label} refers to conflicting rows ({locations})."
                )

    frame = pd.DataFrame(rows, columns=CANONICAL_COLUMNS)
    schema = "bank" if "bank" in schemas else ("sample" if "sample" in schemas else "generic")
    return NormalizationResult(
        frame=frame,
        errors=errors,
        warnings=warnings,
        schema=schema,
        files=files,
    )
