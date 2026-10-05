"""Village-level precipitation forecasts from the public Open-Meteo API."""

from __future__ import annotations

import csv
import http.client
import json
import logging
import math
import os
import socket
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib import error, parse, request
from zoneinfo import ZoneInfo


logger = logging.getLogger(__name__)
if not logger.handlers:
    weather_log_handler = logging.StreamHandler()
    weather_log_handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(weather_log_handler)
logger.setLevel(logging.INFO)
logger.propagate = False
ROOT = Path(__file__).resolve().parents[3]
SOURCE = "Open-Meteo"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
TIME_ZONE = "Asia/Kolkata"
LOCAL_TIME_ZONE = ZoneInfo(TIME_ZONE)
FORECAST_HOURS = 24
DEFAULT_CACHE_MINUTES = 60
CACHE_PATH = ROOT / "data" / "cache" / "open_meteo_latest.json"
VILLAGE_FEATURES_PATH = ROOT / "data" / "processed" / "village_time_features.csv"
EXPECTED_VILLAGE_COUNT = 40
REQUEST_TIMEOUT_SECONDS = 15
MAX_REQUEST_ATTEMPTS = 3
MAX_RETRY_DELAY_SECONDS = 8


def _cache_path() -> Path:
    configured = os.getenv("WEATHER_CACHE_PATH", "").strip()
    return Path(configured).resolve() if configured else CACHE_PATH


def _cache_ttl_minutes() -> int:
    raw = os.getenv("WEATHER_CACHE_TTL_MINUTES", str(DEFAULT_CACHE_MINUTES))
    try:
        minutes = int(raw)
    except ValueError:
        logger.error("WEATHER_CACHE_TTL_MINUTES must be a positive integer; using default.")
        return DEFAULT_CACHE_MINUTES
    if minutes < 1:
        logger.error("WEATHER_CACHE_TTL_MINUTES must be a positive integer; using default.")
        return DEFAULT_CACHE_MINUTES
    return minutes


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _read_village_coordinates() -> list[dict[str, Any]]:
    """Load existing per-model-village historical IMD grid assignments.

    These are the only village-linked coordinates available in the current
    processed feature table. They represent the nearest historical IMD grid
    assigned to an exact-ID polygon representative point, not village centroids.
    """
    villages: dict[str, dict[str, Any]] = {}
    with VILLAGE_FEATURES_PATH.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            code = row.get("village_lgd_code", "").strip()
            if not code:
                continue
            try:
                latitude = float(row["imd_grid_latitude"])
                longitude = float(row["imd_grid_longitude"])
            except (KeyError, TypeError, ValueError) as exc:
                logger.warning("[WEATHER] Skipping village %s without usable coordinates.", code)
                continue
            if (
                not math.isfinite(latitude)
                or not math.isfinite(longitude)
                or not -90 <= latitude <= 90
                or not -180 <= longitude <= 180
            ):
                logger.warning("[WEATHER] Skipping village %s with invalid coordinates.", code)
                continue
            village = {
                "village_lgd_code": code,
                "village_name_en": row.get("village_name_en", ""),
                "latitude": latitude,
                "longitude": longitude,
                "coordinate_source": (
                    "Existing imd_grid_latitude/imd_grid_longitude assignment in "
                    "data/processed/village_time_features.csv"
                ),
            }
            previous = villages.get(code)
            if previous is not None and previous != village:
                raise ValueError(f"Village {code} has inconsistent coordinate assignments.")
            villages[code] = village

    if len(villages) != EXPECTED_VILLAGE_COUNT:
        raise ValueError(
            f"Expected {EXPECTED_VILLAGE_COUNT} model-village coordinates; found {len(villages)}."
        )
    return [villages[code] for code in sorted(villages)]


def _parse_local_datetime(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Open-Meteo returned a missing or invalid timestamp.")
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"Open-Meteo returned an invalid timestamp: {value!r}.") from exc
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=LOCAL_TIME_ZONE)
    return timestamp.astimezone(LOCAL_TIME_ZONE)


