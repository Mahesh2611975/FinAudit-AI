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
    "merchant",
    "description",
    "account",
    "upi_ref",
    "balance_after",
    "source_sms",
    "channel",
    "channel_source",
    "channel_original",
    "imported_risk_level",
    "country",
    "state",
    "city",
    "latitude",
    "longitude",
    "location_status",
    "source_file",
    "source_row",
    "duplicate_key",
]

# A CSV ``risk_level`` column is an *imported label*, not a calculated risk
# score.  It is stored for provenance and never used as proof of fraud.
IMPORTED_RISK_LEVELS = ("low", "medium", "high", "critical")
IMPORTED_RISK_ALIASES = {
    "l": "low",
    "lo": "low",
    "low": "low",
    "m": "medium",
    "med": "medium",
    "medium": "medium",
    "h": "high",
    "high": "high",
    "c": "critical",
    "crit": "critical",
    "critical": "critical",
}

# Canonical payment channels used for newly ingested data.
CANONICAL_CHANNELS = (
    "Online Banking",
    "Mobile App",
    "ATM",
    "Wire Transfer",
    "Point of Sale",
    "UPI",
    "Card Payment",
    "Cash",
    "Unknown",
)

# Legacy labels are still accepted so previously stored records keep their
# original channel value. They are not used for new uploads unless the source
# explicitly supplies one of these labels.
LEGACY_CHANNELS = ("Mobile Banking", "POS", "NEFT", "RTGS", "IMPS")
SUPPORTED_CHANNELS = CANONICAL_CHANNELS + LEGACY_CHANNELS

CHANNEL_ALIASES = {
    "onlinebanking": "Online Banking",
    "internetbanking": "Online Banking",
    "netbanking": "Online Banking",
    "onlinebank": "Online Banking",
    "mobilebanking": "Mobile App",
    "mobileapp": "Mobile App",
    "bankingapp": "Mobile App",
    "atm": "ATM",
    "atmcash": "ATM",
    "cashwithdrawal": "ATM",
    "cashwithdraw": "ATM",
    "wire": "Wire Transfer",
    "wiretransfer": "Wire Transfer",
    "swift": "Wire Transfer",
    "internationaltransfer": "Wire Transfer",
    "neft": "Wire Transfer",
    "rtgs": "Wire Transfer",
    "imps": "Wire Transfer",
    "pos": "Point of Sale",
    "pointofsale": "Point of Sale",
    "cardpos": "Point of Sale",
    "cardswipe": "Point of Sale",
    "merchantterminal": "Point of Sale",
    "upi": "UPI",
    "vpa": "UPI",
    "gpay": "UPI",
    "googlepay": "UPI",
    "phonepe": "UPI",
    "paytm": "UPI",
    "bhim": "UPI",
    "card": "Card Payment",
    "cardpayment": "Card Payment",
    "creditcard": "Card Payment",
    "debitcard": "Card Payment",
    "visacard": "Card Payment",
    "mastercard": "Card Payment",
    "cash": "Cash",
    "cashpayment": "Cash",
    "paidincash": "Cash",
    "unknown": "Unknown",
}

CHANNEL_KEYWORDS = [
    ("Wire Transfer", ("wire transfer", "wire", "swift", "neft", "rtgs", "imps", "international transfer")),
    ("UPI", ("upi", "vpa", "gpay", "google pay", "phonepe", "paytm", "bhim")),
    ("Point of Sale", ("point of sale", "pos", "card swipe", "merchant terminal")),
    ("ATM", ("atm", "cash withdrawal", "cash withdraw")),
    ("Online Banking", ("online banking", "internet banking", "net banking", "netbanking")),
    ("Mobile App", ("mobile banking", "mobile app", "banking app")),
    ("Card Payment", ("credit card", "debit card", "card payment", "card ending", "visa", "mastercard")),
    ("Cash", ("cash payment", "paid in cash", "cash")),
]

