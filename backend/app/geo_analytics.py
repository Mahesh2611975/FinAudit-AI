"""Geographic risk analytics derived from persisted transactions and alerts.

All counts and values below are computed from stored database rows. Stored
coordinates are used first. Named locations are resolved through an optional,
cached geocoder only when coordinates are missing. No analytical value is
hardcoded and no coordinates are fabricated.
"""
from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable

from sqlalchemy.orm import Session

from .geocoding import geocoding_enabled, location_key, resolve_locations
from .models import RiskAlert, TransactionRecord
from .risk_engine import (
    SUPPORTED_CHANNELS,
    _alert_record_ids,
    _alert_transaction_ids,
    _amount,
    _as_date,
    _text,
    _valid_coordinates,
    build_channel_analytics,
    infer_channel_details,
    load_analysis_transactions,
)

SEVERITY_RANK = {"low": 1, "medium": 2, "high": 3, "critical": 4}
HIGH_RISK_LEVELS = {"high", "critical"}
RISK_LEVEL_ORDER = ("low", "medium", "high", "critical")
VALID_RISK_FILTERS = set(RISK_LEVEL_ORDER)
# Risk classification is derived from persisted risk-analysis alerts by
# default. ``imported`` is an explicitly selectable alternative that uses an
# optional CSV-supplied label; it never changes which rows are flagged.
VALID_RISK_SOURCES = ("calculated", "imported")
DEFAULT_RISK_SOURCE = "calculated"
VALID_LOCATION_STATUSES = {"mapped", "named_only", "invalid_coordinates", "unavailable"}
LOCATION_UNAVAILABLE = "Location unavailable"
CHANNEL_ORDER = {channel: index for index, channel in enumerate(SUPPORTED_CHANNELS)}


@dataclass(frozen=True)
class GeoFilterSet:
    start_date: date | None = None
    end_date: date | None = None
    countries: tuple[str, ...] = ()
    risk_levels: frozenset[str] = frozenset()
    channels: tuple[str, ...] = ()
    min_amount: float | None = None
    risk_source: str = DEFAULT_RISK_SOURCE

    def as_dict(self) -> dict[str, Any]:
        return {
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "end_date": self.end_date.isoformat() if self.end_date else None,
            "countries": list(self.countries),
            "risk_levels": sorted(self.risk_levels),
            "channels": list(self.channels),
            "min_amount": self.min_amount,
            "risk_source": self.risk_source,
        }


def _split_values(value: str | Iterable[str] | None) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        items = value.split(",")
    else:
        items = value
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item or "").strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(text)
    return tuple(cleaned)


def parse_geo_filters(
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    country: str | None = None,
    risk_level: str | None = None,
    channel: str | None = None,
    min_amount: float | str | None = None,
    risk_source: str | None = None,
) -> GeoFilterSet:
    start = _as_date(start_date)
    if start_date and start is None:
        raise ValueError("start_date must be an ISO date")
    end = _as_date(end_date)
    if end_date and end is None:
        raise ValueError("end_date must be an ISO date")
    if start and end and start > end:
        raise ValueError("start_date must be on or before end_date")

    levels = {item.lower() for item in _split_values(risk_level)}
    invalid = levels - VALID_RISK_FILTERS
    if invalid:
        raise ValueError(
            "risk_level must contain only low, medium, high or critical"
        )

    minimum: float | None = None
    if min_amount not in (None, ""):
        try:
            minimum = float(min_amount)
        except (TypeError, ValueError) as exc:
            raise ValueError("min_amount must be a number") from exc
        if not math.isfinite(minimum) or minimum < 0:
            raise ValueError("min_amount must be zero or greater")

    source = str(risk_source or DEFAULT_RISK_SOURCE).lower()
    if source not in VALID_RISK_SOURCES:
        raise ValueError("risk_source must be 'calculated' or 'imported'")

    return GeoFilterSet(
        start_date=start,
        end_date=end,
        countries=_split_values(country),
        risk_levels=frozenset(levels),
        channels=_split_values(channel),
        min_amount=minimum,
        risk_source=source,
    )


