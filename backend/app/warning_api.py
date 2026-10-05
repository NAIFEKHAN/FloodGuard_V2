"""API-facing warning evaluation over existing model, rainfall, sensor and GIS outputs."""

from __future__ import annotations

import csv
import logging
from collections import Counter
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from backend.app import sensor_store
from backend.app.data_catalog import MODEL_METADATA
from backend.app.hydrology import get_village_hydrology
from backend.app.sensor_store import latest_village_reading
from backend.app.warning_config import (
    WARNING_DISCLAIMER,
    WARNING_RULE_VERSION,
    WARNING_STAGES,
    WARNING_THRESHOLDS,
)
from backend.app.warning_engine import evaluate_warning


logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
router = APIRouter(prefix="/api", tags=["warnings"])
SUPPORTED_MODEL_COVERAGE = 40


@lru_cache(maxsize=1)
def _read_scores() -> tuple[dict[str, str], ...]:
    with (DATA / "processed/ml_village_susceptibility_scores.csv").open(
        "r", encoding="utf-8-sig", newline=""
    ) as handle:
        rows = tuple(csv.DictReader(handle))
    if len(rows) != SUPPORTED_MODEL_COVERAGE:
        raise RuntimeError(
            f"Warning engine expected {SUPPORTED_MODEL_COVERAGE} model villages; found {len(rows)}."
        )
    if len({row["village_lgd_code"] for row in rows}) != len(rows):
        raise RuntimeError("Warning model scores contain duplicate village LGD codes.")
    return rows


@lru_cache(maxsize=1)
def _read_villages() -> dict[str, dict[str, str]]:
    with (DATA / "processed/nilgiris_villages.csv").open(
        "r", encoding="utf-8-sig", newline=""
    ) as handle:
        return {
            row["village_lgd_code"]: row
            for row in csv.DictReader(handle)
            if row.get("village_lgd_code")
        }


@lru_cache(maxsize=1)
def _historical_evidence_counts() -> dict[str, int]:
    path = DATA / "processed/event_village_linkage.csv"
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = csv.DictReader(handle)
            counts = Counter(
                row["matched_village_lgd_code"]
                for row in rows
                if row.get("matched_village_lgd_code")
                and row.get("spatial_linkage_status") == "EXACT_POLYGON_MATCH"
            )
    except FileNotFoundError:
        logger.exception("Historical event linkage unavailable to warning context.")
        return {}
    return dict(counts)


@lru_cache(maxsize=1)
def _historical_rainfall_dates() -> dict[str, str]:
    path = DATA / "processed/village_time_features.csv"
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            latest: dict[str, str] = {}
            for row in csv.DictReader(handle):
                code = row.get("village_lgd_code")
                observed_at = row.get("date")
                if code and observed_at and observed_at > latest.get(code, ""):
                    latest[code] = observed_at
            return latest
    except FileNotFoundError:
        logger.exception("Historical rainfall timestamps unavailable to warning context.")
        return {}


def _score(value: str | None) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _scenario_records(
    *,
    scenario: str,
    multiplier: float | None,
) -> tuple[dict[str, dict[str, Any]], float, str]:
    from backend.app.main import ml_model, spatial_dataset
    from pipeline.run_rainfall_scenario import PRESET_SCENARIOS, evaluate_scenario
    from pipeline.spatial_features import MODEL_FEATURES
    import pandas as pd

    if multiplier is not None:
        scenario = "custom"
    if scenario not in PRESET_SCENARIOS and scenario != "custom":
        raise HTTPException(status_code=422, detail="Unknown rainfall scenario.")
    if scenario == "custom" and multiplier is None:
        raise HTTPException(
            status_code=422,
            detail="A multiplier is required for the custom rainfall scenario.",
        )
    if multiplier is not None and not 0.1 <= multiplier <= 5.0:
        raise HTTPException(status_code=422, detail="Multiplier must be between 0.1 and 5.0.")

    frame = pd.DataFrame(spatial_dataset())
    for feature in MODEL_FEATURES:
        frame[feature] = frame[feature].astype(float)
    evaluated = evaluate_scenario(
        frame,
        ml_model(),
        scenario_key=scenario,
        multiplier=multiplier,
    )
    records = {
        str(row["village_lgd_code"]): row
        for row in evaluated.to_dict(orient="records")
    }
    factor = (
        float(multiplier)
        if multiplier is not None
        else float(PRESET_SCENARIOS[scenario]["multiplier"])
    )
    name = (
        f"Custom ({factor:.2f}x Multiplier)"
        if scenario == "custom"
        else str(PRESET_SCENARIOS[scenario]["name"])
    )
    return records, factor, name


