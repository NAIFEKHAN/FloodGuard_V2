"""Pipeline script to build the spatial ML dataset and train the spatial susceptibility model.

Uses a Positive-Unlabeled (PU) spatial learning formulation with Leave-One-Taluk-Out (LOTO)
spatial cross-validation across the 6 Nilgiris taluks.
"""

from __future__ import annotations

import json
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_auc_score, average_precision_score, brier_score_loss
from xgboost import XGBClassifier

from pipeline.run_rainfall_scenario import classify_scenario_tier
from pipeline.spatial_features import (
    FEATURE_GROUPS,
    HYDROLOGY_FEATURES,
    MODEL_FEATURES,
    PLANNED_SENSOR_FEATURES,
    TARGET_EVIDENCE_COLUMN,
)

ROOT = Path(__file__).resolve().parents[1]
DATA_PROCESSED = ROOT / "data/processed"
MODEL_DIR = ROOT / "model/artifacts"


def _validate_unique_lgd_codes(frame: pd.DataFrame, source_name: str) -> None:
    if "village_lgd_code" not in frame:
        raise ValueError(f"{source_name} is missing the canonical village_lgd_code column.")
    if frame["village_lgd_code"].isna().any():
        raise ValueError(f"{source_name} contains empty village_lgd_code values.")
    if frame["village_lgd_code"].duplicated().any():
        duplicates = sorted(
            frame.loc[frame["village_lgd_code"].duplicated(keep=False), "village_lgd_code"]
            .astype(str)
            .unique()
        )
        raise ValueError(f"{source_name} contains duplicate village LGD codes: {duplicates}")


def join_hydrology_features(
    model_villages: pd.DataFrame,
    hydrology_villages: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, object]]:
    """Join available Phase 3 metrics using only the canonical LGD identifier."""
    _validate_unique_lgd_codes(model_villages, "Model training population")
    _validate_unique_lgd_codes(hydrology_villages, "Village hydrology features")

    required_columns = ["village_lgd_code", *HYDROLOGY_FEATURES]
    missing_columns = [column for column in required_columns if column not in hydrology_villages]
    if missing_columns:
        raise ValueError(
            "Village hydrology features are missing required columns: "
            f"{missing_columns}"
        )

    hydrology_subset = hydrology_villages[required_columns].copy()
    joined = model_villages.merge(
        hydrology_subset,
        on="village_lgd_code",
        how="left",
        validate="one_to_one",
        indicator="_hydrology_join",
    )
    matched = joined["_hydrology_join"] == "both"
    unmatched_codes = sorted(
        joined.loc[~matched, "village_lgd_code"].astype(str).tolist()
    )
    model_codes = set(model_villages["village_lgd_code"].astype(str))
    hydrology_codes = set(hydrology_villages["village_lgd_code"].astype(str))
    hydrology_only_codes = sorted(hydrology_codes - model_codes)
    coverage: dict[str, object] = {
        "model_population_count": int(len(model_villages)),
        "matched_count": int(matched.sum()),
        "unmatched_model_count": len(unmatched_codes),
        "unmatched_model_lgd_codes": unmatched_codes,
        "hydrology_only_count": len(hydrology_only_codes),
        "hydrology_only_lgd_codes": hydrology_only_codes,
        "join_key": "village_lgd_code",
        "missing_data_strategy": "No imputation; all training-population villages require hydrology coverage.",
    }
    if unmatched_codes:
        raise ValueError(
            "Hydrology coverage is incomplete for the model training population "
            f"({len(unmatched_codes)}/{len(model_villages)} unmatched LGD codes: "
            f"{unmatched_codes}); no rows were dropped or imputed."
        )

    joined = joined.drop(columns="_hydrology_join")
    missing_values = joined[list(HYDROLOGY_FEATURES)].isna()
    if missing_values.any().any():
        affected = sorted(
            joined.loc[missing_values.any(axis=1), "village_lgd_code"].astype(str).tolist()
        )
        raise ValueError(
            "Hydrology features contain missing values for matched training villages "
            f"{affected}; no rows were dropped or imputed."
        )
    for feature in HYDROLOGY_FEATURES:
        joined[feature] = pd.to_numeric(joined[feature], errors="raise")
    joined.attrs["hydrology_join"] = coverage
    return joined, coverage


