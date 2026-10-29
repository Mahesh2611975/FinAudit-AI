from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any


def money_to_decimal(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    try:
        # Handle float strings safely
        s = str(value).strip()
        if s == "" or s.lower() in {"nan", "none"}:
            return Decimal("0")
        return Decimal(s)
    except Exception:
        try:
            return Decimal(str(float(value)))
        except Exception:
            return Decimal("0")


def round_currency(value: Decimal, places: int = 2) -> Decimal:
    q = Decimal(10) ** (-places)
    return value.quantize(q, rounding=ROUND_HALF_UP)


def abs_decimal(value: Decimal) -> Decimal:
    return -value if value < 0 else value