def _build_village_warning(
    code: str,
    *,
    mode: str,
    scenario: str,
    multiplier: float | None,
    scenario_data: tuple[dict[str, dict[str, Any]], float, str] | None,
    generated_at: str,
) -> dict[str, Any]:
    village = _read_villages().get(code)
    if village is None:
        raise HTTPException(status_code=404, detail="Village LGD code was not found.")
    model_row = next(
        (row for row in _read_scores() if row["village_lgd_code"] == code),
        None,
    )
    if model_row is None:
        return {
            "status": "unavailable",
            "village_lgd_code": code,
            "village_name": village["village_name_en"],
            "reason": "Insufficient model/data coverage for warning evaluation.",
            "missing_inputs": ["validated model and hydrology coverage"],
            "generated_at": generated_at,
            "data_type": "decision_support",
            "disclaimer": WARNING_DISCLAIMER,
        }

    baseline_score = _score(
        model_row.get("baseline_susceptibility_0_100")
        or model_row.get("ml_susceptibility_0_100")
    )
    soil_reading = latest_village_reading(code)
    soil = (
        {
            "value": soil_reading["soil_moisture_percent"],
            "sensor_id": soil_reading["sensor_id"],
            "freshness": soil_reading["freshness"],
            "recorded_at": soil_reading["recorded_at"],
            "received_at": soil_reading["received_at"],
            "is_test": soil_reading["is_test"],
        }
        if soil_reading
        else {"value": None, "freshness": "no_data", "is_test": False}
    )
    if mode == "scenario":
        assert scenario_data is not None
        records, factor, scenario_name = scenario_data
        scenario_record = records[code]
        scenario_score = _score(
            str(scenario_record["scenario_susceptibility_0_100"])
        )
        rainfall_multiplier = factor
        scenario_key = "custom" if multiplier is not None else scenario
        rainfall_timestamp = None
    else:
        scenario_score = None
        rainfall_multiplier = None
        scenario_name = None
        scenario_key = None
        rainfall_timestamp = None

    hydrology = get_village_hydrology(code)
    warning = evaluate_warning(
        village_lgd_code=code,
        village_name=village["village_name_en"],
        baseline_score=baseline_score,
        scenario_score=scenario_score,
        rainfall_multiplier=rainfall_multiplier,
        rainfall_mode=mode,
        soil_moisture=soil,
        hydrology=hydrology,
        historical_evidence_count=_historical_evidence_counts().get(code),
        model_version=str(MODEL_METADATA["model_type"]),
        input_timestamps={
            "model_generated_at": None,
            "rainfall_observed_at": rainfall_timestamp,
            "historical_rainfall_observed_at": _historical_rainfall_dates().get(code),
            "sensor_recorded_at": soil.get("recorded_at"),
            "sensor_received_at": soil.get("received_at"),
            "scenario_generated_at": generated_at if mode == "scenario" else None,
        },
        generated_at=generated_at,
    )
    warning["rainfall_scenario_key"] = scenario_key
    warning["rainfall_scenario_name"] = scenario_name
    warning["sensor_status"] = soil.get("freshness")
    warning["soil_moisture"] = soil.get("value")
    warning["warning_mode_label"] = (
        "SCENARIO" if mode == "scenario" else "CURRENT CONDITIONS (RAINFALL UNAVAILABLE)"
    )
    sensor_factor = next(
        factor for factor in warning["factors"] if factor["type"] == "soil_moisture"
    )
    warning["is_test"] = bool(sensor_factor["is_test"])
    return warning