CHANNEL_COLUMNS = (
    "channel",
    "payment_channel",
    "transaction_channel",
    "payment_mode",
    "channel_type",
    "payment_method",
    "mode",
)
CURRENCY_COLUMNS = ("currency", "currency_code", "ccy")
LOCATION_COLUMNS = {
    "country": ("country", "country_name"),
    "state": ("state", "state_name", "province", "region"),
    "city": ("city", "city_name", "town", "location_city"),
    "latitude": ("latitude", "lat"),
    "longitude": ("longitude", "lng", "lon", "long"),
}
DESCRIPTION_COLUMNS = (
    "description",
    "transaction_description",
    "payment_description",
    "narration",
    "remarks",
    "details",
    "memo",
)

# Header aliases cover human-readable exports as well as snake_case exports.
# Values are already passed through ``_normalize_header``.
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "transaction_id": (
        "transaction_id",
        "transactionid",
        "txn_id",
        "txnid",
        "payment_id",
        "transaction_reference",
        "reference_id",
        "id",
    ),
    "date": (
        "date",
        "transaction_date",
        "transactiondate",
        "txn_date",
        "txndate",
        "trans_date",
        "value_date",
        "posting_date",
        "booking_date",
        "entry_date",
    ),
    "amount": (
        "amount",
        "transaction_amount",
        "transactionamount",
        "txn_amount",
        "txnamount",
        "amount_inr",
        "value",
    ),
    "currency": ("currency", "currency_code", "currencycode", "ccy"),
    "description": DESCRIPTION_COLUMNS,
    "merchant": (
        "merchant",
        "merchant_name",
        "merchantname",
        "merchant_city_name",
        "payee_name",
    ),
    "vendor": (
        "vendor",
        "vendor_name",
        "vendorname",
        "supplier",
        "supplier_name",
        "recipient",
        "beneficiary",
        "counterparty",
        "payee",
    ),
    "payment_channel": CHANNEL_COLUMNS,
    "imported_risk_level": (
        "risk_level",
        "risk",
        "risk_rating",
        "riskrating",
        "imported_risk_level",
    ),
    "city": LOCATION_COLUMNS["city"] + ("transaction_city", "merchant_city"),
    "state": LOCATION_COLUMNS["state"] + ("transaction_state", "merchant_state"),
    "country": LOCATION_COLUMNS["country"] + ("transaction_country", "merchant_country"),
    "latitude": LOCATION_COLUMNS["latitude"] + ("transaction_latitude", "location_latitude"),
    "longitude": LOCATION_COLUMNS["longitude"] + ("transaction_longitude", "location_longitude"),
    "source_file": ("source_file", "source_filename", "sourcefile"),
    "type": ("type", "transaction_type", "transactiontype", "dr_cr", "direction"),
    "account": ("account", "account_number", "account_no", "acct"),
    "upi_ref": ("upi_ref", "upi_reference", "upi_id", "upi", "vpa"),
    "balance_after": ("balance_after", "balance", "closing_balance", "balanceafter"),
    "source_sms": ("source_sms", "sms", "sms_text"),
}

_REVERSE_COLUMN_ALIASES: dict[str, str] = {}
for _canonical, _aliases in COLUMN_ALIASES.items():
    for _alias in _aliases:
        _normalized_alias = re.sub(r"[^a-z0-9]+", "_", _alias.lower()).strip("_")
        _REVERSE_COLUMN_ALIASES.setdefault(_normalized_alias, _canonical)
        _REVERSE_COLUMN_ALIASES.setdefault(_canonical, _canonical)

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
    return required.issubset(set(map(str, df.columns)))


def safe_filename(filename: str | None) -> str:
    base = re.split(r"[\\/]", str(filename or ""))[-1].strip()
    return (base or "upload.csv")[:255]


