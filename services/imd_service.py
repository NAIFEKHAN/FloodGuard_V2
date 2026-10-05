"""IMD rainfall forecast integration for FloodGuard.

This module stays independent from the ML model and the rainfall-scenario engine.
It fetches the latest IMD forecast (when configured), validates the payload,
normalizes rainfall units, filters to the Nilgiris region, and maps the result to
village metadata when a matching record is available. If the live source is down or
misconfigured, it reuses the most recent valid cached forecast. If neither live nor
cached data are available, it returns a safe unavailable payload and the frontend
continues to use the existing manual rainfall Scenario Mode.
"""

from __future__ import annotations

import csv
import json
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable
from urllib import error, request

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CACHE_DIR = ROOT / "data" / "cache"
DEFAULT_CACHE_PATH = DEFAULT_CACHE_DIR / "imd_latest.json"
NILGIRIS_BOUNDS = {
    "lat_min": 11.0,
    "lat_max": 11.8,
    "lon_min": 76.0,
    "lon_max": 77.2,
}


def _get_settings() -> dict[str, Any]:
    cache_dir = os.getenv("IMD_CACHE_DIR", str(DEFAULT_CACHE_DIR)).strip()
    return {
        "forecast_url": os.getenv("IMD_FORECAST_URL", "").strip(),
        "rapid_forecast_url": os.getenv("IMD_RAPID_FORECAST_URL", "").strip(),
        "refresh_hours": max(1, int(os.getenv("IMD_REFRESH_HOURS", "24"))),
        "rapid_refresh_hours": max(1, int(os.getenv("IMD_RAPID_REFRESH_HOURS", "2"))),
        "cache_dir": Path(cache_dir).resolve() if cache_dir else DEFAULT_CACHE_DIR,
    }


def _read_village_master() -> list[dict[str, str]]:
    path = ROOT / "data" / "processed" / "nilgiris_villages.csv"
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _parse_datetime(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=UTC).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        text = text.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(text).astimezone(UTC).isoformat()
        except ValueError:
            try:
                return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC).isoformat()
            except ValueError:
                return None
    return None


