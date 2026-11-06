"""Optional, cached geocoding for transaction city/state/country values.

Design rules:
- Stored latitude/longitude always win. This module is only used when names
  exist and coordinates do not.
- No coordinates are invented. If a reliable provider cannot resolve a place,
  the caller reports "Location unavailable".
- Every lookup, successful or not, is cached in ``geocode_cache`` so repeated
  API requests never re-query the provider for the same place.
- The provider is OpenStreetMap Nominatim, a free service. It is disabled by
  default and requires ``FINAUDIT_GEOCODING_ENABLED=true``. No paid API is
  required and the application works fully without network access.
"""
from __future__ import annotations

import math
import os
import threading
import time
from collections.abc import Iterable
from typing import Any

import httpx
from sqlalchemy.orm import Session

from .models import GeocodeCache

TRUTHY = {"1", "true", "yes", "on"}
NOMINATIM_URL = os.getenv(
    "FINAUDIT_GEOCODING_URL", "https://nominatim.openstreetmap.org/search"
)
USER_AGENT = os.getenv(
    "FINAUDIT_GEOCODING_USER_AGENT",
    "FinAuditAI/1.0 (local financial audit application)",
)
PROVIDER = "nominatim"

_rate_lock = threading.Lock()
_last_request_at = 0.0


def geocoding_enabled() -> bool:
    return os.getenv("FINAUDIT_GEOCODING_ENABLED", "").strip().lower() in TRUTHY


def _max_lookups() -> int:
    try:
        value = int(os.getenv("FINAUDIT_GEOCODING_MAX_LOOKUPS", "10"))
    except (TypeError, ValueError):
        value = 10
    return max(0, min(value, 100))


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def location_key(city: Any = None, state: Any = None, country: Any = None) -> str:
    city_key = _norm(city)
    state_key = _norm(state)
    country_key = _norm(country)
    if not (city_key or state_key or country_key):
        return ""
    return "|".join([city_key, state_key, country_key])


def _valid(latitude: Any, longitude: Any) -> tuple[float, float] | None:
    try:
        lat = float(latitude)
        lng = float(longitude)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(lat) and math.isfinite(lng)):
        return None
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None
    return lat, lng


def _request_json(
    city: str, state: str, country: str
) -> tuple[bool, dict[str, Any] | None]:
    """Return (request_succeeded, first_result).

    A successful request with no match returns ``(True, None)`` so the miss can
    be cached. A network/HTTP failure returns ``(False, None)`` so it can be
    retried on a later request instead of being stored as a permanent miss.
    """
    query = ", ".join(part for part in (city, state, country) if part)
    if not query:
        return True, None

    global _last_request_at
    with _rate_lock:
        elapsed = time.monotonic() - _last_request_at
        if elapsed < 1.05:
            time.sleep(1.05 - elapsed)
        _last_request_at = time.monotonic()
        try:
            response = httpx.get(
                NOMINATIM_URL,
                params={
                    "q": query,
                    "format": "jsonv2",
                    "limit": 1,
                    "addressdetails": 1,
                },
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                },
                timeout=6.0,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:
            return False, None
        if isinstance(payload, list) and payload:
            item = payload[0]
            if isinstance(item, dict):
                return True, item
        return True, None


def _looks_like_name(value: Any) -> bool:
    text = _norm(value)
    return bool(text) and text not in {
        "unknown",
        "n/a",
        "na",
        "none",
        "null",
        "location unavailable",
    }


def resolve_locations(
    db: Session,
    locations: Iterable[dict[str, Any]],
    *,
    allow_network: bool | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Resolve named locations to coordinates using cache then optional provider.

    ``locations`` items may contain ``city``, ``state`` and ``country``. The
    return value is keyed by :func:`location_key` and contains ``latitude``,
    ``longitude``, ``source`` (``geocode_cache`` or ``nominatim``) and
    ``display_name``.
    """
    pending: dict[str, dict[str, str]] = {}
    for item in locations:
        city = str(item.get("city") or "").strip()
        state = str(item.get("state") or "").strip()
        country = str(item.get("country") or "").strip()
        if not any(_looks_like_name(value) for value in (city, state, country)):
            continue
        key = location_key(city, state, country)
        if key and key not in pending:
            pending[key] = {"city": city, "state": state, "country": country}

    stats = {
        "enabled": geocoding_enabled(),
        "provider": PROVIDER if geocoding_enabled() else None,
        "requested": len(pending),
        "cache_hits": 0,
        "cache_misses": 0,
        "network_lookups": 0,
        "unresolved": 0,
        "errors": 0,
    }
    resolved: dict[str, dict[str, Any]] = {}
    if not pending:
        return resolved, stats

    cached = {
        row.query_key: row
        for row in db.query(GeocodeCache)
        .filter(GeocodeCache.query_key.in_(list(pending)))
        .all()
    }

    unresolved: list[str] = []
    for key in pending:
        row = cached.get(key)
        if row is None:
            stats["cache_misses"] += 1
            unresolved.append(key)
            continue
        coords = _valid(row.latitude, row.longitude)
        if row.status == "success" and coords:
            stats["cache_hits"] += 1
            resolved[key] = {
                "latitude": coords[0],
                "longitude": coords[1],
                "source": "geocode_cache",
                "display_name": row.display_name,
            }
        else:
            stats["cache_hits"] += 1
            stats["unresolved"] += 1

    if allow_network is None:
        allow_network = geocoding_enabled()
    if not allow_network or not unresolved:
        return resolved, stats

    budget = min(len(unresolved), _max_lookups())
    for key in unresolved[:budget]:
        item = pending[key]
        ok, payload = _request_json(item["city"], item["state"], item["country"])
        stats["network_lookups"] += 1
        if not ok:
            stats["errors"] += 1
            continue

        row = cached.get(key)
        if row is None:
            row = GeocodeCache(
                query_key=key,
                city=item["city"] or None,
                state=item["state"] or None,
                country=item["country"] or None,
                provider=PROVIDER,
            )
            db.add(row)
            cached[key] = row
        if not payload:
            row.status = "not_found"
            row.display_name = None
            row.latitude = None
            row.longitude = None
            stats["unresolved"] += 1
            continue

        coords = _valid(payload.get("lat"), payload.get("lon"))
        row.display_name = str(payload.get("display_name") or "")[:500] or None
        if coords:
            row.status = "success"
            row.latitude, row.longitude = coords
            resolved[key] = {
                "latitude": coords[0],
                "longitude": coords[1],
                "source": "nominatim",
                "display_name": row.display_name,
            }
        else:
            row.status = "not_found"
            row.latitude = None
            row.longitude = None
            stats["unresolved"] += 1

    if stats["network_lookups"]:
        db.commit()
    return resolved, stats


def geocoding_status() -> dict[str, Any]:
    return {
        "enabled": geocoding_enabled(),
        "provider": PROVIDER,
        "url": NOMINATIM_URL,
        "max_lookups_per_request": _max_lookups(),
        "notice": (
            "Geocoding is optional and disabled by default. Set "
            "FINAUDIT_GEOCODING_ENABLED=true to resolve stored city/state/country "
            "values with OpenStreetMap Nominatim. Results are cached in SQLite."
        ),
    }
