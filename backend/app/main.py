"""Evidence-only FastAPI service for the FloodGuard demonstration dashboard."""

from __future__ import annotations

import csv
import json
<<<<<<< Updated upstream
import sqlite3
=======
import logging
>>>>>>> Stashed changes
from collections import Counter
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

from backend.app.sensor_api import MAX_SENSOR_REQUEST_BYTES, router as sensor_router
from backend.app.shelter_api import router as shelter_router
from backend.app.warning_api import router as warning_router
from backend.app.alert_api import router as alert_router
from backend.app.data_catalog import (
    MODEL_METADATA,
    list_data_sources,
    source_provenance,
)
from backend.app.hydrology import get_hydrology_map_data, get_village_hydrology
from backend.app.services.open_meteo import (
    fetch_forecast,
    get_forecast_status,
)
from backend.app.village_context import get_village_context
from pipeline.spatial_features import FEATURE_GROUPS, HYDROLOGY_FEATURES


ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
FRONTEND = ROOT / "frontend"
load_dotenv(ROOT / ".env")
logger = logging.getLogger(__name__)

app = FastAPI(title="FloodGuard Evidence Dashboard API", version="0.3.0")
app.mount("/assets", StaticFiles(directory=FRONTEND), name="assets")
app.mount("/data", StaticFiles(directory=DATA), name="data")
app.include_router(sensor_router)
app.include_router(warning_router)
<<<<<<< Updated upstream
app.include_router(shelter_router)
=======
app.include_router(alert_router)
>>>>>>> Stashed changes


@app.middleware("http")
async def limit_sensor_write_body(request: Request, call_next):
    if request.method == "POST" and request.url.path in {
        "/api/sensors",
        "/api/sensors/readings",
    }:
        content_length = request.headers.get("content-length")
        if content_length:
            if not content_length.isdigit():
                return JSONResponse(
                    status_code=400,
                    content={"detail": "Invalid Content-Length header."},
                )
            if int(content_length) > MAX_SENSOR_REQUEST_BYTES:
                return JSONResponse(
                    status_code=413,
                    content={"detail": "Sensor request body exceeds the 4 KiB limit."},
                )
    return await call_next(request)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


@lru_cache(maxsize=1)
def villages() -> list[dict[str, str]]:
    return read_csv(DATA / "processed/nilgiris_villages.csv")


@lru_cache(maxsize=1)
def rainfall() -> list[dict[str, str]]:
    return read_csv(DATA / "processed/historical_rainfall_features.csv")


@lru_cache(maxsize=1)
def terrain() -> list[dict[str, str]]:
    return read_csv(DATA / "processed/terrain_features.csv")


@lru_cache(maxsize=1)
def events() -> list[dict[str, str]]:
    return read_csv(DATA / "processed/landslide_events.csv")


@lru_cache(maxsize=1)
def experimental_hazard_index() -> list[dict[str, str]]:
    rows = read_csv(DATA / "processed/experimental_hazard_index.csv")
    if len(rows) != 40 or len({row["village_lgd_code"] for row in rows}) != 40:
        raise RuntimeError("Experimental hazard-index artifact must contain exactly 40 unique validated villages.")
    return rows


@lru_cache(maxsize=1)
def ddmp_manifest() -> dict[str, object]:
    return json.loads((DATA / "processed/disaster/ddmp_evidence_manifest.json").read_text(encoding="utf-8"))