def _alert_indexes(
    alerts: Iterable[RiskAlert],
) -> tuple[dict[int, list[RiskAlert]], dict[str, list[RiskAlert]]]:
    by_record: dict[int, list[RiskAlert]] = defaultdict(list)
    by_transaction: dict[str, list[RiskAlert]] = defaultdict(list)
    for alert in alerts:
        for record_id in _alert_record_ids(alert):
            by_record[record_id].append(alert)
        for transaction_id in _alert_transaction_ids(alert):
            by_transaction[transaction_id].append(alert)
    return by_record, by_transaction


def _related_alerts(
    record: TransactionRecord,
    by_record: dict[int, list[RiskAlert]],
    by_transaction: dict[str, list[RiskAlert]],
) -> list[RiskAlert]:
    related = list(by_record.get(record.id, []))
    transaction_id = _text(record.transaction_id)
    if transaction_id:
        related.extend(by_transaction.get(transaction_id, []))
    unique = {alert.id: alert for alert in related if alert.id is not None}
    return sorted(
        unique.values(),
        key=lambda alert: (SEVERITY_RANK.get(alert.severity, 0), alert.risk_score),
        reverse=True,
    )


def _risk_from_alerts(alerts: list[RiskAlert]) -> tuple[str, int, bool, str]:
    if not alerts:
        return (
            "low",
            0,
            False,
            "No anomaly alert is linked to this transaction in the latest analysis.",
        )
    strongest = max(
        alerts,
        key=lambda alert: (SEVERITY_RANK.get(alert.severity, 0), alert.risk_score),
    )
    level = strongest.severity if strongest.severity in SEVERITY_RANK else "low"
    score = max(int(alert.risk_score or 0) for alert in alerts)
    reasons: list[str] = []
    for alert in alerts:
        text = _text(alert.explanation)
        if text and text not in reasons:
            reasons.append(text)
        if len(reasons) >= 3:
            break
    return level, score, True, " · ".join(reasons)[:900]


def _imported_level(record: TransactionRecord) -> str | None:
    """Return a normalised CSV-supplied risk label, never a calculated score."""
    value = _text(getattr(record, "imported_risk_level", None)).lower()
    return value if value in VALID_RISK_FILTERS else None


def _select_risk_level(
    calculated: str, imported: str | None, source: str
) -> tuple[str, bool]:
    """Pick the classification level for the selected source.

    ``flagged`` is always derived from linked alerts (see ``_risk_from_alerts``)
    and is never derived from an imported label, so an imported label can never
    be presented as proof of fraud.
    """
    if source == "imported" and imported:
        return imported, True
    return calculated, False


def _flagged_value(entry: dict[str, Any]) -> float:
    """Single source of truth for the value of one flagged transaction.

    One entry represents one stored transaction, and ``amount`` is that
    transaction's own absolute amount. Multiple alerts linked to the same
    transaction are de-duplicated in ``_related_alerts`` before this value is
    used, so a transaction is counted at most once in any aggregate.
    """
    return entry["amount"] if entry.get("flagged") else 0.0


def _currency_for(record: TransactionRecord, alerts: list[RiskAlert]) -> str:
    for alert in alerts:
        code = _text(alert.currency).upper()
        if len(code) == 3 and code.isalpha():
            return code
    code = _text(getattr(record, "currency", None)).upper()
    if len(code) == 3 and code.isalpha():
        return code
    return "INR"


def _merge_location(
    record: TransactionRecord, alerts: list[RiskAlert]
) -> tuple[str, str, str, tuple[float, float] | None, str]:
    country = _text(record.country)
    state = _text(record.state)
    city = _text(record.city)
    coords = _valid_coordinates(record.latitude, record.longitude)
    source = "stored" if coords else ""

    if not (country and state and city) or coords is None:
        for alert in alerts:
            if not country:
                country = _text(alert.country)
            if not state:
                state = _text(alert.state)
            if not city:
                city = _text(alert.city)
            if coords is None:
                alert_coords = _valid_coordinates(alert.latitude, alert.longitude)
                if alert_coords:
                    coords = alert_coords
                    source = "alert"

    return country, state, city, coords, source


def _location_status(record: TransactionRecord) -> str:
    stored = _text(getattr(record, "location_status", None)).lower()
    if stored in VALID_LOCATION_STATUSES:
        return stored
    coords = _valid_coordinates(record.latitude, record.longitude)
    if coords:
        return "mapped"
    if any(_text(getattr(record, key, None)) for key in ("city", "state", "country")):
        return "named_only"
    return "unavailable"