def evaluate_village_warning(
    village_code: str,
    *,
    mode: str = "scenario",
    scenario: str = "baseline",
    multiplier: float | None = None,
    persist_history: bool = True,
    generated_at: str | None = None,
) -> dict[str, Any]:
    if mode not in {"scenario", "current"}:
        raise HTTPException(
            status_code=422,
            detail="Warning mode must be 'scenario' or 'current'.",
        )
    now = generated_at or datetime.now(UTC).isoformat()
    scenario_data = (
        _scenario_records(scenario=scenario, multiplier=multiplier)
        if mode == "scenario"
        else None
    )
    warning = _build_village_warning(
        village_code.strip(),
        mode=mode,
        scenario=scenario,
        multiplier=multiplier,
        scenario_data=scenario_data,
        generated_at=now,
    )
    if persist_history and warning["status"] == "available":
        sensor_store.record_warning_if_changed(warning)
    return warning


def evaluate_all_warnings(
    *,
    mode: str = "scenario",
    scenario: str = "baseline",
    multiplier: float | None = None,
    persist_history: bool = True,
) -> dict[str, Any]:
    if mode not in {"scenario", "current"}:
        raise HTTPException(
            status_code=422,
            detail="Warning mode must be 'scenario' or 'current'.",
        )
    generated_at = datetime.now(UTC).isoformat()
    scenario_data = (
        _scenario_records(scenario=scenario, multiplier=multiplier)
        if mode == "scenario"
        else None
    )
    supported = {row["village_lgd_code"] for row in _read_scores()}
    records = [
        _build_village_warning(
            code,
            mode=mode,
            scenario=scenario,
            multiplier=multiplier,
            scenario_data=scenario_data,
            generated_at=generated_at,
        )
        for code in sorted(supported)
    ]
    if persist_history:
        for warning in records:
            if warning["status"] == "available":
                sensor_store.record_warning_if_changed(warning)
    counts = Counter(row["stage"] for row in records)
    return {
        "status": "available",
        "warning_engine": "configurable_deterministic_rules",
        "rule_version": WARNING_RULE_VERSION,
        "mode": mode,
        "mode_label": (
            "SCENARIO"
            if mode == "scenario"
            else "CURRENT CONDITIONS (VALIDATED CURRENT RAINFALL UNAVAILABLE)"
        ),
        "scenario_key": (
            "custom" if multiplier is not None else scenario
        ) if mode == "scenario" else None,
        "scenario_name": (
            scenario_data[2] if scenario_data is not None else None
        ),
        "scenario_is_simulated": mode == "scenario",
        "generated_at": generated_at,
        "data_type": "decision_support",
        "coverage": {
            "supported_village_count": len(records),
            "total_village_master_count": len(_read_villages()),
            "unsupported_village_count": len(_read_villages()) - len(records),
            "join_key": "village_lgd_code",
        },
        "stage_counts": {stage: counts.get(stage, 0) for stage in WARNING_STAGES},
        "stages": WARNING_STAGES,
        "thresholds": WARNING_THRESHOLDS,
        "disclaimer": WARNING_DISCLAIMER,
        "records": records,
    }


@router.get("/warnings")
def get_warnings(
    mode: str = Query(default="scenario"),
    scenario: str = Query(default="baseline"),
    multiplier: float | None = Query(default=None, ge=0.1, le=5.0),
) -> dict[str, Any]:
    return evaluate_all_warnings(mode=mode, scenario=scenario, multiplier=multiplier)


@router.get("/villages/{village_code}/warning")
def get_village_warning(
    village_code: str,
    mode: str = Query(default="scenario"),
    scenario: str = Query(default="baseline"),
    multiplier: float | None = Query(default=None, ge=0.1, le=5.0),
) -> dict[str, Any]:
    return evaluate_village_warning(
        village_code,
        mode=mode,
        scenario=scenario,
        multiplier=multiplier,
    )


@router.get("/warnings/history")
def get_warning_history_endpoint(
    village_lgd_code: str | None = Query(default=None, min_length=1, max_length=32),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    records = sensor_store.get_warning_history(
        village_lgd_code=village_lgd_code,
        limit=limit,
    )
    return {
        "record_count": len(records),
        "records": records,
        "data_type": "decision_support_history",
        "disclaimer": WARNING_DISCLAIMER,
    }
