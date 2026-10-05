"""Read-only aggregation of currently available data for one canonical village."""

from __future__ import annotations

import csv
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from backend.app.data_catalog import (
    DATA,
    DATA_SOURCE_BY_ID,
    MODEL_METADATA,
    Availability,
    source_provenance,
    source_status,
)
from backend.app.hydrology import get_village_hydrology
from backend.app.sensor_store import latest_village_reading, sensors_for_village


logger = logging.getLogger(__name__)

CONTEXT_DATASETS = {
    "village_master": DATA / "processed/nilgiris_villages.csv",
    "model_susceptibility": DATA / "processed/ml_village_susceptibility_scores.csv",
    "srtm_terrain": DATA / "processed/terrain_features_villages.csv",
    "historical_rainfall_villages": DATA / "processed/village_time_features.csv",
    "historical_event_linkage": DATA / "processed/event_village_linkage.csv",
}


@lru_cache(maxsize=8)
def _read_csv(path: Path) -> tuple[dict[str, str], ...]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return tuple(csv.DictReader(handle))


def _rows(source_id: str) -> tuple[dict[str, str], ...]:
    return _read_csv(CONTEXT_DATASETS[source_id])


def _number(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _optional_dataset(source_id: str) -> tuple[tuple[dict[str, str], ...] | None, str]:
    try:
        return _rows(source_id), Availability.AVAILABLE.value
    except FileNotFoundError:
        logger.exception("Optional village-context source %s is missing", source_id)
        return None, Availability.UNAVAILABLE.value


def _provenance(
    source_id: str,
    *,
    status: str | None = None,
    unit: str | None = None,
    observed_at: str | None = None,
) -> dict[str, object]:
    result = source_provenance(
        source_id,
        unit=unit,
        observed_at=observed_at,
    )
    if status is not None:
        result["status"] = status
    return result


def _tier(score: float) -> str:
    from pipeline.run_rainfall_scenario import classify_scenario_tier

    return classify_scenario_tier(score).lower()


def _linked_event(row: dict[str, str]) -> dict[str, object]:
    return {
        "event_id": row.get("inventory_serial") or None,
        "slide_no": row.get("slide_no") or None,
        "location": row.get("location_description") or row.get("slide_name") or None,
        "latitude": _number(row.get("latitude")),
        "longitude": _number(row.get("longitude")),
        "date": row.get("reported_history_date") or None,
        "history_raw": row.get("history_raw") or None,
        "material": row.get("material_involved") or None,
        "movement_type": row.get("movement_type") or None,
        "source": {
            "file": row.get("source_pdf_path") or None,
            "page": row.get("source_pdf_page") or None,
            "table": row.get("source_table_label") or None,
        },
        "linkage_status": row.get("spatial_linkage_status") or None,
    }


def _scenario_context(scenario: str, multiplier: float | None) -> dict[str, object]:
    from pipeline.run_rainfall_scenario import PRESET_SCENARIOS

    if scenario == "custom":
        if multiplier is None:
            raise HTTPException(
                status_code=422,
                detail="A multiplier is required for the custom scenario.",
            )
        scenario_name = f"Custom ({multiplier:.2f}x Multiplier)"
        factor = multiplier
    else:
        if scenario not in PRESET_SCENARIOS:
            raise HTTPException(status_code=422, detail="Unknown rainfall scenario.")
        scenario_name = str(PRESET_SCENARIOS[scenario]["name"])
        factor = (
            multiplier
            if multiplier is not None
            else float(PRESET_SCENARIOS[scenario]["multiplier"])
        )
        if multiplier is not None:
            scenario_name = f"Custom ({multiplier:.2f}x Multiplier)"
            scenario = "custom"

    return {
        "key": scenario,
        "name": scenario_name,
        "multiplier": factor,
        "unit": "×",
        "mode": "scenario",
        "data_type": "simulated",
        "status": Availability.AVAILABLE.value,
        "provenance": _provenance(
            "rainfall_scenario",
            unit="×",
        ),
    }


def get_village_context(
    village_code: str,
    *,
    scenario: str = "baseline",
    multiplier: float | None = None,
    warning_mode: str = "scenario",
) -> dict[str, Any]:
    """Return independently sourced village data without inferring missing values."""
    code = village_code.strip()
    administrative_rows = _rows("village_master")
    village = next(
        (row for row in administrative_rows if row["village_lgd_code"] == code),
        None,
    )
    if village is None:
        raise HTTPException(status_code=404, detail="Village LGD code was not found.")

    scenario_context = _scenario_context(scenario, multiplier)
    score_rows, risk_status = _optional_dataset("model_susceptibility")
    score = None
    model_record = None
    if score_rows is not None:
        model_record = next(
            (row for row in score_rows if row["village_lgd_code"] == code),
            None,
        )
        if model_record is None:
            risk_status = Availability.UNAVAILABLE.value
        else:
            score = _number(model_record["ml_susceptibility_0_100"])

    terrain_rows, terrain_status = _optional_dataset("srtm_terrain")
    terrain_record = None
    if terrain_rows is not None:
        terrain_record = next(
            (row for row in terrain_rows if row["village_lgd_code"] == code),
            None,
        )
        if terrain_record is None:
            terrain_status = Availability.UNAVAILABLE.value

    rainfall_rows, rainfall_status = _optional_dataset(
        "historical_rainfall_villages"
    )
    rainfall_record = None
    if rainfall_rows is not None:
        matching_rainfall = (
            row for row in rainfall_rows if row["village_lgd_code"] == code
        )
        rainfall_record = max(
            matching_rainfall,
            key=lambda row: row["date"],
            default=None,
        )
        if rainfall_record is None:
            rainfall_status = Availability.UNAVAILABLE.value

    event_rows, event_status = _optional_dataset("historical_event_linkage")
    linked_events: list[dict[str, object]] = []
    if event_rows is not None:
        linked_events = [
            _linked_event(row)
            for row in event_rows
            if row.get("matched_village_lgd_code") == code
            and row.get("spatial_linkage_status") == "EXACT_POLYGON_MATCH"
        ]

    source_metadata = {
        "code": code,
        "name": village["village_name_en"],
        "taluk": village["taluk_name_en"],
    }
    model = {
        **MODEL_METADATA,
        "data_type": "modeled",
        "provenance": _provenance(
            "model_susceptibility",
            status=risk_status,
            unit="relative score: 0-100",
        ),
    }
    hydrology = get_village_hydrology(code)

    weather_source = DATA_SOURCE_BY_ID["current_weather"]
    registered_sensors = sensors_for_village(code)
    soil_reading = latest_village_reading(code)
    soil_status = (
        Availability.AVAILABLE.value if soil_reading else Availability.UNAVAILABLE.value
    )
    soil_provenance = _provenance(
        "soil_moisture",
        status=soil_status,
        unit="%",
        observed_at=str(soil_reading["recorded_at"]) if soil_reading else None,
    )
    if soil_reading:
        soil_provenance.update(
            {
                "sensor_id": soil_reading["sensor_id"],
                "recorded_at": soil_reading["recorded_at"],
                "received_at": soil_reading["received_at"],
                "is_test": soil_reading["is_test"],
            }
        )
    soil_moisture = {
        "status": soil_status,
        "data_type": (
            "test" if soil_reading and soil_reading["is_test"] else "observed"
        ),
        "value": (
            soil_reading["soil_moisture_percent"] if soil_reading else None
        ),
        "unit": "%",
        "sensor_id": soil_reading["sensor_id"] if soil_reading else None,
        "sensor_name": soil_reading["sensor_name"] if soil_reading else None,
        "recorded_at": soil_reading["recorded_at"] if soil_reading else None,
        "received_at": soil_reading["received_at"] if soil_reading else None,
        "freshness": soil_reading["freshness"] if soil_reading else None,
        "age_seconds": soil_reading["age_seconds"] if soil_reading else None,
        "is_test": soil_reading["is_test"] if soil_reading else False,
        "provenance": soil_provenance,
    }
    current_conditions = {
        **MODEL_METADATA["current_conditions"],
        "weather": {"status": Availability.UNAVAILABLE.value},
        "rainfall": {"status": rainfall_status, "data_type": "historical"},
        "soil_moisture": {
            "status": soil_status,
            "data_type": soil_moisture["data_type"],
            "value": soil_moisture["value"],
            "unit": "%",
            "freshness": soil_moisture["freshness"],
            "is_test": soil_moisture["is_test"],
        },
    }
    from backend.app.warning_api import evaluate_village_warning

    warning = evaluate_village_warning(
        code,
        mode=warning_mode,
        scenario=scenario,
        multiplier=multiplier,
    )
    return {
        "village": {
            "code": code,
            "village_code": code,
            "village_lgd_code": code,
            "source_village_code": village["village_code"],
            "name": village["village_name_en"],
            "taluk": village["taluk_name_en"],
            "district": village["district_name_en"],
            "district_lgd_code": village["district_lgd_code"],
            "taluk_lgd_code": village["taluk_lgd_code"],
            "data_type": "static",
            "provenance": _provenance("village_master"),
        },
        "risk": {
            "status": risk_status,
            "score": score,
            "tier": _tier(score) if score is not None else None,
            "baseline_susceptibility_score": score,
            "baseline_tier": _tier(score) if score is not None else None,
            "output": "BASELINE MODELED SUSCEPTIBILITY",
            "data_type": "modeled",
            "pu_status": model_record.get("pu_status") if model_record else None,
            "provenance": model["provenance"],
        },
        "terrain": {
            "status": terrain_status,
            "data": (
                {
                    "elevation_mean_m": _number(terrain_record["elevation_mean_m"]),
                    "elevation_min_m": _number(terrain_record["elevation_min_m"]),
                    "elevation_max_m": _number(terrain_record["elevation_max_m"]),
                    "slope_mean_deg": _number(terrain_record["slope_mean_deg"]),
                    "slope_min_deg": _number(terrain_record["slope_min_deg"]),
                    "slope_max_deg": _number(terrain_record["slope_max_deg"]),
                    "valid_dem_cell_count": int(
                        terrain_record["dem_valid_cell_count"]
                    ),
                }
                if terrain_record
                else None
            ),
            "data_type": "derived",
            "provenance": _provenance(
                "srtm_terrain",
                status=terrain_status,
                unit="elevation: m; slope: degrees; cells: count",
            ),
        },
        "rainfall": {
            "status": rainfall_status,
            "historical": (
                {
                    "date": rainfall_record["date"],
                    "rainfall_1d_mm": _number(rainfall_record["rainfall_1d_mm"]),
                    "rainfall_3d_mm": _number(rainfall_record["rainfall_3d_mm"]),
                    "rainfall_7d_mm": _number(rainfall_record["rainfall_7d_mm"]),
                }
                if rainfall_record
                else None
            ),
            "data_type": "historical",
            "provenance": _provenance(
                "historical_rainfall_villages",
                status=rainfall_status,
                unit="mm",
                observed_at=rainfall_record["date"] if rainfall_record else None,
            ),
            "scenario": scenario_context,
        },
        "weather": {
            "status": Availability.UNAVAILABLE.value,
            "data": None,
            "data_type": "external_current",
            "reason": (
                "Weather is fetched directly by the browser and is not persisted "
                "or passed to this API."
            ),
            "provenance": _provenance(
                "current_weather",
                status=(
                    source_status(weather_source).value
                    if source_status(weather_source) == Availability.AVAILABLE
                    else Availability.UNAVAILABLE.value
                ),
            ),
        },
        "historical_evidence": {
            "status": event_status,
            "count": len(linked_events),
            "data": linked_events if event_rows is not None else None,
            "data_type": "historical",
            "limitations": (
                DATA_SOURCE_BY_ID["historical_event_linkage"].description
            ),
            "provenance": _provenance(
                "historical_event_linkage",
                status=event_status,
            ),
        },
        "model": model,
        "current_conditions": current_conditions,
        "warning": warning,
        "scenario_adjusted_risk": {
            "status": "available_after_scenario_evaluation",
            "output": "SCENARIO-ADJUSTED MODELED RISK",
            "endpoint": "/api/rainfall-scenario",
        },
        "hydrology": hydrology,
        "soil_moisture": {
            **soil_moisture,
            "data": soil_reading,
        },
        "sensor": {
            "status": (
                Availability.AVAILABLE.value
                if registered_sensors
                else Availability.UNAVAILABLE.value
            ),
            "data": registered_sensors or None,
            "provenance": soil_provenance,
        },
        "scenario": scenario_context,
        "data_sources": {
            "administrative": "village_master",
            "risk": "model_susceptibility",
            "terrain": "srtm_terrain",
            "historical_rainfall": "historical_rainfall_villages",
            "historical_evidence": "historical_event_linkage",
            "soil_moisture": "soil_moisture",
        },
    }