@app.get("/", include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(FRONTEND / "index.html")


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok", "service": "FloodGuard API"}


@app.get("/api/imd/status")
@app.get("/api/weather/status")
def get_weather_status() -> dict[str, object]:
    return get_forecast_status()


@app.get("/api/imd/forecast")
@app.get("/api/weather/forecast")
def get_weather_forecast(
    force_refresh: bool = Query(default=False),
    village_lgd_code: str | None = Query(default=None, min_length=1, max_length=32),
) -> dict[str, object]:
    """Return cached/live Open-Meteo rainfall and scores from the existing model."""
    forecast = fetch_forecast(force_refresh=force_refresh)
    if not forecast.get("available"):
        return {
            **forecast,
            "mode": "scenario",
            "records": [],
        }

    import pandas as pd

    from pipeline.run_rainfall_scenario import evaluate_scenario
    from pipeline.spatial_features import MODEL_FEATURES, SCENARIO_RAINFALL_FEATURES

    frame = pd.DataFrame(spatial_dataset())
    missing = [feature for feature in MODEL_FEATURES if feature not in frame.columns]
    if missing:
        raise RuntimeError(f"Weather model input is missing features: {missing}.")
    for feature in MODEL_FEATURES:
        frame[feature] = frame[feature].astype(float)
    if frame.loc[:, list(MODEL_FEATURES)].isna().any().any():
        raise RuntimeError("Weather model input contains missing rainfall or spatial features.")

    rainfall_by_code = {str(row["village_lgd_code"]): row for row in forecast["villages"]}
    model = ml_model()
    records: list[dict[str, object]] = []
    for index, row in frame.iterrows():
        village_code = str(row["village_lgd_code"])
        rainfall = rainfall_by_code.get(village_code)
        if rainfall is None:
            continue
        forecast_amount = float(rainfall["chosen_feature_value_mm"])
        baseline_daily_rainfall = float(row["rainfall_1d_max_mm"])
        if not baseline_daily_rainfall > 0:
            raise ValueError(
                "Cannot calculate an AUTO rainfall factor for village "
                f"{village_code}: baseline daily rainfall must be greater than zero."
            )
        rainfall_factor = forecast_amount / baseline_daily_rainfall
        evaluated = evaluate_scenario(
            frame.loc[[index]],
            model,
            scenario_key="open_meteo_auto_forecast",
            multiplier=rainfall_factor,
        )
        result = evaluated.iloc[0].to_dict()
        result.update(
            {
                "scenario_key": "open_meteo_auto_forecast",
                "scenario_name": "Open-Meteo forecast · next 24 hours",
                "rainfall_factor": rainfall_factor,
                "baseline_daily_rainfall_mm": baseline_daily_rainfall,
                "output_classification": "FORECAST_ADJUSTED_MODELED_RISK",
                "governance_classification": (
                    "FORECAST_ADJUSTED_SCENARIO_MODELED_RISK_NOT_RECALIBRATED"
                ),
                "forecast_rainfall_input_mm": forecast_amount,
                "next_24h_rainfall_mm": forecast_amount,
                "rainfall_input_feature": "scenario rainfall features scaled by rainfall_factor",
                "forecast_period_hours": 24,
                "weather_source": rainfall["source"],
                "weather_status": rainfall["status"],
                "updated_at": rainfall["updated_at"],
                "current_precipitation_mm": rainfall["current_precipitation_mm"],
                "weather_forecast": rainfall,
            }
        )
        records.append(result)
        if village_lgd_code is not None and village_code == village_lgd_code:
            logger.info("[AUTO] Village: %s", row["village_name_en"])
            logger.info("[AUTO] Village ID: %s", village_code)
            logger.info(
                "[AUTO] Latitude: %s · Longitude: %s",
                rainfall["latitude"],
                rainfall["longitude"],
            )
            logger.info(
                "[AUTO] Terrain elevation/slope: %s m / %s°",
                row["elevation_mean_m"],
                row["slope_mean_deg"],
            )
            logger.info(
                "[AUTO] Historical rainfall features: 1d=%s, 3d=%s, 7d=%s, annual=%s mm",
                baseline_daily_rainfall,
                row["rainfall_3d_p95_mm"],
                row["rainfall_7d_p95_mm"],
                row["rainfall_annual_mean_mm"],
            )
            logger.info("[AUTO] Baseline rainfall: %s mm", baseline_daily_rainfall)
            logger.info("[AUTO] Forecast rainfall: %s mm", forecast_amount)
            logger.info("[AUTO] Calculated multiplier: %.4fx", rainfall_factor)
            forecast_model_input = frame.loc[index, list(MODEL_FEATURES)].to_dict()
            for feature in SCENARIO_RAINFALL_FEATURES:
                forecast_model_input[feature] = (
                    float(forecast_model_input[feature]) * rainfall_factor
                )
            logger.info(
                "[AUTO] Model inputs: baseline=%s · forecast=%s",
                frame.loc[index, list(MODEL_FEATURES)].to_dict(),
                forecast_model_input,
            )
            logger.info(
                "[AUTO] Baseline score: %.2f · Forecast score: %.2f · Change: %+.2f",
                result["baseline_susceptibility_0_100"],
                result["scenario_susceptibility_0_100"],
                result["susceptibility_delta"],
            )

    if len(records) != len(forecast["villages"]):
        raise RuntimeError(
            "Weather forecast village coverage does not match the model's 40-village coverage."
        )
    records.sort(
        key=lambda item: float(item["scenario_susceptibility_0_100"]),
        reverse=True,
    )
    tiers = Counter(str(row["scenario_tier"]) for row in records)
    selected_village = None
    if village_lgd_code is not None:
        selected_village = next(
            (
                item
                for item in forecast["villages"]
                if str(item["village_lgd_code"]) == village_lgd_code
            ),
            None,
        )
        if selected_village is None:
            return {
                **forecast,
                "mode": "scenario",
                "status": "unavailable",
                "available": False,
                "forecast_status": "UNAVAILABLE",
                "record_count": 0,
                "villages": [],
                "records": [],
                "selected_village_lgd_code": village_lgd_code,
                "warning": (
                    "No validated forecast coordinates are available for this village; "
                    "Scenario Mode remains active."
                ),
            }
        forecast["selected_village"] = selected_village
        forecast["selected_village_lgd_code"] = village_lgd_code
        records = [
            record
            for record in records
            if str(record["village_lgd_code"]) == village_lgd_code
        ]
        selected_record = records[0] if records else None
        if selected_record is not None and selected_record["scenario_tier"] == "HIGH":
            from backend.app.services.email_alert import send_high_risk_alert

            selected_record["email_alert"] = send_high_risk_alert(
                village_code=village_lgd_code,
                village_name=str(selected_record["village_name_en"]),
                risk_score=float(selected_record["scenario_susceptibility_0_100"]),
                rainfall_mm=float(selected_record["forecast_rainfall_input_mm"]),
                weather_source=str(selected_record["weather_source"]),
                forecast_status=str(selected_record["weather_status"]),
            )
    response = {
        **forecast,
        "mode": "auto",
        "data_type": "forecast_adjusted_modeled",
        "classification": "OPEN_METEO_FORECAST_ADJUSTED_MODELED_RISK",
        "record_count": len(records),
        "high_tier_count": tiers.get("HIGH", 0),
        "medium_tier_count": tiers.get("MEDIUM", 0),
        "low_tier_count": tiers.get("LOW", 0),
        "records": records,
        "warning": (
            forecast.get("warning")
            or "Forecast-adjusted modeled output only; not an official warning."
        ),
    }
    if selected_village is not None:
        response.update(
            {
                "village": selected_village["village_name_en"],
                "latitude": selected_village["latitude"],
                "longitude": selected_village["longitude"],
                "next_24h_rainfall_mm": selected_village[
                    "next_24h_rainfall_mm"
                ],
            }
        )
    return response


@app.get("/api/villages")
def get_villages(limit: int = Query(default=102, ge=1, le=102)) -> dict[str, object]:
    rows = villages()
    return {"classification": "REAL_ADMINISTRATIVE_RECORDS_NOT_MODELLING_UNITS", "record_count": len(rows), "exact_spatial_matches": 40, "village_master_only": 62, "kmz_only": 18, "records": rows[:limit]}


@app.get("/api/data-sources")
def get_data_sources() -> list[dict[str, object]]:
    return list_data_sources()


@app.get("/api/villages/{village_code}/context")
def get_village_context_endpoint(
    village_code: str,
    scenario: str = Query(default="baseline"),
    multiplier: float | None = Query(default=None, ge=0.1, le=5.0),
    warning_mode: str = Query(default="scenario", pattern="^(scenario|current)$"),
) -> dict[str, object]:
    return get_village_context(
        village_code,
        scenario=scenario,
        multiplier=multiplier,
        warning_mode=warning_mode,
    )


@app.get("/api/hydrology")
def get_hydrology() -> dict[str, object]:
    return get_hydrology_map_data()


@app.get("/api/villages/{village_code}/hydrology")
def get_village_hydrology_endpoint(village_code: str) -> dict[str, object]:
    return get_village_hydrology(village_code)


@app.get("/api/rainfall/summary")
def rainfall_summary() -> dict[str, object]:
    rows = rainfall()
    by_year = Counter(row["date"][:4] for row in rows)
    return {"classification": "REAL_IMD_DERIVED_FOR_DEMO_SETTLEMENTS_ONLY", "data_type": "historical", "mode": "historical", "unit": "mm", "provenance": source_provenance("historical_rainfall_demo", unit="mm"), "record_count": len(rows), "years": [{"year": year, "record_count": by_year[year]} for year in sorted(by_year)], "rolling_nulls": {"three_day": 20, "seven_day": 60}, "imputation": "none"}


@app.get("/api/rainfall")
def get_rainfall(year: int = Query(..., ge=2017, le=2024)) -> dict[str, object]:
    rows = [row for row in rainfall() if row["date"].startswith(f"{year}-")]
    if not rows:
        raise HTTPException(status_code=404, detail="No validated local rainfall source for that year.")
    return {"year": year, "classification": "DEMO_SETTLEMENT_FEATURES", "data_type": "historical", "mode": "historical", "unit": "mm", "provenance": source_provenance("historical_rainfall_demo", unit="mm"), "records": rows}


@app.get("/api/terrain")
def get_terrain() -> dict[str, object]:
    return {"classification": "REAL_SRTM_DERIVED_FOR_DEMO_SETTLEMENTS_ONLY", "data_type": "derived", "provenance": source_provenance("srtm_terrain"), "records": terrain()}


@app.get("/api/events")
def get_events() -> dict[str, object]:
    rows = events()
    categories = Counter("explicit" if row["reported_history_date"] else "year_only" if row["history_raw"].strip().isdigit() else "na" if row["history_raw"].strip().upper() == "NA" else "ambiguous" for row in rows)
    return {"classification": "REAL_GSI_NLFC_STANDALONE_INVENTORY", "data_type": "historical", "provenance": source_provenance("historical_events"), "record_count": len(rows), "date_categories": categories, "records": rows, "warning": "Records are independent evidence points, not village assignments or model labels."}


@app.get("/api/ddmp")
def get_ddmp() -> dict[str, object]:
    return {"classification": "OFFICIAL_DDMP_DOCUMENTARY_EVIDENCE_NOT_ML_LABELS", "manifest": ddmp_manifest(), "arg_stations": read_csv(DATA / "processed/disaster/ddmp_proposed_arg_stations.csv"), "aws_stations": read_csv(DATA / "processed/disaster/ddmp_aws_stations.csv"), "vulnerability_summary": read_csv(DATA / "processed/disaster/ddmp_vulnerable_location_summary.csv")}


@app.get("/api/experimental-hazard-index")
def get_experimental_hazard_index() -> dict[str, object]:
    """Return the fixed Phase 8 descriptive index without recalculation or inference."""
    rows = experimental_hazard_index()
    return {
        "classification": "EXPERIMENTAL_HAZARD_INDEX_NOT_ML_NOT_A_PREDICTION",
        "record_count": len(rows),
        "warning": "A relative, fixed-method demonstration index for 40 validated villages only; not a prediction, probability, warning, label, or calibrated risk score.",
        "records": rows,
    }


@app.get("/api/status")
def get_status() -> dict[str, object]:
    return {
        "ml_status": "CONDITIONAL_SPATIAL_SUSCEPTIBILITY_MODEL",
        **MODEL_METADATA,
        "data_type": "modeled",
        "provenance": source_provenance("model_susceptibility"),
        "susceptibility_scores": "AVAILABLE",
        "scenario_engine": "AVAILABLE",
        "warning": "Demonstration spatial susceptibility ranking, not live operational flood/landslide predictions or calibrated probabilities.",
    }


def _read_coverage_csv(path: Path) -> list[dict[str, str]] | None:
    try:
        return read_csv(path)
    except OSError:
        return None


def _system_coverage_snapshot() -> dict[str, object]:
    from backend.app import sensor_store, shelter_service
    from backend.app.data_catalog import DATA_SOURCE_BY_ID, source_status

    master = _read_coverage_csv(DATA / "processed/nilgiris_villages.csv")
    model_rows = _read_coverage_csv(
        DATA / "processed/ml_village_susceptibility_scores.csv"
    )
    experimental_rows = _read_coverage_csv(
        DATA / "processed/experimental_hazard_index.csv"
    )
    hydro_rows = _read_coverage_csv(
        DATA / "processed/hydrology/village_hydrology_features.csv"
    )
    terrain_rows = _read_coverage_csv(
        DATA / "processed/terrain_features_villages.csv"
    )
    rainfall_rows = _read_coverage_csv(
        DATA / "processed/ml_spatial_dataset.csv"
    )
    event_rows = _read_coverage_csv(DATA / "processed/landslide_events.csv")

    def codes(rows: list[dict[str, str]] | None) -> set[str]:
        return {
            row["village_lgd_code"].strip()
            for row in (rows or [])
            if row.get("village_lgd_code", "").strip()
        }

    model_codes = codes(model_rows)
    hydro_codes = codes(hydro_rows)
    terrain_codes = codes(terrain_rows)
    rainfall_codes = codes(rainfall_rows)
    try:
        sensor_records = sensor_store.list_sensors()
        sensor_storage_status = "available"
        sensor_reason = None
    except (OSError, sqlite3.Error) as error:
        sensor_records = []
        sensor_storage_status = "unavailable"
        sensor_reason = str(error)
    real_sensors = [sensor for sensor in sensor_records if not sensor["is_test"]]
    online_real_sensors = [
        sensor for sensor in real_sensors if sensor["status"] == "online"
    ]
    test_sensors = [sensor for sensor in sensor_records if sensor["is_test"]]
    if sensor_storage_status != "available" or not real_sensors:
        sensor_data_status = "unavailable"
        sensor_data_reason = (
            sensor_reason
            or "No non-test soil-moisture sensors are registered."
        )
    elif online_real_sensors:
        sensor_data_status = "available"
        sensor_data_reason = None
    else:
        sensor_data_status = "degraded"
        sensor_data_reason = "Real sensors are registered, but none currently report online."

    try:
        shelter_data = shelter_service.shelter_coverage()
        shelter_status = (
            "available"
            if shelter_data["route_eligible_records"] > 0
            else "unavailable"
        )
        shelter_reason = (
            None
            if shelter_data["route_eligible_records"] > 0
            else "No verified, operational shelter records are available."
        )
    except (OSError, RuntimeError, ValueError, KeyError) as error:
        shelter_data = None
        shelter_status = "unavailable"
        shelter_reason = str(error)

    total_villages = len(master) if master is not None else None
    model_count = len(model_rows) if model_rows is not None else None
    hydrology_count = len(hydro_rows) if hydro_rows is not None else None
    boundary_count = (
        len(model_codes)
        if model_rows is not None
        and (DATA / "raw/admin/vb_soi_tn.kmz").is_file()
        else None
    )
    boundary_status = "available" if boundary_count is not None else "unavailable"
    hydrology_status = source_status(DATA_SOURCE_BY_ID["hydrology"]).value
    model_artifact_available = (
        (ROOT / "model/artifacts/spatial_susceptibility_xgboost.json").is_file()
        and (ROOT / "model/artifacts/spatial_validation_metrics.json").is_file()
    )
    warning_ready = (
        model_rows is not None
        and model_artifact_available
        and hydrology_status == "available"
        and sensor_storage_status == "available"
    )
    warning_status = "available" if warning_ready else "unavailable"
    warning_reason = (
        None
        if warning_ready
        else "One or more model, hydrology, or sensor-store dependencies are unavailable."
    )
    warning_coverage = (
        {
            "supported_village_count": model_count,
            "total_village_master_count": total_villages,
        }
        if warning_ready
        else None
    )
    coverage = {
        "village_master": {
            "total": total_villages,
            "status": "available" if master is not None else "unavailable",
            "source": "data/processed/nilgiris_villages.csv",
        },
        "village_boundaries": {
            "validated_model_polygons": boundary_count,
            "status": boundary_status,
            "source": "SOI KMZ boundary subset represented in the validated model population",
        },
        "model": {
            "supported_villages": model_count,
            "coverage_percent": (
                round(model_count / total_villages * 100, 1)
                if model_count is not None and total_villages
                else None
            ),
            "status": (
                "available"
                if model_rows is not None and model_artifact_available
                else "unavailable"
            ),
            "artifact_available": model_artifact_available,
            "lazy_loaded": ml_model.cache_info().currsize > 0,
            "source": "model/artifacts/spatial_susceptibility_xgboost.json",
        },
        "experimental_hazard_index": {
            "records": len(experimental_rows) if experimental_rows is not None else None,
            "status": "available" if experimental_rows is not None else "unavailable",
            "classification": "descriptive_not_ML_not_prediction",
        },
        "terrain": {
            "supported_villages": len(terrain_codes) if terrain_rows is not None else None,
            "status": "available" if terrain_rows is not None else "unavailable",
            "source": "SRTM-derived terrain features",
        },
        "hydrology": {
            "supported_villages": hydrology_count,
            "model_villages_matched": len(model_codes & hydro_codes),
            "status": hydrology_status,
            "source": "DEM-derived offline hydrology products",
        },
        "historical_rainfall": {
            "supported_villages": len(rainfall_codes) if rainfall_rows is not None else None,
            "status": "available" if rainfall_rows is not None else "unavailable",
            "source": "IMD-derived historical rainfall features in the model-ready table",
        },
        "historical_events": {
            "records": len(event_rows) if event_rows is not None else None,
            "status": "available" if event_rows is not None else "unavailable",
            "source": "GSI/NLFC standalone event inventory",
        },
        "warnings": {
            "supported_villages": (
                warning_coverage["supported_village_count"]
                if warning_coverage
                else None
            ),
            "status": warning_status,
            "reason": warning_reason,
        },
        "current_rainfall": {
            "status": "unavailable",
            "reason": "No validated current rainfall feed is configured.",
        },
        "rainfall_scenario": {
            "status": (
                "available"
                if model_rows is not None and model_artifact_available
                else "unavailable"
            ),
            "classification": "simulated",
            "model_population": model_count,
        },
        "weather": {
            "status": "available",
            "provider": "Open-Meteo",
            "classification": "contextual_only",
            "health_check": "not_probed",
        },
        "sensors": {
            "storage_status": sensor_storage_status,
            "storage_reason": sensor_reason,
            "status": sensor_data_status,
            "reason": sensor_data_reason,
            "registered_real": len(real_sensors),
            "online_real": len(online_real_sensors),
            "registered_test": len(test_sensors),
            "physical_validation": "pending",
        },
        "shelters": {
            "status": shelter_status,
            "reason": shelter_reason,
            "total_records": (
                shelter_data["total_records"] if shelter_data else None
            ),
            "verified_records": shelter_data["verified"] if shelter_data else None,
            "route_eligible": (
                shelter_data["route_eligible_records"] if shelter_data else None
            ),
        },
    }

    def feature(
        name: str,
        status: str,
        source: str,
        covered: object,
        limitation: str,
    ) -> dict[str, object]:
        return {
            "feature": name,
            "status": status,
            "source": source,
            "coverage": covered,
            "limitation": limitation,
        }

    feature_audit = [
        feature(
            "Village master and boundaries",
            "partial" if boundary_count is not None and total_villages != boundary_count else boundary_status,
            "LGD village master + SOI KMZ",
            f"{boundary_count if boundary_count is not None else 'unknown'} validated model polygons / "
            f"{total_villages if total_villages is not None else 'unknown'} master records",
            "Unmatched master and boundary records are not inferred or joined.",
        ),
        feature(
            "Baseline susceptibility model",
            str(coverage["model"]["status"]),
            "PU-weighted XGBoost; spatial validation artifact",
            f"{model_count if model_count is not None else 'unknown'} supported villages",
            "Positive/unlabeled proxy; no verified negatives or calibrated probabilities.",
        ),
        feature(
            "Experimental hazard index",
            str(coverage["experimental_hazard_index"]["status"]),
            "Fixed-method descriptive index artifact",
            f"{coverage['experimental_hazard_index']['records'] if experimental_rows is not None else 'unknown'} records",
            "Descriptive demonstration only; not ML, a prediction, or a warning.",
        ),
        feature(
            "Terrain",
            str(coverage["terrain"]["status"]),
            "SRTM-derived terrain feature table",
            f"{len(terrain_codes) if terrain_rows is not None else 'unknown'} villages",
            "Offline-derived coverage only; not a live sensor measurement.",
        ),
        feature(
            "Hydrology",
            hydrology_status,
            "DEM-derived offline hydrology products",
            f"{hydrology_count if hydrology_count is not None else 'unknown'} villages; "
            f"{len(model_codes & hydro_codes)} model villages joined",
            "Terrain-derived context, not observed flow or flood extent.",
        ),
        feature(
            "Historical rainfall",
            str(coverage["historical_rainfall"]["status"]),
            "IMD-derived historical features in the model-ready table",
            f"{len(rainfall_codes) if rainfall_rows is not None else 'unknown'} villages",
            "Historical features do not represent current rainfall conditions.",
        ),
        feature(
            "Historical event evidence",
            str(coverage["historical_events"]["status"]),
            "GSI/NLFC event inventory",
            f"{len(event_rows) if event_rows is not None else 'unknown'} standalone records",
            "Events are evidence points; they are not automatically village assignments.",
        ),
        feature(
            "Current rainfall",
            "unavailable",
            "No validated current rainfall provider",
            "No live coverage",
            "Rainfall scenarios are simulations and must not be read as observations.",
        ),
        feature(
            "Rainfall Scenario",
            str(coverage["rainfall_scenario"]["status"]),
            "On-demand in-memory model feature simulation",
            f"{model_count if model_count is not None else 'unknown'} model villages",
            "Simulated input only; not current rainfall or a forecast.",
        ),
        feature(
            "Weather context",
            str(coverage["weather"]["status"]),
            "Open-Meteo browser integration",
            "District/village context when the provider responds",
            "Context only; not an input to the susceptibility model.",
        ),
        feature(
            "Soil-moisture sensors",
            sensor_data_status,
            "Optional ESP32 registration and local SQLite readings",
            f"{len(online_real_sensors)} online real / {len(real_sensors)} registered real; "
            f"{len(test_sensors)} test",
            sensor_data_reason
            or "No sensor features in the model; physical validation remains pending.",
        ),
        feature(
            "Warning engine",
            warning_status,
            "Configurable deterministic decision-support rules",
            (
                f"{warning_coverage['supported_village_count']} supported villages"
                if warning_coverage
                else "Unavailable"
            ),
            warning_reason or "Not an official alert or operational warning service.",
        ),
        feature(
            "Shelters and evacuation routing",
            shelter_status,
            "Source-verified shelter inventory + road-routing provider",
            (
                f"{shelter_data['route_eligible_records']} route-eligible facilities"
                if shelter_data
                else "Inventory unavailable"
            ),
            shelter_reason or "Routing requires verified, operational facility records.",
        ),
        feature(
            "Satellite and street basemaps",
            "available",
            "Esri World Imagery and OpenStreetMap tile providers",
            "Configured in the Leaflet dashboard",
            "External tile health is not probed by the backend status endpoint.",
        ),
    ]
    return {
        "status": "available",
        "generated_at": datetime.now(UTC).isoformat(),
        "coverage": coverage,
        "feature_audit": feature_audit,
    }


@app.get("/api/system/coverage")
def get_system_coverage() -> dict[str, object]:
    return _system_coverage_snapshot()


@app.get("/api/system/status")
def get_system_status() -> dict[str, object]:
    snapshot = _system_coverage_snapshot()
    coverage = snapshot["coverage"]
    assert isinstance(coverage, dict)
    model = coverage["model"]
    hydrology = coverage["hydrology"]
    sensors = coverage["sensors"]
    shelters = coverage["shelters"]
    warnings = coverage["warnings"]
    components = {
        "backend": {"status": "available", "detail": "FastAPI is serving this response."},
        "model": {
            "status": model["status"],
            "detail": (
                "Model artifact is available; the XGBoost model loads lazily on scoring."
                if model["status"] == "available"
                else "Model artifact or score coverage is unavailable."
            ),
            "loaded": model["lazy_loaded"],
        },
        "basemaps": {
            "status": "available",
            "detail": "Satellite and street providers are configured; external tile health is not probed.",
        },
        "hydrology": {
            "status": hydrology["status"],
            "detail": "Offline-derived hydrology coverage; not a live measurement.",
        },
        "warning_engine": {
            "status": warnings["status"],
            "detail": warnings["reason"] or "Warning rules and required local dependencies are available; no evaluation is performed by this status request.",
        },
        "sensor_storage": {
            "status": sensors["storage_status"],
            "detail": sensors["storage_reason"] or "Optional sensor store is available.",
        },
        "current_rainfall": {
            "status": "unavailable",
            "detail": "No validated current rainfall feed is configured.",
        },
        "weather": {
            "status": coverage["weather"]["status"],
            "detail": "Provider availability is configured; live health is not probed here.",
        },
        "shelters": {
            "status": shelters["status"],
            "detail": shelters["reason"] or "Verified, operational shelter data is available.",
        },
        "routing": {
            "status": "available" if shelters["route_eligible"] else "degraded",
            "detail": (
                "Road routing requires at least one verified, operational shelter."
                if not shelters["route_eligible"]
                else "Routing is available for verified, operational shelters."
            ),
        },
    }
    degraded = any(
        component["status"] in {"degraded", "unavailable"}
        for name, component in components.items()
        if name != "backend"
    )
    return {
        "status": "degraded" if degraded else "available",
        "generated_at": snapshot["generated_at"],
        "components": components,
        "coverage": coverage,
        "feature_audit": snapshot["feature_audit"],
    }


@lru_cache(maxsize=1)
def spatial_dataset() -> list[dict[str, str]]:
    rows = read_csv(DATA / "processed/ml_spatial_dataset.csv")
    if len(rows) != 40:
        raise RuntimeError("Spatial ML dataset must contain exactly 40 unique validated villages.")
    return rows


@lru_cache(maxsize=1)
def ml_model():
    from xgboost import XGBClassifier
    model_path = ROOT / "model/artifacts/spatial_susceptibility_xgboost.json"
    if not model_path.exists():
        raise RuntimeError(f"Model artifact not found at {model_path}")
    model = XGBClassifier()
    model.load_model(str(model_path))
    return model


@app.get("/api/ml-susceptibility")
def get_ml_susceptibility() -> dict[str, object]:
    """Return baseline modeled susceptibility with evidence and feature coverage."""
    rows = read_csv(DATA / "processed/ml_village_susceptibility_scores.csv")
    if len(rows) != 40:
        raise RuntimeError("ML village susceptibility table must contain exactly 40 validated villages.")
    
    pos_count = sum(1 for r in rows if r["pu_status"] == "POSITIVE")
    unl_count = sum(1 for r in rows if r["pu_status"] == "UNLABELED")

    return {
        "classification": "SPATIAL_VILLAGE_SUSCEPTIBILITY_PU_MODEL",
        "data_type": "modeled",
        "record_count": len(rows),
        "labeled_positive_count": pos_count,
        "unlabeled_count": unl_count,
        "spatial_validation": "6-Fold Leave-One-Taluk-Out (LOTO)",
        "baseline_output": "BASELINE MODELED SUSCEPTIBILITY",
        "feature_groups": {
            name: list(features) for name, features in FEATURE_GROUPS.items()
        },
        "feature_importances": MODEL_METADATA["feature_importances"],
        "folds_using_hydrology": MODEL_METADATA["folds_using_hydrology"],
        "estimator_parameters": MODEL_METADATA["estimator_parameters"],
        "training_coverage": MODEL_METADATA["hydrology_join"],
        "current_conditions": MODEL_METADATA["current_conditions"],
        "scenario_output": MODEL_METADATA["scenario_output"],
        "historical_evidence_target_source": "conditional_strict_linked_event_count",
        "historical_evidence_predictor_features": [],
        "excluded_training_columns": [
            "conditional_strict_linked_event_count",
            "experimental_hazard_index_0_100",
        ],
        "future_sensor_features": {
            "soil_moisture": {"status": "planned", "included_in_training": False}
        },
        "hydrology_join": {
            "join_key": "village_lgd_code",
            "matched_count": len(rows),
            "unmatched_model_count": 0,
            "features": list(HYDROLOGY_FEATURES),
            "missing_data_strategy": "No imputation; no training rows omitted.",
        },
        "metric_interpretation": MODEL_METADATA["metric_interpretation"],
        "metrics": {
            "loto_roc_auc": MODEL_METADATA["loto_roc_auc"],
            "loto_pr_auc": MODEL_METADATA["loto_pr_auc"],
            "loto_brier_score": MODEL_METADATA["loto_brier_score"],
        },
        "provenance": source_provenance("model_susceptibility"),
        "warning": "Demonstration spatial susceptibility ranking; not a real-time warning, evacuation trigger, or calibrated flood probability.",
        "records": rows,
    }


@app.get("/api/rainfall-scenario")
def get_rainfall_scenario(
    scenario: str = Query(default="baseline", description="Preset scenario (moderate, baseline, heavy, extreme, custom)"),
    multiplier: float | None = Query(default=None, ge=0.1, le=5.0, description="Custom rainfall multiplier (0.1 to 5.0)"),
    r1d: float | None = Query(default=None, ge=0.0, le=500.0, description="Custom 1-day rainfall override (mm)"),
    r7d: float | None = Query(default=None, ge=0.0, le=1500.0, description="Custom 7-day rainfall override (mm)"),
) -> dict[str, object]:
    """Execute dynamic non-destructive rainfall scenario simulation over the 40 validated villages."""
    from pipeline.run_rainfall_scenario import evaluate_scenario, PRESET_SCENARIOS
    import pandas as pd

    model = ml_model()
    df_raw = pd.DataFrame(spatial_dataset())
    
    # Cast all named model features from CSV strings without substituting missing values.
    from pipeline.spatial_features import MODEL_FEATURES

    num_cols = list(MODEL_FEATURES)
    for c in num_cols:
        df_raw[c] = df_raw[c].astype(float)

    results_df = evaluate_scenario(
        df_raw,
        model,
        scenario_key=scenario,
        multiplier=multiplier,
        custom_rainfall_1d_mm=r1d,
        custom_rainfall_7d_mm=r7d,
    )

    records = results_df.to_dict(orient="records")
    tier_counts = Counter(r["scenario_tier"] for r in records)

    scenario_name = PRESET_SCENARIOS.get(scenario, {}).get("name", f"Custom ({multiplier or 1.0}x)")
    factor = multiplier if multiplier is not None else PRESET_SCENARIOS.get(scenario, {}).get("multiplier", 1.0)
    generated_at = datetime.now(UTC).isoformat()

    return {
        "classification": "RAINFALL_SCENARIO_DEMONSTRATION_SIMULATION",
        "data_type": "simulated",
        "mode": "scenario",
        "output_data_type": "modeled",
        "provenance": source_provenance(
            "rainfall_scenario",
            unit="×",
            generated_at=generated_at,
        ),
        "scenario_key": scenario,
        "scenario_name": scenario_name,
        "baseline_output": MODEL_METADATA["baseline_output"],
        "scenario_output": MODEL_METADATA["scenario_output"],
        "current_conditions": MODEL_METADATA["current_conditions"],
        "metric_interpretation": MODEL_METADATA["metric_interpretation"],
        "rainfall_factor": factor,
        "record_count": len(records),
        "high_tier_count": tier_counts.get("HIGH", 0),
        "medium_tier_count": tier_counts.get("MEDIUM", 0),
        "low_tier_count": tier_counts.get("LOW", 0),
        "warning": "Demonstration scenario output under simulated precipitation; not a live forecast, official alert, or evacuation instruction.",
        "records": records,
    }
