"""Shared feature schema for baseline and rainfall-scenario spatial models."""

from __future__ import annotations

TERRAIN_FEATURES = (
    "elevation_mean_m",
    "elevation_range_m",
    "slope_mean_deg",
    "slope_max_deg",
)

HYDROLOGY_FEATURES = (
    "mean_flow_accumulation_cells",
    "max_flow_accumulation_cells",
    "high_flow_area_fraction",
    "drainage_density_km_per_km2",
)

RAINFALL_FEATURES = (
    "rainfall_7d_p95_mm",
    "rainfall_3d_p95_mm",
    "rainfall_1d_max_mm",
    "rainfall_annual_mean_mm",
)

FEATURE_GROUPS = {
    "terrain": TERRAIN_FEATURES,
    "hydrology": HYDROLOGY_FEATURES,
    "rainfall": RAINFALL_FEATURES,
    "historical_evidence_predictors": (),
}

MODEL_FEATURES = tuple(
    feature for group_features in FEATURE_GROUPS.values() for feature in group_features
)

SCENARIO_RAINFALL_FEATURES = (
    "rainfall_7d_p95_mm",
    "rainfall_3d_p95_mm",
    "rainfall_1d_max_mm",
)

TARGET_EVIDENCE_COLUMN = "conditional_strict_linked_event_count"
PLANNED_SENSOR_FEATURES = {
    "soil_moisture": {
        "status": "planned",
        "included_in_training": False,
    }
}