def _merge_training_source(
    left: pd.DataFrame,
    right: pd.DataFrame,
    *,
    source_name: str,
) -> pd.DataFrame:
    merged = left.merge(
        right,
        on="village_lgd_code",
        how="left",
        validate="one_to_one",
        indicator="_source_join",
    )
    unmatched = sorted(
        merged.loc[merged["_source_join"] != "both", "village_lgd_code"]
        .astype(str)
        .tolist()
    )
    if unmatched:
        raise ValueError(
            f"{source_name} is missing training-population LGD codes {unmatched}; "
            "no villages were dropped."
        )
    return merged.drop(columns="_source_join")


def build_spatial_dataset() -> pd.DataFrame:
    """Assemble exact-ID terrain, hydrology, and historical rainfall features."""
    tf_path = DATA_PROCESSED / "terrain_features_villages.csv"
    vtf_path = DATA_PROCESSED / "village_time_features.csv"
    ev_path = DATA_PROCESSED / "experimental_hazard_index.csv"
    vm_path = DATA_PROCESSED / "nilgiris_villages.csv"
    hydrology_path = DATA_PROCESSED / "hydrology/village_hydrology_features.csv"

    tf = pd.read_csv(tf_path)
    vtf = pd.read_csv(vtf_path)
    ev = pd.read_csv(ev_path)
    vm = pd.read_csv(vm_path)
    hydrology = pd.read_csv(hydrology_path)
    for frame, source_name in (
        (tf, "Village terrain features"),
        (vtf.drop_duplicates("village_lgd_code"), "Village time features"),
        (ev, "Experimental hazard index"),
        (vm.drop_duplicates("village_lgd_code"), "Village master"),
    ):
        _validate_unique_lgd_codes(frame, source_name)

    # 1. Aggregate 5-year historical rainfall features per village
    rain_agg = vtf.groupby("village_lgd_code").agg(
        rainfall_7d_p95_mm=("rainfall_7d_mm", lambda x: float(np.nanpercentile(x, 95))),
        rainfall_3d_p95_mm=("rainfall_3d_mm", lambda x: float(np.nanpercentile(x, 95))),
        rainfall_1d_max_mm=("rainfall_1d_mm", "max"),
        rainfall_annual_mean_mm=("rainfall_1d_mm", lambda x: float(x.sum() / 5.0)),
    ).reset_index()

    # 2. Merge terrain summaries
    df = tf[
        [
            "village_lgd_code",
            "district_lgd_code",
            "taluk_lgd_code",
            "village_name_en",
            "elevation_mean_m",
            "elevation_min_m",
            "elevation_max_m",
            "slope_mean_deg",
            "slope_max_deg",
        ]
    ]
    df = _merge_training_source(
        df,
        rain_agg,
        source_name="Historical rainfall aggregation",
    )

    df["elevation_range_m"] = df["elevation_max_m"] - df["elevation_min_m"]

    # 3. Merge event evidence, baseline hazard index, and administrative taluk names
    df = _merge_training_source(
        df,
        ev[["village_lgd_code", "conditional_strict_linked_event_count", "experimental_hazard_index_0_100"]],
        source_name="Historical event evidence",
    )
    df = df.merge(
        vm[["village_lgd_code", "taluk_name_en"]].drop_duplicates(),
        on="village_lgd_code",
        how="left",
        validate="one_to_one",
        indicator="_village_master_join",
    )
    missing_master = sorted(
        df.loc[df["_village_master_join"] != "both", "village_lgd_code"]
        .astype(str)
        .tolist()
    )
    if missing_master:
        raise ValueError(
            "Village master is missing training-population LGD codes "
            f"{missing_master}; no villages were dropped."
        )
    df = df.drop(columns="_village_master_join")
    df, hydrology_coverage = join_hydrology_features(df, hydrology)

    # A missing inventory link is unlabeled evidence, not a verified negative.
    df["label_s"] = (df[TARGET_EVIDENCE_COLUMN] > 0).astype(int)
    df["pu_status"] = np.where(df["label_s"] == 1, "POSITIVE", "UNLABELED")

    # 5. Add Provenance
    df["terrain_feature_source"] = "data/processed/terrain_features_villages.csv (SRTM 30m DEM)"
    df["rainfall_feature_source"] = "data/processed/village_time_features.csv (IMD 0.25deg gridded 2017,2019,2022-2024)"
    df["event_evidence_source"] = "data/processed/event_village_linkage.csv (GSI/NLFC National Inventory)"
    df["hydrology_feature_source"] = (
        "data/processed/hydrology/village_hydrology_features.csv (SRTM-derived; exact LGD join)"
    )
    df["boundary_source"] = "data/raw/admin/vb_soi_tn.kmz (40 exact LGD polygon matches)"

    # Validation checks
    assert len(df) == 40, f"Expected 40 village records, got {len(df)}"
    assert df["village_lgd_code"].nunique() == 40, "Duplicate village LGD codes found"
    assert (df["label_s"] == 1).sum() == 25, f"Expected 25 positive villages, got {(df['label_s'] == 1).sum()}"
    assert (df["label_s"] == 0).sum() == 15, f"Expected 15 unlabeled villages, got {(df['label_s'] == 0).sum()}"
    assert df["taluk_name_en"].nunique() == 6, f"Expected 6 taluks, got {df['taluk_name_en'].nunique()}"
    assert not df.isnull().any().any(), "Unexpected missing values in spatial dataset"
    df.attrs["hydrology_join"] = hydrology_coverage

    return df


