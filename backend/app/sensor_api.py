"""Registration, authenticated ingestion, and read-only ESP32 sensor APIs."""

from __future__ import annotations

import csv
import hmac
import os
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.app import sensor_store
from backend.app.data_catalog import source_provenance


ROOT = Path(__file__).resolve().parents[2]
VILLAGE_MASTER = ROOT / "data" / "processed" / "nilgiris_villages.csv"
MAX_SENSOR_REQUEST_BYTES = 4096
router = APIRouter(prefix="/api", tags=["sensors"])


class SensorRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sensor_id: str = Field(min_length=1, max_length=64)
    sensor_name: str = Field(min_length=1, max_length=120)
    village_lgd_code: str = Field(min_length=1, max_length=32)
    latitude: float | None = Field(default=None, ge=-90, le=90, allow_inf_nan=False)
    longitude: float | None = Field(default=None, ge=-180, le=180, allow_inf_nan=False)
    sensor_type: Literal["soil_moisture"] = "soil_moisture"
    installed_at: datetime | None = None
    is_test: bool = False

    @field_validator("sensor_id", "sensor_name", "village_lgd_code")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


class SensorReading(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sensor_id: str = Field(min_length=1, max_length=64)
    village_lgd_code: str = Field(min_length=1, max_length=32)
    soil_moisture_percent: float = Field(
        strict=True, ge=0, le=100, allow_inf_nan=False
    )
    raw_value: int | None = Field(default=None, ge=0, le=10_000_000)
    recorded_at: datetime | None = None
    is_test: bool = False

    @field_validator("sensor_id", "village_lgd_code")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


@lru_cache(maxsize=1)
def _village_codes() -> dict[str, dict[str, str]]:
    with VILLAGE_MASTER.open("r", encoding="utf-8-sig", newline="") as handle:
        return {
            row["village_lgd_code"]: row
            for row in csv.DictReader(handle)
            if row.get("village_lgd_code")
        }


def _require_sensor_key(x_sensor_key: str | None = Header(default=None, alias="X-Sensor-Key")) -> None:
    configured_key = os.environ.get("SENSOR_API_KEY")
    if not configured_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Sensor writes are disabled until SENSOR_API_KEY is configured.",
        )
    if not x_sensor_key or not hmac.compare_digest(
        x_sensor_key.encode("utf-8"), configured_key.encode("utf-8")
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A valid X-Sensor-Key header is required.",
            headers={"WWW-Authenticate": "Sensor-Key"},
        )


def _village_or_404(code: str) -> dict[str, str]:
    village = _village_codes().get(code)
    if village is None:
        raise HTTPException(status_code=404, detail="Village LGD code was not found.")
    return village


def _with_village(sensor: dict[str, object]) -> dict[str, object]:
    village = _village_or_404(str(sensor["village_lgd_code"]))
    return {
        **sensor,
        "village": {
            "village_lgd_code": village["village_lgd_code"],
            "village_name_en": village["village_name_en"],
            "taluk_name_en": village["taluk_name_en"],
        },
    }


def _as_utc(value: datetime | None) -> str:
    timestamp = value or datetime.now(UTC)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return timestamp.astimezone(UTC).isoformat()


@router.post("/sensors", status_code=status.HTTP_201_CREATED)
def register_sensor(
    payload: SensorRegistration,
    _: None = Depends(_require_sensor_key),
) -> dict[str, object]:
    village = _village_or_404(payload.village_lgd_code)
    try:
        sensor = sensor_store.register_sensor(
            sensor_id=payload.sensor_id,
            sensor_name=payload.sensor_name,
            village_lgd_code=village["village_lgd_code"],
            latitude=payload.latitude,
            longitude=payload.longitude,
            sensor_type=payload.sensor_type,
            installed_at=_as_utc(payload.installed_at) if payload.installed_at else None,
            is_test=payload.is_test,
        )
    except sensor_store.DuplicateSensorError as error:
        raise HTTPException(status_code=409, detail="Sensor ID is already registered.") from error
    return _with_village(sensor)