def _numeric_or_none(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _forecast_url(latitude: float, longitude: float) -> str:
    query = parse.urlencode(
        {
            "latitude": f"{latitude:.6f}",
            "longitude": f"{longitude:.6f}",
            "hourly": "rain,precipitation,precipitation_probability",
            "current": "precipitation",
            "timezone": TIME_ZONE,
            "forecast_days": 2,
        }
    )
    return f"{FORECAST_URL}?{query}"


def _fetch_coordinate_forecast(
    latitude: float, longitude: float
) -> dict[str, Any]:
    url = _forecast_url(latitude, longitude)
    req = request.Request(url, headers={"User-Agent": "FloodGuard/1.0"})
    logger.info(
        "[WEATHER] Village coordinates: latitude=%.6f longitude=%.6f",
        latitude,
        longitude,
    )
    for attempt in range(MAX_REQUEST_ATTEMPTS):
        try:
            with request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                logger.info("[WEATHER] Open-Meteo HTTP status: %s", response.status)
                if response.status != 200:
                    raise OSError(f"Open-Meteo returned HTTP {response.status}.")
                raw_response = response.read().decode("utf-8")
            break
        except error.HTTPError as exc:
            if exc.code != 429 or attempt == MAX_REQUEST_ATTEMPTS - 1:
                logger.error("[WEATHER] Open-Meteo HTTP status: %s", exc.code)
                raise
            retry_after = exc.headers.get("Retry-After")
            try:
                delay = float(retry_after) if retry_after else 2**attempt
            except ValueError:
                delay = 2**attempt
            delay = min(max(delay, 0.0), MAX_RETRY_DELAY_SECONDS)
            logger.warning(
                "[WEATHER] Open-Meteo HTTP 429; retry %s/%s in %.1f seconds",
                attempt + 1,
                MAX_REQUEST_ATTEMPTS - 1,
                delay,
            )
            time.sleep(delay)
    else:
        raise OSError("Open-Meteo request ended without a response.")

    payload = json.loads(raw_response)
    if not isinstance(payload, dict):
        raise ValueError("Open-Meteo response was not a JSON object.")
    if payload.get("timezone") != TIME_ZONE:
        raise ValueError("Open-Meteo response timezone did not match Asia/Kolkata.")

    hourly = payload.get("hourly")
    if not isinstance(hourly, dict):
        raise ValueError("Open-Meteo response has no hourly forecast.")
    times = hourly.get("time")
    precipitation = hourly.get("precipitation")
    rain = hourly.get("rain")
    probability = hourly.get("precipitation_probability")
    if not all(isinstance(values, list) for values in (times, precipitation, rain, probability)):
        raise ValueError("Open-Meteo response is missing hourly rain or precipitation fields.")
    if not (len(times) == len(precipitation) == len(rain) == len(probability)):
        raise ValueError("Open-Meteo returned hourly arrays with inconsistent lengths.")
    logger.info(
        "[WEATHER] Hourly values received: %s records at latitude=%.6f longitude=%.6f",
        len(times),
        latitude,
        longitude,
    )

    current = payload.get("current")
    if not isinstance(current, dict) or not isinstance(current.get("time"), str):
        raise ValueError("Open-Meteo response has no usable current timestamp.")
    current_time = _parse_local_datetime(current["time"])
    period_end = current_time + timedelta(hours=FORECAST_HOURS)
    records = []
    for timestamp_raw, total_raw, rain_raw, probability_raw in zip(
        times, precipitation, rain, probability
    ):
        timestamp = _parse_local_datetime(timestamp_raw)
        if timestamp <= current_time or timestamp > period_end:
            continue
        total_amount = _numeric_or_none(total_raw)
        rain_amount = _numeric_or_none(rain_raw)
        if total_amount is None:
            total_amount = rain_amount
        if total_amount is None or total_amount < 0:
            raise ValueError(
                f"Open-Meteo has no valid hourly precipitation amount for {timestamp.isoformat()}."
            )
        records.append(
            {
                "forecast_time": timestamp.isoformat(),
                "rain_mm": rain_amount,
                "precipitation_mm": total_amount,
                "precipitation_probability_percent": _numeric_or_none(
                    probability_raw
                ),
            }
        )
    if len(records) != FORECAST_HOURS:
        raise ValueError(
            f"Open-Meteo returned {len(records)} valid hours; expected {FORECAST_HOURS} "
            "for the next 24 hours."
        )
    records.sort(key=lambda record: record["forecast_time"])
    for previous, following in zip(records, records[1:]):
        if (
            _parse_local_datetime(following["forecast_time"])
            - _parse_local_datetime(previous["forecast_time"])
        ) != timedelta(hours=1):
            raise ValueError("Open-Meteo hourly forecast has a gap in the next-24-hour window.")

    current_precipitation = _numeric_or_none(current.get("precipitation"))
    next_24h_rainfall_mm = round(
        sum(record["precipitation_mm"] for record in records), 3
    )
    logger.info(
        "[WEATHER] Next 24h rainfall: %.3f mm at latitude=%.6f longitude=%.6f",
        next_24h_rainfall_mm,
        latitude,
        longitude,
    )
    return {
        "forecast": records,
        "next_24h_rainfall_mm": next_24h_rainfall_mm,
        "forecast_period_start": records[0]["forecast_time"],
        "forecast_period_end": records[-1]["forecast_time"],
        "current_precipitation_mm": current_precipitation,
        "current_time": current_time.isoformat(),
    }


def _fetch_live_forecasts() -> dict[str, Any]:
    villages = _read_village_coordinates()
    coordinate_groups: dict[tuple[float, float], list[dict[str, Any]]] = {}
    for village in villages:
        key = (village["latitude"], village["longitude"])
        coordinate_groups.setdefault(key, []).append(village)

    coordinate_forecasts: dict[tuple[float, float], dict[str, Any]] = {}
    for locations in coordinate_groups.values():
        for village in locations:
            logger.info(
                "[WEATHER] Requesting Open-Meteo forecast for %s",
                village["village_name_en"],
            )
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {
            executor.submit(_fetch_coordinate_forecast, latitude, longitude): (
                latitude,
                longitude,
            )
            for latitude, longitude in coordinate_groups
        }
        for future in as_completed(futures):
            coordinate_forecasts[futures[future]] = future.result()

    retrieved_at = _now_utc().isoformat()
    forecast_villages: list[dict[str, Any]] = []
    for coordinate, locations in coordinate_groups.items():
        forecast = coordinate_forecasts[coordinate]
        for village in locations:
            logger.info(
                "[WEATHER] Live forecast received: %.1f mm / next 24h for %s",
                forecast["next_24h_rainfall_mm"],
                village["village_name_en"],
            )
            forecast_villages.append(
                {
                    **village,
                    **forecast,
                    "source": SOURCE,
                    "updated_at": retrieved_at,
                    "status": "live",
                    "forecast_status": "LIVE",
                    "chosen_feature": "next_24h_rainfall_mm",
                    "chosen_feature_value_mm": forecast["next_24h_rainfall_mm"],
                }
            )
    forecast_villages.sort(key=lambda row: row["village_lgd_code"])
    return {
        "source": SOURCE,
        "status": "live",
        "available": True,
        "forecast_status": "LIVE",
        "retrieved_at": retrieved_at,
        "updated_at": retrieved_at,
        "timezone": TIME_ZONE,
        "forecast_period_hours": FORECAST_HOURS,
        "request_count": len(coordinate_groups),
        "record_count": len(forecast_villages),
        "villages": forecast_villages,
    }


def _valid_cache(payload: Any) -> bool:
    if (
        not isinstance(payload, dict)
        or payload.get("source") != SOURCE
        or not isinstance(payload.get("villages"), list)
        or len(payload["villages"]) != EXPECTED_VILLAGE_COUNT
    ):
        return False
    codes = []
    for village in payload["villages"]:
        if not isinstance(village, dict):
            return False
        code = str(village.get("village_lgd_code", ""))
        amount = _numeric_or_none(village.get("next_24h_rainfall_mm"))
        values = village.get("forecast")
        if (
            not code
            or amount is None
            or amount < 0
            or not isinstance(village.get("latitude"), (int, float))
            or not isinstance(village.get("longitude"), (int, float))
            or not isinstance(values, list)
            or len(values) != FORECAST_HOURS
            or any(
                not isinstance(record, dict)
                or _numeric_or_none(record.get("precipitation_mm")) is None
                or not isinstance(record.get("forecast_time"), str)
                for record in values
            )
        ):
            return False
        codes.append(code)
    retrieved_at = payload.get("retrieved_at")
    if not isinstance(retrieved_at, str):
        return False
    try:
        timestamp = datetime.fromisoformat(retrieved_at)
    except ValueError:
        return False
    return timestamp.tzinfo is not None and len(set(codes)) == EXPECTED_VILLAGE_COUNT


def _read_cache() -> dict[str, Any] | None:
    try:
        with _cache_path().open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError):
        logger.exception("[WEATHER] Could not read cached forecast.")
        return None
    if not _valid_cache(payload):
        logger.warning("[WEATHER] Ignoring invalid or incomplete Open-Meteo cache.")
        return None
    return payload