def train_and_evaluate_spatial_models(df: pd.DataFrame) -> dict[str, object]:
    """Train models using 6-fold Leave-One-Taluk-Out (LOTO) spatial cross-validation."""
    feature_cols = list(MODEL_FEATURES)
    missing_features = [feature for feature in feature_cols if feature not in df]
    if missing_features:
        raise ValueError(f"Training table is missing model features: {missing_features}")
    if df[feature_cols].isna().any().any():
        missing_rows = df.loc[df[feature_cols].isna().any(axis=1), "village_lgd_code"]
        raise ValueError(
            "Training features contain missing values; no rows are imputed or dropped. "
            f"Affected LGD codes: {sorted(missing_rows.astype(str).tolist())}"
        )

    taluks = sorted(df["taluk_name_en"].unique())
    y_true = df["label_s"].values

    models = {
        "pu_logistic_regression": {
            "name": "PU-Weighted Logistic Regression",
            "oof_preds": np.zeros(len(df)),
        },
        "pu_random_forest": {
            "name": "PU-Weighted Random Forest",
            "oof_preds": np.zeros(len(df)),
        },
        "pu_xgboost": {
            "name": "PU-Weighted XGBoost Classifier",
            "oof_preds": np.zeros(len(df)),
        },
    }

    fold_details = []

    for fold_idx, holdout_taluk in enumerate(taluks, 1):
        train_mask = (df["taluk_name_en"] != holdout_taluk).values
        test_mask = (df["taluk_name_en"] == holdout_taluk).values

        X_train = df.loc[train_mask, feature_cols]
        y_train = df.loc[train_mask, "label_s"].values
        X_test = df.loc[test_mask, feature_cols]
        y_test = df.loc[test_mask, "label_s"].values

        # Compute PU sample weights: positive instances = 1.0, unlabeled instances = n_pos / n_unl
        n_pos = int(np.sum(y_train == 1))
        n_unl = int(np.sum(y_train == 0))
        w_unl = float(n_pos / max(n_unl, 1))
        sample_weights = np.where(y_train == 1, 1.0, w_unl)

        # Standardize features for linear model
        scaler = StandardScaler()
        X_train_s = scaler.fit_transform(X_train)
        X_test_s = scaler.transform(X_test)

        # 1. PU Logistic Regression
        m_lr = LogisticRegression(C=0.5, random_state=42, max_iter=200)
        m_lr.fit(X_train_s, y_train, sample_weight=sample_weights)
        models["pu_logistic_regression"]["oof_preds"][test_mask] = m_lr.predict_proba(X_test_s)[:, 1]

        # 2. PU Random Forest
        m_rf = RandomForestClassifier(n_estimators=50, max_depth=2, random_state=42)
        m_rf.fit(X_train, y_train, sample_weight=sample_weights)
        models["pu_random_forest"]["oof_preds"][test_mask] = m_rf.predict_proba(X_test)[:, 1]

        # 3. PU XGBoost
        m_xgb = XGBClassifier(
            n_estimators=35,
            max_depth=3,
            learning_rate=0.08,
            random_state=42,
            eval_metric="logloss",
            n_jobs=1,
        )
        m_xgb.fit(X_train, y_train, sample_weight=sample_weights)
        models["pu_xgboost"]["oof_preds"][test_mask] = m_xgb.predict_proba(X_test)[:, 1]
        fold_xgb_importance = m_xgb.get_booster().get_score(importance_type="weight")
        fold_hydrology_features = [
            feature
            for feature in HYDROLOGY_FEATURES
            if fold_xgb_importance.get(feature, 0.0) > 0
        ]

        fold_details.append(
            {
                "fold": fold_idx,
                "holdout_taluk": holdout_taluk,
                "train_samples": int(np.sum(train_mask)),
                "test_samples": int(np.sum(test_mask)),
                "test_positives": int(np.sum(y_test == 1)),
                "test_unlabeled": int(np.sum(y_test == 0)),
                "xgboost_hydrology_features_used": fold_hydrology_features,
            }
        )

    # Compute overall out-of-fold metrics
    metrics = {}
    for m_key, m_info in models.items():
        preds = m_info["oof_preds"]
        roc_auc = float(roc_auc_score(y_true, preds))
        pr_auc = float(average_precision_score(y_true, preds))
        brier = float(brier_score_loss(y_true, preds))

        metrics[m_key] = {
            "model_name": m_info["name"],
            "loto_roc_auc": round(roc_auc, 4),
            "loto_pr_auc": round(pr_auc, 4),
            "loto_brier_score": round(brier, 4),
        }

    # Train Final Production Model on all 40 villages
    total_pos = int(np.sum(y_true == 1))
    total_unl = int(np.sum(y_true == 0))
    full_weights = np.where(y_true == 1, 1.0, float(total_pos / total_unl))

    final_xgb = XGBClassifier(
        n_estimators=35,
        max_depth=3,
        learning_rate=0.08,
        random_state=42,
        eval_metric="logloss",
        n_jobs=1,
    )
    final_xgb.fit(df[feature_cols], y_true, sample_weight=full_weights)

    feature_importances = {
        feat: round(float(imp), 4)
        for feat, imp in zip(feature_cols, final_xgb.feature_importances_)
    }

    # Generate model susceptibility scores (0-100 scale)
    final_preds = final_xgb.predict_proba(df[feature_cols])[:, 1]
    df["ml_susceptibility_raw"] = final_preds
    df["ml_susceptibility_0_100"] = np.round(final_preds * 100.0, 2)
    df["baseline_susceptibility_raw"] = final_preds
    df["baseline_susceptibility_0_100"] = np.round(final_preds * 100.0, 2)
    df["baseline_tier"] = [
        classify_scenario_tier(score * 100.0) for score in final_preds
    ]
    df["ml_loto_oof_score_0_100"] = np.round(models["pu_xgboost"]["oof_preds"] * 100.0, 2)

    return {
        "features": feature_cols,
        "feature_groups": {name: list(features) for name, features in FEATURE_GROUPS.items()},
        "metrics": metrics,
        "fold_details": fold_details,
        "feature_importances": feature_importances,
        "scored_df": df,
        "final_model": final_xgb,
    }