def _matches_channel(entry_channel: str, channels: tuple[str, ...]) -> bool:
    if not channels:
        return True
    wanted = {item.lower() for item in channels}
    return entry_channel.lower() in wanted


def _matches_risk(actual: str, requested: frozenset[str]) -> bool:
    if not requested:
        return True
    if actual in requested:
        return True
    # The dashboard groups critical findings under the red "high" filter.
    return "high" in requested and actual == "critical"


def _in_date_range(value: Any, filters: GeoFilterSet) -> bool:
    if not filters.start_date and not filters.end_date:
        return True
    parsed = _as_date(value)
    if parsed is None:
        return False
    if filters.start_date and parsed < filters.start_date:
        return False
    if filters.end_date and parsed > filters.end_date:
        return False
    return True


def _base_entries(
    db: Session, filters: GeoFilterSet
) -> tuple[list[dict[str, Any]], dict[str, Any], list[TransactionRecord]]:
    records = load_analysis_transactions(db)
    alerts = db.query(RiskAlert).all()
    by_record, by_transaction = _alert_indexes(alerts)

    entries: list[dict[str, Any]] = []
    for record in records:
        amount = abs(_amount(record.amount))
        channel, channel_source = infer_channel_details(record)
        related = _related_alerts(record, by_record, by_transaction)
        calculated_level, risk_score, flagged, reason = _risk_from_alerts(related)
        imported_level = _imported_level(record)
        risk_level, risk_is_imported = _select_risk_level(
            calculated_level, imported_level, filters.risk_source
        )
        country, state, city, coords, location_source = _merge_location(
            record, related
        )

        if filters.countries and _text(country).lower() not in {
            item.lower() for item in filters.countries
        }:
            continue
        if filters.min_amount is not None and amount < filters.min_amount:
            continue
        if not _matches_channel(channel, filters.channels):
            continue
        if not _matches_risk(risk_level, filters.risk_levels):
            continue
        if not _in_date_range(record.date, filters):
            continue

        entries.append(
            {
                "record": record,
                "amount": amount,
                "currency": _currency_for(record, related),
                "channel": channel,
                "channel_source": channel_source,
                "merchant": _text(getattr(record, "merchant", None)) or None,
                "alerts": related,
                "alert_ids": sorted(
                    alert.alert_code for alert in related if alert.alert_code
                ),
                "linked_alert_count": len(related),
                "risk_level": risk_level,
                "risk_score": risk_score,
                "calculated_risk_level": calculated_level,
                "imported_risk_level": imported_level,
                "risk_is_imported": risk_is_imported,
                "flagged": flagged,
                "reason": reason,
                "country": country or None,
                "state": state or None,
                "city": city or None,
                "latitude": coords[0] if coords else None,
                "longitude": coords[1] if coords else None,
                "location_source": location_source,
                "location_status": "mapped" if coords else _location_status(record),
            }
        )

    options = _filter_options(records)
    return entries, options, records


def _filter_options(records: Iterable[TransactionRecord]) -> dict[str, Any]:
    records = list(records)
    observed = {infer_channel_details(record)[0] for record in records}
    # Keep Unknown available even when no transaction currently uses it so a
    # user can filter explicitly for records with an unavailable channel.
    observed.add("Unknown")
    channels = sorted(
        observed,
        key=lambda channel: (CHANNEL_ORDER.get(channel, len(CHANNEL_ORDER)), channel),
    )
    return {
        "countries": sorted(
            {_text(record.country) for record in records if _text(record.country)},
            key=str.lower,
        ),
        "states": sorted(
            {_text(record.state) for record in records if _text(record.state)},
            key=str.lower,
        ),
        "cities": sorted(
            {_text(record.city) for record in records if _text(record.city)},
            key=str.lower,
        ),
        "channels": channels,
        "risk_levels": list(RISK_LEVEL_ORDER),
        "risk_sources": list(VALID_RISK_SOURCES),
    }