def _cache_is_fresh(payload: dict[str, Any]) -> bool:
    retrieved_at = datetime.fromisoformat(payload["retrieved_at"]).astimezone(UTC)
    age = _now_utc() - retrieved_at
    return timedelta(0) <= age <= timedelta(minutes=_cache_ttl_minutes())


def _write_cache(payload: dict[str, Any]) -> None:
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(".tmp")
    with temporary_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    temporary_path.replace(path)
    logger.info("[WEATHER] Cache updated")


def _unavailable_payload() -> dict[str, Any]:
    return {
        "source": SOURCE,
        "status": "unavailable",
        "available": False,
        "forecast_status": "UNAVAILABLE",
        "retrieved_at": _now_utc().isoformat(),
        "updated_at": None,
        "timezone": TIME_ZONE,
        "forecast_period_hours": FORECAST_HOURS,
        "record_count": 0,
        "villages": [],
        "warning": "Live forecast unavailable; Scenario Mode remains active.",
    }


def fetch_forecast(*, force_refresh: bool = False) -> dict[str, Any]:
    """Return live Open-Meteo forecasts, falling back to cached data on failure."""
    cache_minutes = _cache_ttl_minutes()
    cached = _read_cache()
    if cached and not force_refresh and _cache_is_fresh(cached):
        logger.info("[WEATHER] Using cached forecast")
        payload = {
            **cached,
            "villages": [
                {**village, "status": "cached", "forecast_status": "CACHED"}
                for village in cached["villages"]
            ],
            "status": "cached",
            "forecast_status": "CACHED",
            "cache_status": "fresh",
        }
        logger.info("[WEATHER] Returning status: %s", payload["status"])
        return payload

    try:
        payload = _fetch_live_forecasts()
    except (
        OSError,
        TimeoutError,
        socket.timeout,
        http.client.HTTPException,
        error.URLError,
        json.JSONDecodeError,
        UnicodeDecodeError,
        ValueError,
    ) as exc:
        logger.warning("[WEATHER] Open-Meteo unavailable: %s", exc)
        if cached:
            logger.info("[WEATHER] Using cached forecast")
            payload = {
                **cached,
                "villages": [
                    {**village, "status": "cached", "forecast_status": "CACHED"}
                    for village in cached["villages"]
                ],
                "status": "cached",
                "forecast_status": "CACHED",
                "cache_status": "stale_fallback",
                "warning": "Open-Meteo unavailable; using the most recent cached forecast.",
            }
            logger.info("[WEATHER] Returning status: %s", payload["status"])
            return payload
        payload = _unavailable_payload()
        logger.info("[WEATHER] Returning status: %s", payload["status"])
        return payload

    try:
        _write_cache(payload)
    except OSError:
        logger.exception("[WEATHER] Live forecast received but cache could not be updated.")
        payload["cache_status"] = "not_saved"
    else:
        payload["cache_status"] = "refreshed"
    logger.info("[WEATHER] Returning status: %s", payload["status"])
    return payload


def get_forecast_status() -> dict[str, Any]:
    """Return cache/provider availability without making a network request."""
    cached = _read_cache()
    fresh = bool(cached and _cache_is_fresh(cached))
    return {
        "configured": VILLAGE_FEATURES_PATH.is_file(),
        "available": bool(cached),
        "last_updated": cached.get("updated_at") if cached else None,
        "cache_available": bool(cached),
        "cache_fresh": fresh,
        "mode": "auto" if cached else "scenario",
        "source": SOURCE,
        "cache_ttl_minutes": _cache_ttl_minutes(),
    }
