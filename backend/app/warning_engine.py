"""Deterministic, auditable rule evaluation kept separate from model training."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from backend.app.warning_config import (
    WARNING_DISCLAIMER,
    WARNING_RULE_VERSION,
    WARNING_STAGES,
    WARNING_THRESHOLDS,
)


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def evaluate_warning(
    *,
    village_lgd_code: str,
    village_name: str,
    baseline_score: float | None,
    scenario_score: float | None,
    rainfall_multiplier: float | None,
    rainfall_mode: str,
    soil_moisture: dict[str, Any] | None,
    hydrology: dict[str, Any] | None,
    historical_evidence_count: int | None,
    model_version: str,
    input_timestamps: dict[str, str | None] | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    thresholds = dict(WARNING_THRESHOLDS)
    soil = soil_moisture or {}
    moisture = _number(soil.get("value"))
    freshness = str(soil.get("freshness") or "no_data").lower()
    soil_is_test = bool(soil.get("is_test", False))
    soil_current = freshness == "online" and moisture is not None
    score = _number(baseline_score)
    rainfall_value = _number(rainfall_multiplier)
    mode = "scenario" if rainfall_mode == "scenario" else "current_conditions"

    factors: list[dict[str, Any]] = [
        {
            "type": "susceptibility",
            "label": "Baseline modeled susceptibility",
            "value": _number(baseline_score),
            "unit": "score/100",
            "status": (
                "high"
                if baseline_score is not None
                and baseline_score >= thresholds["susceptibility_watch"]
                else "below_watch"
                if baseline_score is not None
                else "unavailable"
            ),
            "output": "BASELINE MODELED SUSCEPTIBILITY",
            "data_type": "modeled",
            "observed_at": (input_timestamps or {}).get("model_generated_at"),
        },
        {
            "type": "rainfall",
            "label": (
                "Rainfall scenario multiplier"
                if mode == "scenario"
                else "Current rainfall"
            ),
            "value": rainfall_value,
            "unit": "× historical baseline",
            "status": (
                "elevated"
                if rainfall_value is not None
                and rainfall_value >= thresholds["rainfall_watch_multiplier"]
                else "baseline"
                if rainfall_value is not None
                else "unavailable"
            ),
            "data_type": "simulated" if mode == "scenario" else "observed",
            "simulated": mode == "scenario",
            "observed_at": (
                None
                if mode == "scenario"
                else (input_timestamps or {}).get("rainfall_observed_at")
            ),
        },
        {
            "type": "soil_moisture",
            "label": "Soil-moisture observation",
            "value": moisture,
            "unit": "%",
            "status": (
                "very_wet"
                if moisture is not None
                and moisture >= thresholds["soil_moisture_very_wet_support"]
                else "wet"
                if moisture is not None
                and moisture >= thresholds["soil_moisture_wet_support"]
                else "below_wet_support"
                if moisture is not None
                else "unavailable"
            ),
            "freshness": freshness,
            "used_as_current_evidence": soil_current,
            "is_test": soil_is_test,
            "data_type": "test" if soil_is_test else "observed",
            "recorded_at": soil.get("recorded_at"),
            "received_at": soil.get("received_at"),
        },
    ]
    hydro_data = (hydrology or {}).get("data")
    factors.append(
        {
            "type": "hydrology",
            "label": "Terrain-derived hydrological context",
            "value": (
                {
                    "high_flow_area_fraction": _number(
                        hydro_data.get("high_flow_area_fraction")
                    ),
                    "drainage_density_km_per_km2": _number(
                        hydro_data.get("drainage_density_km_per_km2")
                    ),
                    "max_contributing_area_km2": _number(
                        hydro_data.get("max_contributing_area_km2")
                    ),
                }
                if isinstance(hydro_data, dict)
                else None
            ),
            "status": (
                "available"
                if (hydrology or {}).get("status") == "available"
                else "unavailable"
            ),
            "data_type": "derived",
            "used_as_stage_trigger": False,
            "reason": (
                "DEM-derived drainage context is not real-time water level or "
                "inundation evidence."
            ),
            "generated_at": (
                (hydrology or {}).get("provenance", {}).get("generated_at")
                if isinstance((hydrology or {}).get("provenance"), dict)
                else None
            ),
        }
    )
    factors.append(
        {
            "type": "historical_evidence",
            "label": "Linked historical-event context",
            "value": historical_evidence_count,
            "status": (
                "linked_records"
                if historical_evidence_count is not None
                and historical_evidence_count > 0
                else "no_linked_records"
                if historical_evidence_count == 0
                else "unavailable"
            ),
            "data_type": "historical",
            "used_as_stage_trigger": False,
            "reason": (
                "Historical linkage is context only; it is not evidence of a "
                "current event or a negative label."
            ),
        }
    )

    missing_inputs: list[str] = []
    if score is None:
        missing_inputs.append("modeled susceptibility")
    if mode == "current_conditions":
        missing_inputs.append("validated current rainfall")
    elif rainfall_value is None:
        missing_inputs.append("rainfall scenario multiplier")
    if not soil_current:
        if freshness == "stale":
            missing_inputs.append("fresh soil-moisture observation (last reading is stale)")
        elif freshness == "offline":
            missing_inputs.append("fresh soil-moisture observation (sensor is offline)")
        else:
            missing_inputs.append("fresh soil-moisture observation")
    if (hydrology or {}).get("status") != "available":
        missing_inputs.append("terrain-derived hydrological context")

    high_susceptibility = (
        score is not None and score >= thresholds["susceptibility_high"]
    )
    watch_susceptibility = (
        score is not None and score >= thresholds["susceptibility_watch"]
    )
    watch_rainfall = (
        mode == "scenario"
        and rainfall_value is not None
        and rainfall_value >= thresholds["rainfall_watch_multiplier"]
    )
    prepare_rainfall = (
        mode == "scenario"
        and rainfall_value is not None
        and rainfall_value >= thresholds["rainfall_prepare_multiplier"]
    )
    wet_online = (
        soil_current
        and moisture is not None
        and moisture >= thresholds["soil_moisture_wet_support"]
    )
    very_wet_online = (
        soil_current
        and moisture is not None
        and moisture >= thresholds["soil_moisture_very_wet_support"]
    )

    if high_susceptibility and prepare_rainfall:
        stage = "red"
        rule_id = "high_susceptibility_and_prepare_rainfall"
    elif high_susceptibility and watch_rainfall and very_wet_online:
        stage = "red"
        rule_id = "high_susceptibility_watch_rainfall_and_very_wet_online_sensor"
    elif (
        watch_susceptibility
        and watch_rainfall
        or watch_susceptibility
        and wet_online
    ):
        stage = "orange"
        rule_id = "watch_susceptibility_with_rainfall_or_online_wet_sensor"
    elif watch_susceptibility or watch_rainfall:
        stage = "yellow"
        rule_id = "single_watch_signal"
    else:
        stage = "green"
        rule_id = "no_configured_watch_signal"

    reasons: list[str] = []
    if watch_susceptibility:
        reasons.append("High baseline modeled susceptibility")
    if rainfall_value is not None and mode == "scenario":
        reasons.append(f"Rainfall scenario {rainfall_value:.2f}× historical baseline")
    if wet_online and moisture is not None:
        reasons.append(
            f"Online soil-moisture observation {moisture:.1f}% used as supporting evidence"
        )
    elif freshness in {"stale", "offline"} and moisture is not None:
        reasons.append(
            f"Soil-moisture last reading {moisture:.1f}% not used as current evidence "
            f"(sensor {freshness})"
        )
    if (hydrology or {}).get("status") == "available":
        reasons.append("Terrain-derived hydrological context available; not a real-time water measurement")
    if not reasons:
        reasons.append("No configured watch signal in the available inputs")

    stage_config = WARNING_STAGES[stage]
    created = generated_at or datetime.now(UTC).isoformat()
    timestamp_map = dict(input_timestamps or {})
    if soil.get("recorded_at"):
        timestamp_map["soil_moisture_recorded_at"] = str(soil["recorded_at"])
    if soil.get("received_at"):
        timestamp_map["soil_moisture_received_at"] = str(soil["received_at"])
    if isinstance((hydrology or {}).get("provenance"), dict):
        timestamp_map["hydrology_generated_at"] = (
            hydrology["provenance"].get("generated_at")
        )

    return {
        "status": "available" if score is not None else "unavailable",
        "village_lgd_code": village_lgd_code,
        "village_name": village_name,
        "stage": stage,
        "label": stage_config["label"],
        "color": stage_config["color"],
        "rule_id": rule_id,
        "rule_version": WARNING_RULE_VERSION,
        "reason": "; ".join(reasons) + ".",
        "factors": factors,
        "missing_inputs": missing_inputs,
        "thresholds": thresholds,
        "input_timestamps": timestamp_map,
        "baseline_score": _number(baseline_score),
        "scenario_score": _number(scenario_score),
        "selected_score": score,
        "rainfall_mode": mode,
        "rainfall_scenario_multiplier": (
            rainfall_value if mode == "scenario" else None
        ),
        "scenario_is_simulated": mode == "scenario",
        "model_version": model_version,
        "generated_at": created,
        "data_type": "decision_support",
        "is_test": soil_is_test,
        "guidance": stage_config["guidance"],
        "disclaimer": WARNING_DISCLAIMER,
    }


def materially_same_warning(previous: dict[str, Any], current: dict[str, Any]) -> bool:
    """Compare stable, decision-relevant fields to suppress poll-driven history."""
    return (
        previous["stage"] == current["stage"]
        and previous["rule_version"] == current["rule_version"]
        and previous["rainfall_mode"] == current["rainfall_mode"]
        and previous.get("rainfall_scenario_multiplier")
        == current.get("rainfall_scenario_multiplier")
        and previous.get("rule_id") == current.get("rule_id")
        and previous.get("sensor_status") == current.get("sensor_status")
        and previous.get("soil_moisture_band") == current.get("soil_moisture_band")
        and previous.get("is_test") == current.get("is_test")
    )


def soil_moisture_band(value: Any) -> str | None:
    number = _number(value)
    if number is None:
        return None
    if number >= WARNING_THRESHOLDS["soil_moisture_very_wet_support"]:
        return "very_wet"
    if number >= WARNING_THRESHOLDS["soil_moisture_wet_support"]:
        return "wet"
    return "below_wet_support"


def history_signature(warning: dict[str, Any]) -> dict[str, Any]:
    soil = next(
        (factor for factor in warning["factors"] if factor["type"] == "soil_moisture"),
        {},
    )
    return {
        "stage": warning["stage"],
        "rule_version": warning["rule_version"],
        "rainfall_mode": warning["rainfall_mode"],
        "rainfall_scenario_multiplier": warning["rainfall_scenario_multiplier"],
        "rule_id": warning["rule_id"],
        "sensor_status": soil.get("freshness"),
        "soil_moisture_band": soil_moisture_band(soil.get("value"))
        if soil.get("used_as_current_evidence")
        else None,
        "is_test": warning["is_test"],
    }