def _resolve_missing_coordinates(
    db: Session,
    entries: list[dict[str, Any]],
    *,
    allow_geocode: bool,
) -> dict[str, Any]:
    named = [
        entry
        for entry in entries
        if entry["latitude"] is None
        and any(entry.get(key) for key in ("city", "state", "country"))
    ]
    if not named:
        return {
            "enabled": geocoding_enabled(),
            "provider": "nominatim" if geocoding_enabled() else None,
            "requested": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "network_lookups": 0,
            "unresolved": 0,
            "errors": 0,
        }

    resolved, stats = resolve_locations(
        db,
        [
            {
                "city": entry.get("city"),
                "state": entry.get("state"),
                "country": entry.get("country"),
            }
            for entry in named
        ],
        allow_network=allow_geocode and geocoding_enabled(),
    )

    for entry in named:
        key = location_key(entry.get("city"), entry.get("state"), entry.get("country"))
        result = resolved.get(key)
        if not result:
            continue
        entry["latitude"] = result["latitude"]
        entry["longitude"] = result["longitude"]
        entry["location_source"] = result["source"]
        entry["location_status"] = "mapped"
    return stats


def _display_name(entry: dict[str, Any]) -> str:
    parts = [
        _text(entry.get("city")),
        _text(entry.get("state")),
        _text(entry.get("country")),
    ]
    label = ", ".join(part for part in parts if part)
    return label or LOCATION_UNAVAILABLE


def _marker(entry: dict[str, Any]) -> dict[str, Any]:
    record: TransactionRecord = entry["record"]
    return {
        "id": record.id,
        "transaction_id": record.transaction_id or f"Record {record.id}",
        "date": record.date,
        "amount": round(entry["amount"], 2),
        "currency": entry["currency"],
        "risk_score": entry["risk_score"],
        "risk_level": entry["risk_level"],
        "risk_classification_source": (
            "imported" if entry.get("risk_is_imported") else "calculated"
        ),
        "calculated_risk_level": entry.get("calculated_risk_level"),
        "imported_risk_level": entry.get("imported_risk_level"),
        "marker_group": (
            "high"
            if entry["risk_level"] in HIGH_RISK_LEVELS
            else "medium"
            if entry["risk_level"] == "medium"
            else "low"
        ),
        "flagged": entry["flagged"],
        "reason": entry["reason"],
        "city": entry["city"],
        "state": entry["state"],
        "country": entry["country"],
        "latitude": entry["latitude"],
        "longitude": entry["longitude"],
        "source_file": record.source_file,
        "source_row": record.source_row,
        "channel": entry["channel"],
        "channel_source": entry["channel_source"],
        "channel_original": _text(getattr(record, "channel_original", None)) or None,
        "linked_alert_count": entry.get("linked_alert_count", len(entry["alerts"])),
        "merchant": entry["merchant"],
        "vendor": record.vendor,
        "description": record.description,
        "location_status": entry["location_status"],
        "location_source": entry["location_source"],
        "alert_ids": entry["alert_ids"],
        "location_label": _display_name(entry),
    }