def _find_first(value: dict[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        if key in value:
            return value[key]
    return None


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        match = re.search(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", text)
        if match:
            return float(match.group(0))
    return None


def _normalize_rainfall(value: Any, unit_hint: str | None = None) -> float | None:
    numeric = _coerce_float(value)
    if numeric is None:
        return None
    unit = (unit_hint or "").lower()
    if "cm" in unit:
        return numeric * 10.0
    if "m" in unit and "mm" not in unit:
        return numeric * 1000.0
    if "inch" in unit or 'in' in unit:
        return numeric * 25.4
    if "kg/m2" in unit or "kgm-2" in unit:
        return numeric
    return numeric


def _record_is_nilgiris(latitude: Any, longitude: Any, raw: dict[str, Any]) -> bool:
    lat = _coerce_float(latitude)
    lon = _coerce_float(longitude)
    if lat is not None and lon is not None:
        return (
            NILGIRIS_BOUNDS["lat_min"] <= lat <= NILGIRIS_BOUNDS["lat_max"]
            and NILGIRIS_BOUNDS["lon_min"] <= lon <= NILGIRIS_BOUNDS["lon_max"]
        )
    text = " ".join(
        str(raw.get(key, ""))
        for key in ("district", "region", "state", "location", "station_name", "village")
        if raw.get(key) is not None
    ).lower()
    return "nilgiris" in text


def _iter_payload_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("data", "features", "records", "entries", "items", "results", "observations", "forecast", "rainfall"):
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
            if isinstance(value, dict):
                return [_flatten_nested_dict(value)]
        if any(
            key in payload
            for key in ("rainfall_mm", "precipitation_mm", "rainfall", "value", "total_mm", "amount_mm")
        ):
            return [payload]
    return []


def _flatten_nested_dict(value: dict[str, Any]) -> dict[str, Any]:
    flattened: dict[str, Any] = {}
    for key, item in value.items():
        if isinstance(item, dict):
            flattened.update(item)
        else:
            flattened[key] = item
    return flattened


def _row_to_forecast_record(raw: dict[str, Any]) -> dict[str, Any] | None:
    latitude = _find_first(raw, ("latitude", "lat", "lat_deg", "latitude_deg", "y"))
    longitude = _find_first(raw, ("longitude", "lon", "lng", "long", "longitude_deg", "x"))
    if not _record_is_nilgiris(latitude, longitude, raw):
        return None

    rainfall_value = _find_first(
        raw,
        (
            "rainfall_mm",
            "precipitation_mm",
            "rainfall",
            "amount_mm",
            "total_mm",
            "precipitation",
            "value",
            "value_mm",
            "rainfall_amount_mm",
        ),
    )
    rainfall_unit = str(_find_first(raw, ("unit", "rainfall_unit", "units", "precipitation_unit")) or "")
    rainfall_mm = _normalize_rainfall(rainfall_value, rainfall_unit)
    if rainfall_mm is None:
        return None

    forecast_time = _find_first(raw, ("forecast_time", "valid_time", "time", "date", "timestamp", "issued_at", "validity"))
    village_name = _find_first(raw, ("village", "village_name", "station_name", "location_name", "name"))
    forecast_horizon = _find_first(raw, ("forecast_horizon_hours", "lead_hours", "lead_time_hours", "hours"))
    source = raw.get("source") or raw.get("provider") or "IMD"

    return {
        "source": source,
        "retrieved_at": _utc_now_iso(),
        "forecast_time": _parse_datetime(forecast_time) or _utc_now_iso(),
        "village": str(village_name) if village_name is not None else "Nilgiris forecast point",
        "latitude": float(latitude) if latitude is not None else None,
        "longitude": float(longitude) if longitude is not None else None,
        "rainfall_mm": round(float(rainfall_mm), 3),
        "forecast_horizon_hours": int(forecast_horizon) if isinstance(forecast_horizon, int) else (_coerce_float(forecast_horizon) if forecast_horizon is not None else None),
    }


def _map_records_to_villages(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    villages = _read_village_master()
    mapped: list[dict[str, Any]] = []
    for record in records:
        matched_village = None
        village_name = str(record.get("village", "")).strip().lower()
        if village_name:
            for village in villages:
                if village_name == str(village.get("village_name_en", "")).strip().lower():
                    matched_village = village
                    break
                if village_name in str(village.get("village_name_en", "")).strip().lower():
                    matched_village = village
                    break
        if matched_village is not None:
            record["village_lgd_code"] = matched_village.get("village_lgd_code")
            record["village_name_en"] = matched_village.get("village_name_en")
            record["taluk_name_en"] = matched_village.get("taluk_name_en")
            record["district_name_en"] = matched_village.get("district_name_en")
            record["matched_by"] = "village_name"
        else:
            record["village_lgd_code"] = None
            record["village_name_en"] = record.get("village")
            record["taluk_name_en"] = None
            record["district_name_en"] = "Nilgiris"
            record["matched_by"] = "nilgiris_grid"
        mapped.append(record)
    return mapped


def _cache_path() -> Path:
    settings = _get_settings()
    cache_dir = settings["cache_dir"]
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / "imd_latest.json"


def _read_cache() -> dict[str, Any] | None:
    cache_path = _cache_path()
    if not cache_path.exists():
        return None
    try:
        with cache_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if isinstance(payload, dict):
            return payload
    except (json.JSONDecodeError, OSError):
        return None
    return None


def _write_cache(payload: dict[str, Any]) -> None:
    cache_path = _cache_path()
    with cache_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)


def _is_cache_fresh(payload: dict[str, Any] | None, max_hours: int) -> bool:
    if not isinstance(payload, dict):
        return False
    retrieved_at = payload.get("retrieved_at")
    if not retrieved_at:
        return False
    dt = _parse_datetime(retrieved_at)
    if dt is None:
        return False
    age = datetime.now(UTC) - datetime.fromisoformat(dt)
    return age <= timedelta(hours=max_hours)


def _fetch_url_text(url: str) -> str:
    req = request.Request(url, headers={"User-Agent": "FloodGuard-IMD-Fetcher/1.0"})
    with request.urlopen(req, timeout=15) as response:
        return response.read().decode("utf-8")


def validate_imd_response(payload: Any) -> list[dict[str, Any]]:
    """Extract and normalize rainfall records from a supported IMD response payload."""
    rows: list[dict[str, Any]] = []
    for raw_item in _iter_payload_rows(payload):
        record = _row_to_forecast_record(raw_item)
        if record is not None:
            rows.append(record)
    return _map_records_to_villages(rows)


def fetch_imd_forecast(*, force: bool = False, provider: str = "IMD") -> dict[str, Any]:
    """Fetch the latest forecast from the configured IMD source.

    Returns a safe structured dict without crashing the application. If live data is
    unavailable, the most recent cached valid response is returned. If neither live nor
    cached data are available, the fallback is manual rainfall Scenario Mode.
    """
    settings = _get_settings()
    refresh_hours = settings["refresh_hours"]
    cache_payload = _read_cache()

    if not force and cache_payload and _is_cache_fresh(cache_payload, refresh_hours):
        cache_payload["status"] = "cached"
        cache_payload["source"] = provider
        cache_payload["warning"] = "Using the most recent valid IMD forecast cache; live IMD fetch skipped because it is still within the configured refresh window."
        cache_payload["fallback_mode"] = "scenario"
        return cache_payload

    if not settings["forecast_url"]:
        if cache_payload and _is_cache_fresh(cache_payload, refresh_hours):
            cache_payload["status"] = "cached"
            cache_payload["source"] = provider
            cache_payload["warning"] = "IMD forecast URL is not configured; using the most recent valid cache."
            cache_payload["fallback_mode"] = "scenario"
            return cache_payload
        return {
            "status": "unavailable",
            "source": provider,
            "retrieved_at": _utc_now_iso(),
            "forecast_time": None,
            "records": [],
            "warning": "IMD forecast is unavailable and no valid cache exists. FloodGuard remains in the existing manual rainfall Scenario Mode.",
            "fallback_mode": "scenario",
        }

    try:
        payload_text = _fetch_url_text(settings["forecast_url"])
        data = json.loads(payload_text)
    except (ValueError, error.URLError, error.HTTPError, UnicodeDecodeError):
        if cache_payload and _is_cache_fresh(cache_payload, refresh_hours):
            cache_payload["status"] = "cached"
            cache_payload["source"] = provider
            cache_payload["warning"] = "Live IMD source is unavailable; using the most recent valid cached forecast."
            cache_payload["fallback_mode"] = "scenario"
            return cache_payload
        return {
            "status": "unavailable",
            "source": provider,
            "retrieved_at": _utc_now_iso(),
            "forecast_time": None,
            "records": [],
            "warning": "Live IMD source is unavailable and no valid cache exists. FloodGuard continues with the manual rainfall Scenario Mode.",
            "fallback_mode": "scenario",
        }

    rows = validate_imd_response(data)
    if not rows:
        if cache_payload and _is_cache_fresh(cache_payload, refresh_hours):
            cache_payload["status"] = "cached"
            cache_payload["source"] = provider
            cache_payload["warning"] = "Live IMD response was empty or outside the Nilgiris bounds; using the most recent valid cache."
            cache_payload["fallback_mode"] = "scenario"
            return cache_payload
        return {
            "status": "unavailable",
            "source": provider,
            "retrieved_at": _utc_now_iso(),
            "forecast_time": None,
            "records": [],
            "warning": "No valid Nilgiris rainfall forecast rows were returned by the IMD source. FloodGuard continues with the manual rainfall Scenario Mode.",
            "fallback_mode": "scenario",
        }

    result = {
        "status": "available",
        "source": provider,
        "retrieved_at": _utc_now_iso(),
        "forecast_time": rows[0].get("forecast_time"),
        "record_count": len(rows),
        "records": rows,
        "warning": "IMD rainfall forecast is available for Nilgiris and is being used as the latest forecast input.",
        "fallback_mode": "scenario",
    }
    _write_cache(result)
    return result


if __name__ == "__main__":
    print(json.dumps(fetch_imd_forecast(), indent=2, ensure_ascii=False))
