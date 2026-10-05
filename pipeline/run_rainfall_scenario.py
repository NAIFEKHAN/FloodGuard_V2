"""Rainfall Scenario Simulation Engine for FloodGuard.

Evaluates village-level susceptibility response under simulated rainfall scenarios
using the trained Phase A spatial XGBoost model without mutating underlying source data.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import numpy as np
import pandas as pd
from xgboost import XGBClassifier

from pipeline.spatial_features import (
    MODEL_FEATURES,
    SCENARIO_RAINFALL_FEATURES,
)

ROOT = Path(__file__).resolve().parents[1]
DATA_PROCESSED = ROOT / "data/processed"
MODEL_DIR = ROOT / "model/artifacts"

FEATURE_COLS = list(MODEL_FEATURES)

PRESET_SCENARIOS = {
    "moderate": {
        "name": "Moderate / Normal Monsoon",
        "multiplier": 0.70,
        "description": "Subdued monsoon / 30% reduction from historical 95th-percentile rainfall.",
    },
    "baseline": {
        "name": "Historical Baseline Climatology",
        "multiplier": 1.00,
        "description": "5-year historical IMD 0.25deg baseline (2017, 2019, 2022-2024).",
    },
    "heavy": {
        "name": "Heavy Monsoon Surge",
        "multiplier": 1.50,
        "description": "50% surge above historical 95th-percentile rainfall across all antecedent windows.",
    },
    "extreme": {
        "name": "Extreme Cloudburst (2019 Disaster Equivalent)",
        "multiplier": 2.20,
        "description": "220% multi-day antecedent saturation matching the August 2019 regional disaster event.",
    },
}


def load_model_and_dataset() -> tuple[XGBClassifier, pd.DataFrame]:
    """Load the trained XGBoost spatial susceptibility model and 40-village dataset."""
    model_path = MODEL_DIR / "spatial_susceptibility_xgboost.json"
    dataset_path = DATA_PROCESSED / "ml_spatial_dataset.csv"

    if not model_path.exists():
        raise FileNotFoundError(f"Model file not found at {model_path}")
    if not dataset_path.exists():
        raise FileNotFoundError(f"Dataset file not found at {dataset_path}")

    model = XGBClassifier()
    model.load_model(str(model_path))

    df = pd.read_csv(dataset_path)
    if len(df) != 40:
        raise ValueError(f"Expected exactly 40 village records, got {len(df)}")

    return model, df


def classify_scenario_tier(score_0_100: float) -> str:
    """Assign relative demonstration tier. High >= 60, Medium 30-59.9, Low < 30."""
    if score_0_100 >= 60.0:
        return "HIGH"
    elif score_0_100 >= 30.0:
        return "MEDIUM"
    else:
        return "LOW"


def evaluate_scenario(
    df: pd.DataFrame,
    model: XGBClassifier,
    scenario_key: str = "heavy",
    multiplier: float | None = None,
    custom_rainfall_1d_mm: float | None = None,
    custom_rainfall_7d_mm: float | None = None,
) -> pd.DataFrame:
    """Compare baseline susceptibility with an in-memory rainfall scenario."""
    scenario_df = df.copy(deep=True)
    missing_features = [feature for feature in FEATURE_COLS if feature not in df]
    if missing_features:
        raise ValueError(f"Scenario table is missing model features: {missing_features}")
    if df[FEATURE_COLS].isna().any().any():
        raise ValueError("Scenario model features contain missing values; no imputation is applied.")

    # Determine scenario multiplier or explicit override
    if multiplier is None:
        if scenario_key in PRESET_SCENARIOS:
            factor = PRESET_SCENARIOS[scenario_key]["multiplier"]
            scenario_name = PRESET_SCENARIOS[scenario_key]["name"]
        else:
            factor = 1.00
            scenario_name = f"Custom ({scenario_key})"
    else:
        factor = float(multiplier)
        scenario_name = f"Custom ({factor:.2f}x Multiplier)"

    # Compute baseline scores
    baseline_raw = model.predict_proba(df[FEATURE_COLS].values)[:, 1]
    baseline_0_100 = np.round(baseline_raw * 100.0, 2)

    # Scenario precipitation does not rewrite the historical annual climatology.
    for col in SCENARIO_RAINFALL_FEATURES:
        scenario_df[col] = scenario_df[col] * factor

    if custom_rainfall_1d_mm is not None:
        scenario_df["rainfall_1d_max_mm"] = float(custom_rainfall_1d_mm)
    if custom_rainfall_7d_mm is not None:
        scenario_df["rainfall_7d_p95_mm"] = float(custom_rainfall_7d_mm)

    # Compute scenario scores
    scenario_raw = model.predict_proba(scenario_df[FEATURE_COLS].values)[:, 1]
    scenario_0_100 = np.round(scenario_raw * 100.0, 2)
    delta_score = np.round(scenario_0_100 - baseline_0_100, 2)

    # Assemble output table
    results = pd.DataFrame(
        {
            "scenario_key": scenario_key,
            "scenario_name": scenario_name,
            "rainfall_factor": round(factor, 2),
            "output_classification": "SCENARIO_ADJUSTED_MODELED_RISK",
            "baseline_output_classification": "BASELINE_MODELED_SUSCEPTIBILITY",
            "village_lgd_code": df["village_lgd_code"],
            "taluk_name_en": df["taluk_name_en"],
            "village_name_en": df["village_name_en"],
            "pu_status": df["pu_status"],
            "elevation_mean_m": np.round(df["elevation_mean_m"], 1),
            "slope_mean_deg": np.round(df["slope_mean_deg"], 1),
            "baseline_rainfall_7d_p95_mm": np.round(df["rainfall_7d_p95_mm"], 1),
            "scenario_rainfall_7d_p95_mm": np.round(scenario_df["rainfall_7d_p95_mm"], 1),
            "baseline_susceptibility_0_100": baseline_0_100,
            "baseline_tier": [classify_scenario_tier(s) for s in baseline_0_100],
            "scenario_susceptibility_0_100": scenario_0_100,
            "susceptibility_delta": delta_score,
            "scenario_tier": [classify_scenario_tier(s) for s in scenario_0_100],
            "scenario_rainfall_annual_mean_mm": np.round(
                scenario_df["rainfall_annual_mean_mm"], 1
            ),
            "governance_classification": "DEMONSTRATION_SCENARIO_NOT_LIVE_WARNING",
        }
    )

    return results


def run_all_presets() -> pd.DataFrame:
    """Run all preset scenarios and save composite comparison artifact."""
    model, df = load_model_and_dataset()
    all_results = []

    for key in ["moderate", "baseline", "heavy", "extreme"]:
        res = evaluate_scenario(df, model, scenario_key=key)
        all_results.append(res)

    composite_df = pd.concat(all_results, ignore_index=True)
    out_path = DATA_PROCESSED / "rainfall_scenario_results.csv"
    composite_df.to_csv(out_path, index=False)
    print(f"Saved: {out_path} ({len(composite_df)} total scenario-village rows)")

    return composite_df


def main() -> None:
    parser = argparse.ArgumentParser(description="Run FloodGuard rainfall scenario engine.")
    parser.add_argument(
        "--scenario",
        choices=["moderate", "baseline", "heavy", "extreme", "all"],
        default="all",
        help="Preset scenario to run.",
    )
    parser.add_argument("--multiplier", type=float, default=None, help="Custom rainfall multiplier.")
    parser.add_argument("--r1d", type=float, default=None, help="Custom 1-day rainfall intensity (mm).")
    parser.add_argument("--r7d", type=float, default=None, help="Custom 7-day antecedent rainfall (mm).")

    args = parser.parse_args()

    if args.scenario == "all" and args.multiplier is None:
        run_all_presets()
    else:
        model, df = load_model_and_dataset()
        res = evaluate_scenario(
            df,
            model,
            scenario_key=args.scenario if args.multiplier is None else "custom",
            multiplier=args.multiplier,
            custom_rainfall_1d_mm=args.r1d,
            custom_rainfall_7d_mm=args.r7d,
        )
        print(res.head(10).to_string())


if __name__ == "__main__":
    main()