def _country_aggregates(
    entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for entry in entries:
        country = _text(entry.get("country"))
        if not country or entry["latitude"] is None:
            continue
        key = country.lower()
        bucket = buckets.setdefault(
            key,
            {
                "country": country,
                "transaction_count": 0,
                "total_value": 0.0,
                "critical_count": 0,
                "high_count": 0,
                "medium_count": 0,
                "low_count": 0,
                "flagged_count": 0,
                "flagged_value": 0.0,
                "risk_score_total": 0,
                "max_risk_score": 0,
                "_cities": set(),
                "_latitudes": [],
                "_longitudes": [],
            },
        )
        bucket["transaction_count"] += 1
        bucket["total_value"] += entry["amount"]
        bucket[f"{entry['risk_level']}_count"] += 1
        bucket["risk_score_total"] += entry["risk_score"]
        bucket["max_risk_score"] = max(
            bucket["max_risk_score"], entry["risk_score"]
        )
        if entry["flagged"]:
            bucket["flagged_count"] += 1
            bucket["flagged_value"] += _flagged_value(entry)
        if entry.get("city"):
            bucket["_cities"].add(_text(entry["city"]).lower())
        bucket["_latitudes"].append(entry["latitude"])
        bucket["_longitudes"].append(entry["longitude"])

    result: list[dict[str, Any]] = []
    for bucket in buckets.values():
        count = bucket["transaction_count"]
        high_risk_count = bucket["high_count"] + bucket["critical_count"]
        result.append(
            {
                "country": bucket["country"],
                "transaction_count": count,
                "total_value": round(bucket["total_value"], 2),
                "critical_count": bucket["critical_count"],
                "high_count": bucket["high_count"],
                "medium_count": bucket["medium_count"],
                "low_count": bucket["low_count"],
                "high_risk_count": high_risk_count,
                "flagged_count": bucket["flagged_count"],
                "flagged_value": round(bucket["flagged_value"], 2),
                "average_risk_score": round(bucket["risk_score_total"] / count, 1)
                if count
                else 0,
                "max_risk_score": bucket["max_risk_score"],
                "city_count": len(bucket["_cities"]),
                "latitude": round(
                    statistics.fmean(bucket["_latitudes"]), 5
                )
                if bucket["_latitudes"]
                else None,
                "longitude": round(
                    statistics.fmean(bucket["_longitudes"]), 5
                )
                if bucket["_longitudes"]
                else None,
            }
        )
    result.sort(
        key=lambda item: (
            item["high_risk_count"],
            item["flagged_value"],
            item["transaction_count"],
        ),
        reverse=True,
    )
    return result


def _city_aggregates(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str, str], dict[str, Any]] = {}
    for entry in entries:
        city = _text(entry.get("city"))
        if not city or entry["latitude"] is None:
            continue
        key = (
            city.lower(),
            _text(entry.get("state")).lower(),
            _text(entry.get("country")).lower(),
        )
        bucket = buckets.setdefault(
            key,
            {
                "city": city,
                "state": entry.get("state"),
                "country": entry.get("country"),
                "latitude": entry["latitude"],
                "longitude": entry["longitude"],
                "transaction_count": 0,
                "total_value": 0.0,
                "critical_count": 0,
                "high_count": 0,
                "medium_count": 0,
                "low_count": 0,
                "flagged_count": 0,
                "flagged_value": 0.0,
                "risk_score_total": 0,
                "max_risk_score": 0,
            },
        )
        bucket["transaction_count"] += 1
        bucket["total_value"] += entry["amount"]
        bucket[f"{entry['risk_level']}_count"] += 1
        bucket["risk_score_total"] += entry["risk_score"]
        bucket["max_risk_score"] = max(bucket["max_risk_score"], entry["risk_score"])
        if entry["flagged"]:
            bucket["flagged_count"] += 1
            bucket["flagged_value"] += _flagged_value(entry)

    result: list[dict[str, Any]] = []
    for bucket in buckets.values():
        count = bucket["transaction_count"]
        high_risk_count = bucket["high_count"] + bucket["critical_count"]
        result.append(
            {
                "city": bucket["city"],
                "state": bucket["state"],
                "country": bucket["country"],
                "latitude": bucket["latitude"],
                "longitude": bucket["longitude"],
                "transaction_count": count,
                "total_value": round(bucket["total_value"], 2),
                "critical_count": bucket["critical_count"],
                "high_count": bucket["high_count"],
                "medium_count": bucket["medium_count"],
                "low_count": bucket["low_count"],
                "high_risk_count": high_risk_count,
                "flagged_count": bucket["flagged_count"],
                "flagged_value": round(bucket["flagged_value"], 2),
                "average_risk_score": round(bucket["risk_score_total"] / count, 1)
                if count
                else 0,
                "max_risk_score": bucket["max_risk_score"],
            }
        )
    result.sort(
        key=lambda item: (
            item["high_risk_count"],
            item["flagged_value"],
            item["transaction_count"],
        ),
        reverse=True,
    )
    return result