@router.post("/sensors/readings", status_code=status.HTTP_202_ACCEPTED)
def ingest_sensor_reading(
    payload: SensorReading,
    _: None = Depends(_require_sensor_key),
) -> dict[str, object]:
    received_at = datetime.now(UTC)
    try:
        result = sensor_store.insert_reading(
            sensor_id=payload.sensor_id,
            village_lgd_code=payload.village_lgd_code,
            soil_moisture_percent=payload.soil_moisture_percent,
            raw_value=payload.raw_value,
            recorded_at=(
                _as_utc(payload.recorded_at)
                if payload.recorded_at
                else received_at.isoformat()
            ),
            received_at=received_at.isoformat(),
            is_test=payload.is_test,
        )
    except sensor_store.SensorNotFoundError as error:
        raise HTTPException(status_code=404, detail="Sensor ID is not registered.") from error
    except sensor_store.VillageMismatchError as error:
        raise HTTPException(
            status_code=409,
            detail="Reading village does not match the registered sensor.",
        ) from error
    except sensor_store.TestReadingNotAllowed as error:
        raise HTTPException(
            status_code=409,
            detail="Test status must match the registered sensor's test-data status.",
        ) from error
    latest = result["latest"]
    return {
        "status": "accepted",
        "sensor_id": payload.sensor_id,
        "soil_moisture_percent": payload.soil_moisture_percent,
        "recorded_at": latest["recorded_at"],
        "received_at": latest["received_at"],
        "sensor_status": result["status"],
        "is_test": latest["is_test"],
    }


@router.get("/sensors")
def get_sensors() -> dict[str, object]:
    records = [_with_village(sensor) for sensor in sensor_store.list_sensors()]
    for sensor in records:
        latest = sensor["latest"]
        sensor["soil_moisture_percent"] = (
            latest["soil_moisture_percent"] if latest else None
        )
        sensor["recorded_at"] = latest["recorded_at"] if latest else None
    return {"record_count": len(records), "records": records}


@router.get("/sensors/{sensor_id}/readings")
def get_sensor_readings(
    sensor_id: str,
    limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, object]:
    sensor = sensor_store.get_sensor(sensor_id)
    if sensor is None:
        raise HTTPException(status_code=404, detail="Sensor ID was not found.")
    readings = sensor_store.readings_for_sensor(sensor_id, limit)
    return {
        "sensor_id": sensor_id,
        "record_count": len(readings),
        "records": readings,
    }


@router.get("/sensors/{sensor_id}")
def get_sensor(sensor_id: str) -> dict[str, object]:
    sensor = sensor_store.get_sensor(sensor_id)
    if sensor is None:
        raise HTTPException(status_code=404, detail="Sensor ID was not found.")
    return _with_village(sensor)


@router.get("/villages/{village_code}/soil-moisture")
def get_village_soil_moisture(village_code: str) -> dict[str, object]:
    village = _village_or_404(village_code)
    code = village["village_lgd_code"]
    sensors = [
        _with_village(sensor)
        for sensor in sensor_store.sensors_for_village(code)
    ]
    latest = sensor_store.latest_village_reading(code)
    status_value = "available" if latest else "unavailable"
    provenance = source_provenance(
        "soil_moisture",
        unit="%",
        observed_at=str(latest["recorded_at"]) if latest else None,
        sensor_id=str(latest["sensor_id"]) if latest else None,
        recorded_at=str(latest["recorded_at"]) if latest else None,
        received_at=str(latest["received_at"]) if latest else None,
        is_test=bool(latest["is_test"]) if latest else None,
        status=status_value,
    )
    return {
        "status": status_value,
        "data_type": "test" if latest and latest["is_test"] else "observed",
        "village_lgd_code": code,
        "latest": latest,
        "data": latest,
        "sensors": sensors,
        "provenance": provenance,
    }