def main() -> None:
    """Build dataset, run spatial cross-validation, and serialize artifacts."""
    MODEL_DIR.mkdir(parents=True, exist_ok=True)

    print("Building spatial ML dataset for 40 validated villages...")
    df = build_spatial_dataset()

    dataset_out = DATA_PROCESSED / "ml_spatial_dataset.csv"
    df.to_csv(dataset_out, index=False)
    print(f"Saved: {dataset_out}")

    print("\nRunning Leave-One-Taluk-Out (LOTO) Spatial Validation...")
    results = train_and_evaluate_spatial_models(df)

    scored_df = results["scored_df"]
    scores_out = DATA_PROCESSED / "ml_village_susceptibility_scores.csv"
    scored_df[
        [
            "village_lgd_code",
            "taluk_name_en",
            "village_name_en",
            "pu_status",
            "conditional_strict_linked_event_count",
            "ml_loto_oof_score_0_100",
            "baseline_susceptibility_0_100",
            "baseline_tier",
            "ml_susceptibility_0_100",
            "experimental_hazard_index_0_100",
            *HYDROLOGY_FEATURES,
        ]
    ].to_csv(scores_out, index=False)
    print(f"Saved: {scores_out}")

    # Save Final XGBoost Model in JSON format
    model_json_path = MODEL_DIR / "spatial_susceptibility_xgboost.json"
    results["final_model"].save_model(str(model_json_path))
    print(f"Saved: {model_json_path}")

    # Save Validation Report JSON
    metadata = {
        "gate_status": "CONDITIONAL_ML",
        "model_type": "Positive-Unlabeled (PU)-weighted XGBoost; positive-versus-unlabeled proxy",
        "estimator_parameters": {
            "n_estimators": 35,
            "max_depth": 3,
            "learning_rate": 0.08,
            "random_state": 42,
            "eval_metric": "logloss",
            "n_jobs": 1,
        },
        "baseline_output": "BASELINE MODELED SUSCEPTIBILITY",
        "baseline_definition": (
            "Relative modeled susceptibility from terrain, DEM-derived hydrology, "
            "and historical rainfall features; not a real-time prediction."
        ),
        "current_conditions": {
            "status": "not_in_model",
            "reason": (
                "The backend does not ingest validated current rainfall; browser-fetched "
                "weather is contextual only."
            ),
        },
        "scenario_output": "SCENARIO-ADJUSTED MODELED RISK",
        "formulation": (
            "Weighted binary XGBoost proxy: linked-event villages are positive; "
            "villages without linked inventory evidence remain unlabeled but are encoded "
            "as class 0 for estimator fitting and proxy metrics."
        ),
        "sample_size": 40,
        "labeled_positive_count": 25,
        "unlabeled_count": 15,
        "verified_negative_count": 0,
        "negative_labels_created": 0,
        "unlabeled_records_encoded_as_class_0": True,
        "metric_interpretation": (
            "ROC-AUC, PR-AUC, and Brier score compare linked positives with unlabeled "
            "records as a proxy task; they are not verified-negative discrimination, "
            "calibrated probabilities, or operational-risk validation."
        ),
        "spatial_validation_method": "6-Fold Leave-One-Taluk-Out (LOTO; explicit taluk holdout loop)",
        "taluk_groups": sorted(df["taluk_name_en"].unique()),
        "features": results["features"],
        "feature_groups": results["feature_groups"],
        "target_source_column": TARGET_EVIDENCE_COLUMN,
        "historical_evidence_predictor_features": [],
        "excluded_training_columns": [
            "conditional_strict_linked_event_count",
            "experimental_hazard_index_0_100",
        ],
        "future_sensor_features": PLANNED_SENSOR_FEATURES,
        "hydrology_join": df.attrs.get("hydrology_join", {}),
        "scenario_adjusted_rainfall_features": [
            "rainfall_7d_p95_mm",
            "rainfall_3d_p95_mm",
            "rainfall_1d_max_mm",
        ],
        "unchanged_historical_rainfall_feature": "rainfall_annual_mean_mm",
        "feature_importances": results["feature_importances"],
        "folds_using_hydrology": sum(
            bool(fold["xgboost_hydrology_features_used"])
            for fold in results["fold_details"]
        ),
        "model_comparison": results["metrics"],
        "fold_details": results["fold_details"],
        "governance_note": "Scores represent spatial demonstration susceptibility ranking, not live operational flood/landslide predictions or calibrated probabilities.",
    }

    metrics_out = MODEL_DIR / "spatial_validation_metrics.json"
    metrics_out.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(f"Saved: {metrics_out}")

    print("\n=== LOTO Spatial Validation Summary ===")
    for k, v in results["metrics"].items():
        print(f"{v['model_name']}: ROC-AUC={v['loto_roc_auc']}, PR-AUC={v['loto_pr_auc']}, Brier={v['loto_brier_score']}")
    print("\nFeature Importances:")
    for f, imp in results["feature_importances"].items():
        print(f"  {f}: {imp * 100:.1f}%")


if __name__ == "__main__":
    main()