def _unusual_locations(
    cities: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    counts = [item["transaction_count"] for item in cities]
    if len(counts) < 2:
        return []
    mean = statistics.fmean(counts)
    deviation = statistics.pstdev(counts)
    threshold = mean + (2 * deviation) if deviation > 0 else max(2.0, mean)
    threshold = max(threshold, 2.0)
    unusual = []
    for item in cities:
        if item["transaction_count"] < threshold:
            continue
        unusual.append(
            {
                **item,
                "activity_threshold": round(threshold, 2),
                "activity_index": round(item["transaction_count"] / mean, 2)
                if mean
                else 0,
            }
        )
    unusual.sort(key=lambda item: item["transaction_count"], reverse=True)
    return unusual


def _trend(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if entry["latitude"] is None:
            continue
        day = _text(entry["record"].date)[:10]
        if not day:
            continue
        bucket = buckets.setdefault(
            day,
            {
                "date": day,
                "total": 0,
                "critical": 0,
                "high": 0,
                "medium": 0,
                "low": 0,
                "flagged": 0,
                "flagged_value": 0.0,
            },
        )
        bucket["total"] += 1
        bucket[entry["risk_level"]] += 1
        if entry["flagged"]:
            bucket["flagged"] += 1
            bucket["flagged_value"] += _flagged_value(entry)
    series = []
    for day in sorted(buckets):
        item = buckets[day]
        series.append({**item, "flagged_value": round(item["flagged_value"], 2)})
    return series


def _summary(
    entries: list[dict[str, Any]],
    geocoding_stats: dict[str, Any],
    total_records: int,
    risk_source: str = DEFAULT_RISK_SOURCE,
) -> dict[str, Any]:
    located = [entry for entry in entries if entry["latitude"] is not None]
    named = [
        entry
        for entry in entries
        if entry.get("city") or entry.get("state") or entry.get("country")
    ]
    coordinate_backed = [
        entry
        for entry in located
        if entry["location_source"] in {"stored", "alert"}
    ]
    geocoded = [
        entry
        for entry in located
        if entry["location_source"] in {"geocode_cache", "nominatim"}
    ]
    flagged = [entry for entry in located if entry["flagged"]]
    high_risk = [
        entry for entry in located if entry["risk_level"] in HIGH_RISK_LEVELS
    ]
    # Distribution over every matched transaction so the four levels always add
    # up to ``total_transactions_matched`` for the selected classification source.
    distribution = Counter(entry["risk_level"] for entry in entries)
    mapped_distribution = Counter(entry["risk_level"] for entry in located)
    status_counts = Counter(entry.get("location_status", "unavailable") for entry in entries)
    countries = {_text(entry.get("country")).lower() for entry in located if entry.get("country")}
    cities = {
        (
            _text(entry.get("city")).lower(),
            _text(entry.get("state")).lower(),
            _text(entry.get("country")).lower(),
        )
        for entry in located
        if entry.get("city")
    }
    linked = [entry for entry in entries if entry.get("linked_alert_count", 0) > 0]
    imported_labels = [
        entry for entry in entries if entry.get("imported_risk_level")
    ]
    return {
        "total_transactions": len(entries),
        "total_transactions_matched": len(entries),
        "total_transactions_in_snapshot": total_records,
        "transactions_with_coordinates": len(located),
        "transactions_without_coordinates": len(entries) - len(located),
        "transactions_with_linked_alerts": len(linked),
        "transactions_without_linked_alerts": len(entries) - len(linked),
        "classification_source": risk_source,
        "imported_label_transactions": len(imported_labels),
        "transactions_with_location_information": sum(
            1
            for entry in entries
            if entry["latitude"] is not None
            or entry.get("city")
            or entry.get("state")
            or entry.get("country")
        ),
        "named_location_transactions": len(named),
        "located_transactions": len(located),
        "coordinate_transactions": len(coordinate_backed),
        "geocoded_transactions": len(geocoded),
        "unlocated_transactions": len(entries) - len(located),
        "location_status_counts": {
            status: status_counts.get(status, 0)
            for status in sorted(VALID_LOCATION_STATUSES)
        },
        "named_only_transactions": status_counts.get("named_only", 0),
        "invalid_coordinate_transactions": status_counts.get("invalid_coordinates", 0),
        "unavailable_location_transactions": status_counts.get("unavailable", 0),
        "total_mapped_value": round(sum(entry["amount"] for entry in located), 2),
        "flagged_transactions": len(flagged),
        "flagged_value": round(sum(_flagged_value(entry) for entry in flagged), 2),
        "high_risk_transactions": len(high_risk),
        "high_risk_value": round(sum(entry["amount"] for entry in high_risk), 2),
        "countries_count": len(countries),
        "cities_count": len(cities),
        "risk_distribution": {
            level: distribution.get(level, 0) for level in RISK_LEVEL_ORDER
        },
        "mapped_risk_distribution": {
            level: mapped_distribution.get(level, 0) for level in RISK_LEVEL_ORDER
        },
        "geocoding": geocoding_stats,
    }


def geography_filter_options(db: Session) -> dict[str, Any]:
    records = load_analysis_transactions(db)
    return _filter_options(records)


def geography_insights(
    db: Session,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    country: str | None = None,
    risk_level: str | None = None,
    channel: str | None = None,
    min_amount: float | str | None = None,
    max_markers: int = 2000,
    allow_geocode: bool = True,
    risk_source: str | None = None,
) -> dict[str, Any]:
    filters = parse_geo_filters(
        start_date=start_date,
        end_date=end_date,
        country=country,
        risk_level=risk_level,
        channel=channel,
        min_amount=min_amount,
        risk_source=risk_source,
    )
    entries, options, records = _base_entries(db, filters)
    geocoding_stats = _resolve_missing_coordinates(
        db, entries, allow_geocode=allow_geocode
    )

    located = [entry for entry in entries if entry["latitude"] is not None]
    located.sort(
        key=lambda entry: (
            SEVERITY_RANK.get(entry["risk_level"], 0),
            entry["amount"],
        ),
        reverse=True,
    )
    markers = [_marker(entry) for entry in located[: max(1, min(max_markers, 5000))]]
    countries = _country_aggregates(entries)
    cities = _city_aggregates(entries)
    high_risk_locations = [
        item for item in cities if item["high_risk_count"] > 0
    ][:25]
    unusual = _unusual_locations(cities)
    trend = _trend(entries)
    channel_analytics = build_channel_analytics(entries)
    summary = _summary(entries, geocoding_stats, len(records), filters.risk_source)
    available = bool(located)

    if not entries:
        reason = "No transactions match the selected filters."
    elif summary["located_transactions"] == 0:
        if summary["named_location_transactions"] == 0:
            reason = (
                "No transaction location fields are stored. The map does not "
                "invent coordinates or locations."
            )
        elif not geocoding_stats.get("enabled"):
            reason = (
                "Location names are present, but coordinates are unavailable. "
                "Enable optional geocoding (FINAUDIT_GEOCODING_ENABLED=true) or "
                "import latitude/longitude columns."
            )
        else:
            reason = (
                "Location names are present, but the optional geocoder could not "
                "resolve them. No coordinates were fabricated."
            )
    else:
        reason = None

    return {
        "available": available,
        "reason": reason,
        "summary": summary,
        "markers": markers,
        "countries": countries,
        "high_risk_countries": [
            item for item in countries if item["high_risk_count"] > 0
        ],
        "cities": cities,
        "high_risk_locations": high_risk_locations,
        "unusual_locations": unusual,
        "trend": trend,
        "channel_analytics": channel_analytics,
        "filters": filters.as_dict(),
        "options": options,
        "geocoding": geocoding_stats,
        "notice": (
            "Every marker comes from a stored transaction. Risk levels follow "
            f"the '{filters.risk_source}' classification source; imported CSV "
            "labels are provenance only and are not proof of fraud. Alerts are "
            "anomaly indicators for human review, not fraud convictions."
        ),
    }


def geography_map(
    db: Session,
    **kwargs: Any,
) -> dict[str, Any]:
    data = geography_insights(db, **kwargs)
    return {
        "available": data["available"],
        "reason": data["reason"],
        "summary": data["summary"],
        "markers": data["markers"],
        "channel_analytics": data["channel_analytics"],
        "filters": data["filters"],
        "options": data["options"],
        "geocoding": data["geocoding"],
        "notice": data["notice"],
    }


def geography_countries(db: Session, **kwargs: Any) -> dict[str, Any]:
    data = geography_insights(db, **kwargs)
    return {
        "available": data["available"],
        "reason": data["reason"],
        "summary": data["summary"],
        "countries": data["countries"],
        "high_risk_countries": data["high_risk_countries"],
    }


def geography_cities(db: Session, **kwargs: Any) -> dict[str, Any]:
    data = geography_insights(db, **kwargs)
    return {
        "available": data["available"],
        "reason": data["reason"],
        "summary": data["summary"],
        "cities": data["cities"],
        "high_risk_locations": data["high_risk_locations"],
        "unusual_locations": data["unusual_locations"],
    }


def geography_high_risk_locations(db: Session, **kwargs: Any) -> dict[str, Any]:
    data = geography_insights(db, **kwargs)
    return {
        "available": data["available"],
        "reason": data["reason"],
        "summary": data["summary"],
        "high_risk_locations": data["high_risk_locations"],
        "unusual_locations": data["unusual_locations"],
    }


def geography_trend(db: Session, **kwargs: Any) -> dict[str, Any]:
    data = geography_insights(db, **kwargs)
    return {
        "available": data["available"],
        "reason": data["reason"],
        "summary": data["summary"],
        "trend": data["trend"],
    }