def _normalize_header(value) -> str:
    text = str(value or "").replace("\ufeff", "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text


def _canonicalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Rename common CSV header variations to the internal schema.

    Columns that are not recognised are kept with a normalised name so that no
    source data is silently discarded. Duplicate mappings keep the first
    occurrence, which matches pandas' usual behaviour for repeated headers.
    """
    work = df.copy()
    renamed: list[str] = []
    used: set[str] = set()
    for column in work.columns:
        normalized = _normalize_header(column)
        target = _REVERSE_COLUMN_ALIASES.get(normalized)
        if target is None:
            target = normalized
        if target in used:
            target = f"{target}_duplicate"
        used.add(target)
        renamed.append(target)
    work.columns = renamed
    return work


def detect_schema(columns: list[str]) -> str:
    cols = {_normalize_header(column) for column in columns}
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


def _keyword_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _canonical_channel(raw: str) -> str | None:
    """Map an explicit channel label to a supported canonical channel."""
    text = _text(raw)
    if not text:
        return None
    key = re.sub(r"[^a-z]", "", text.lower())
    if key in CHANNEL_ALIASES:
        return CHANNEL_ALIASES[key]
    return _channel_from_text(text)


def _channel_from_text(raw: str) -> str | None:
    """Classify a channel from free text using whole-keyword matches."""
    key = _keyword_key(raw)
    if not key:
        return None
    for channel, keywords in CHANNEL_KEYWORDS:
        for keyword in keywords:
            pattern = r"\b" + re.escape(_keyword_key(keyword)) + r"\b"
            if re.search(pattern, key):
                return channel
    return None


def channel_from_description(raw: str) -> str | None:
    """Public helper for classifying a channel from transaction free text."""
    return _channel_from_text(raw)


def _infer_channel(row) -> tuple[str, str, str | None]:
    """Return ``(channel, source, warning)`` for a single transaction row.

    Explicit source columns always win. Free-text classification is reported as
    ``inferred`` and never overwrites the original description.
    """
    explicit = _first_value(row, CHANNEL_COLUMNS)
    if explicit:
        canonical = _canonical_channel(explicit)
        if canonical:
            if canonical == "Unknown":
                return "Unknown", "unknown", None
            return canonical, "explicit", None
        return (
            "Unknown",
            "explicit_unmapped",
            f"Payment channel '{explicit[:60]}' was not recognised; classified as Unknown.",
        )

    description = _first_value(row, DESCRIPTION_COLUMNS) or _text(row.get("source_sms", ""))
    inferred = _channel_from_text(description)
    if inferred:
        return inferred, "inferred", None
    return "Unknown", "unknown", None


def _imported_risk_level(row) -> tuple[str | None, str | None]:
    """Normalise an optional CSV ``risk_level`` label for provenance only.

    The returned value is a label supplied by the source file.  It is stored
    alongside (never instead of) the calculated risk alerts and is never
    interpreted as evidence of fraud.  Unrecognised labels are reported as a
    warning and are not stored as a calculated risk level.
    """
    raw = _first_value(row, ("imported_risk_level",))
    if not raw:
        return None, None
    key = re.sub(r"[^a-z0-9]+", "", raw.lower()).strip()
    level = IMPORTED_RISK_ALIASES.get(key)
    if level is None:
        return None, (
            f"Imported risk label '{raw[:60]}' is not one of "
            f"{', '.join(IMPORTED_RISK_LEVELS)}; kept as an unclassified label."
        )
    return level, None


def _parse_coordinate(value) -> float | None:
    if _is_blank(value) or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = _text(value).replace(",", "").replace("°", "").strip()
        try:
            number = float(text)
        except (TypeError, ValueError):
            return None
    return number if math.isfinite(number) else None


def _location_value(row) -> tuple[dict[str, object], list[str]]:
    values: dict[str, object] = {}
    warnings: list[str] = []

    for field_name, names in LOCATION_COLUMNS.items():
        raw = _first_value(row, names)
        if field_name == "latitude":
            number = _parse_coordinate(raw)
            if number is not None and not (-90 <= number <= 90):
                warnings.append(f"Latitude {raw} is outside -90..90; coordinates ignored.")
                number = None
            values[field_name] = number
        elif field_name == "longitude":
            number = _parse_coordinate(raw)
            if number is not None and not (-180 <= number <= 180):
                warnings.append(f"Longitude {raw} is outside -180..180; coordinates ignored.")
                number = None
            values[field_name] = number
        else:
            values[field_name] = raw[:100]

    has_lat_text = bool(_first_value(row, LOCATION_COLUMNS["latitude"]))
    has_lng_text = bool(_first_value(row, LOCATION_COLUMNS["longitude"]))
    # Use `is not None`, not truthiness: a valid 0.0 latitude/longitude
    # (equator / prime meridian) must not be mistaken for a missing value.
    has_lat = values["latitude"] is not None
    has_lng = values["longitude"] is not None
    if has_lat != has_lng:
        if not warnings:
            warnings.append("Incomplete latitude/longitude pair ignored.")
        values["latitude"] = None
        values["longitude"] = None

    has_names = any(values.get(key) for key in ("city", "state", "country"))
    has_coordinates = values["latitude"] is not None and values["longitude"] is not None
    if has_coordinates:
        status = "mapped"
    elif has_names:
        status = "invalid_coordinates" if (has_lat_text or has_lng_text) else "named_only"
    elif has_lat_text or has_lng_text:
        status = "invalid_coordinates"
    else:
        status = "unavailable"
    values["location_status"] = status
    return values, warnings


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

    # ISO-like dates/timestamps: YYYY-MM-DD, YYYY-MM-DDTHH:MM:SS, YYYY-MM-DD HH:MM:SS...
    # Parse the date component strictly to avoid dayfirst month/day swaps on timestamps.
    iso_prefix = re.match(r"^(\d{4})-(\d{2})-(\d{2})(?:$|[T\s])", text)
    if iso_prefix:
        date_part = text[:10]
        try:
            parsed = pd.to_datetime(date_part, errors="coerce", format="%Y-%m-%d")
            if not pd.isna(parsed):
                return parsed.date().isoformat()
        except (TypeError, ValueError, OverflowError):
            return None
        return None

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

        work = _canonicalize_columns(df)
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
            row_label = f"{source_file}, row {data_row}"

            raw_date = _first_value(row, ("date",))
            date_value = _parse_date(raw_date)
            if not raw_date:
                errors.append(f"{row_label}: missing date.")
            elif date_value is None:
                errors.append(f"{row_label}: invalid date.")

            raw_amount = _first_value(row, ("amount",))
            amount = _parse_amount(raw_amount)
            if not raw_amount:
                errors.append(f"{row_label}: missing amount.")
            elif amount is None:
                errors.append(f"{row_label}: invalid amount.")

            raw_type = _first_value(row, ("type",))
            type_value, direction = _normalise_type(raw_type)

            merchant = _first_value(row, ("merchant",))[:255]
            vendor = (
                _first_value(row, ("vendor",))
                or _first_value(row, ("recipient", "supplier", "payee", "beneficiary"))
                or merchant
            )
            description = _first_value(row, DESCRIPTION_COLUMNS)
            account = _first_value(row, ("account",))
            upi_ref = _first_value(row, ("upi_ref",))
            source_sms = _first_value(row, ("source_sms",))

            raw_balance = _first_value(row, ("balance_after",))
            balance_after = _parse_amount(raw_balance)
            if raw_balance and balance_after is None:
                warnings.append(f"{row_label}: invalid balance_after ignored.")

            channel, channel_source, channel_warning = _infer_channel(row)
            if channel_warning:
                warnings.append(f"{row_label}: {channel_warning}")
            # Keep the raw imported channel so an unmapped value is not lost.
            # Normalised ``channel`` is used for analytics; the original is
            # retained for display and audit provenance.
            channel_original = _first_value(row, CHANNEL_COLUMNS)[:255] or None
            imported_risk, imported_risk_warning = _imported_risk_level(row)
            if imported_risk_warning:
                warnings.append(f"{row_label}: {imported_risk_warning}")
            currency = _currency_value(row)
            location, location_warnings = _location_value(row)
            for warning in location_warnings:
                warnings.append(f"{row_label}: {warning}")

            provided_id = _first_value(row, ("transaction_id",))[:100]
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
                "vendor": vendor[:255],
                "merchant": merchant,
                "description": description,
                "account": account,
                "upi_ref": upi_ref,
                "balance_after": balance_after,
                "source_sms": source_sms,
                "channel": channel,
                "channel_source": channel_source,
                "channel_original": channel_original,
                "imported_risk_level": imported_risk,
                "country": location["country"],
                "state": location["state"],
                "city": location["city"],
                "latitude": location["latitude"],
                "longitude": location["longitude"],
                "location_status": location["location_status"],
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
