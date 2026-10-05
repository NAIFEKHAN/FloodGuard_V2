"""Evidence-only FastAPI service for the FloodGuard demonstration dashboard."""

from __future__ import annotations

import csv
import json
from collections import Counter
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.app.sensor_api import MAX_SENSOR_REQUEST_BYTES, router as sensor_router
from backend.app.shelter_api import router as shelter_router
from backend.app.warning_api import router as warning_router
from backend.app.data_catalog import (
    MODEL_METADATA,
    list_data_sources,
    source_provenance,
)
from backend.app.hydrology import get_hydrology_map_data, get_village_hydrology
from backend.app.village_context import get_village_context
from pipeline.spatial_features import FEATURE_GROUPS, HYDROLOGY_FEATURES


ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
FRONTEND = ROOT / "frontend"

app = FastAPI(title="FloodGuard Evidence Dashboard API", version="0.3.0")
app.mount("/assets", StaticFiles(directory=FRONTEND), name="assets")
app.mount("/data", StaticFiles(directory=DATA), name="data")
app.include_router(sensor_router)
app.include_router(warning_router)
app.include_router(shelter_router)


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


@app.get("/api/imd/forecast")
def get_imd_forecast(force_refresh: bool = Query(default=False, description="Force a live IMD fetch instead of using a valid cache.")) -> dict[str, object]:
    """Return the latest configured IMD rainfall forecast, cached if needed."""
    from services.imd_service import fetch_imd_forecast

    result = fetch_imd_forecast(force=force_refresh)
    if result.get("status") == "unavailable":
        return {
            **result,
            "mode": "manual_scenario",
            "warning": result.get("warning") or "IMD forecast unavailable; the existing rainfall Scenario Mode remains active.",
        }
    return {
        **result,
        "mode": "imd_auto_forecast",
        "warning": result.get("warning") or "IMD auto forecast is active.",
    }


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
