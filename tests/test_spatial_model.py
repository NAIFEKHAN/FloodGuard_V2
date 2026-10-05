"""Tests for Phase A Spatial Susceptibility Machine Learning artifacts and validation."""

from pathlib import Path
import json
import pandas as pd
import pytest
from xgboost import XGBClassifier

from pipeline.spatial_features import HYDROLOGY_FEATURES, MODEL_FEATURES
from pipeline.train_spatial_model import build_spatial_dataset, join_hydrology_features

ROOT = Path(__file__).resolve().parents[1]
DATA_PROCESSED = ROOT / "data/processed"
MODEL_DIR = ROOT / "model/artifacts"


def test_spatial_dataset_structure_and_integrity() -> None:
    dataset_path = DATA_PROCESSED / "ml_spatial_dataset.csv"
    assert dataset_path.exists(), "ml_spatial_dataset.csv does not exist"

    df = pd.read_csv(dataset_path)
    assert len(df) == 40, f"Expected exactly 40 village records, got {len(df)}"
    assert df["village_lgd_code"].nunique() == 40, "Village LGD codes must be unique"
    assert (df["label_s"] == 1).sum() == 25, "Expected 25 positive villages"
    assert (df["label_s"] == 0).sum() == 15, "Expected 15 unlabeled villages"
    assert df["taluk_name_en"].nunique() == 6, "Expected 6 taluk groups"
    assert not df.isnull().any().any(), "Dataset must not contain null values"
    assert set(HYDROLOGY_FEATURES).issubset(df.columns)
    assert not set(HYDROLOGY_FEATURES).intersection(
        {"label_s", "conditional_strict_linked_event_count"}
    )


def test_spatial_model_artifact_loading_and_scoring() -> None:
    model_path = MODEL_DIR / "spatial_susceptibility_xgboost.json"
    assert model_path.exists(), "spatial_susceptibility_xgboost.json does not exist"

    model = XGBClassifier()
    model.load_model(str(model_path))

    dataset_path = DATA_PROCESSED / "ml_spatial_dataset.csv"
    df = pd.read_csv(dataset_path)

    assert model.get_booster().feature_names == list(MODEL_FEATURES)
    preds = model.predict_proba(df[list(MODEL_FEATURES)])[:, 1]
    assert len(preds) == 40, "Predictions count must match 40 villages"
    assert (preds >= 0.0).all() and (preds <= 1.0).all(), "Predictions must be in [0, 1]"


def test_hydrology_join_uses_exact_lgd_code_and_reports_coverage() -> None:
    model_villages = pd.DataFrame(
        {"village_lgd_code": ["A", "B"], "village_name_en": ["Same", "Other"]}
    )
    hydrology = pd.DataFrame(
        {
            "village_lgd_code": ["A", "B"],
            "village_name_en": ["Other", "Same"],
            **{feature: [1.0, 2.0] for feature in HYDROLOGY_FEATURES},
        }
    )

    joined, coverage = join_hydrology_features(model_villages, hydrology)

    assert joined["village_lgd_code"].tolist() == ["A", "B"]
    assert joined["mean_flow_accumulation_cells"].tolist() == [1.0, 2.0]
    assert coverage["join_key"] == "village_lgd_code"
    assert coverage["matched_count"] == 2
    assert coverage["unmatched_model_count"] == 0


def test_hydrology_join_refuses_unmatched_or_missing_values() -> None:
    model_villages = pd.DataFrame({"village_lgd_code": ["A", "B"]})
    hydrology = pd.DataFrame(
        {
            "village_lgd_code": ["A"],
            **{feature: [1.0] for feature in HYDROLOGY_FEATURES},
        }
    )

    with pytest.raises(ValueError, match="1/2 unmatched LGD codes"):
        join_hydrology_features(model_villages, hydrology)

    hydrology = pd.DataFrame(
        {
            "village_lgd_code": ["A", "B"],
            **{feature: [1.0, 2.0] for feature in HYDROLOGY_FEATURES},
        }
    )
    hydrology.loc[1, HYDROLOGY_FEATURES[0]] = float("nan")
    with pytest.raises(ValueError, match="contain missing values"):
        join_hydrology_features(model_villages, hydrology)


def test_training_dataset_has_complete_exact_id_hydrology_coverage() -> None:
    dataset = build_spatial_dataset()

    assert len(dataset) == 40
    assert dataset["village_lgd_code"].nunique() == 40
    assert dataset.attrs["hydrology_join"]["matched_count"] == 40
    assert dataset.attrs["hydrology_join"]["unmatched_model_count"] == 0
    assert not dataset[list(MODEL_FEATURES)].isna().any().any()


def test_spatial_validation_metrics_exist_and_meet_thresholds() -> None:
    metrics_path = MODEL_DIR / "spatial_validation_metrics.json"
    assert metrics_path.exists(), "spatial_validation_metrics.json does not exist"

    data = json.loads(metrics_path.read_text(encoding="utf-8"))
    assert data["gate_status"] == "CONDITIONAL_ML"
    assert data["sample_size"] == 40
    assert data["labeled_positive_count"] == 25
    assert data["unlabeled_count"] == 15
    assert data["verified_negative_count"] == 0
    assert data["negative_labels_created"] == 0
    assert data["baseline_output"] == "BASELINE MODELED SUSCEPTIBILITY"
    assert data["feature_groups"]["hydrology"] == list(HYDROLOGY_FEATURES)
    assert sum(
        data["feature_importances"][feature] for feature in HYDROLOGY_FEATURES
    ) > 0
    assert data["folds_using_hydrology"] == 5
    assert data["estimator_parameters"]["max_depth"] == 3
    assert data["hydrology_join"]["matched_count"] == 40
    assert data["hydrology_join"]["unmatched_model_count"] == 0
    scores = pd.read_csv(DATA_PROCESSED / "ml_village_susceptibility_scores.csv")
    assert scores["baseline_tier"].isin({"HIGH", "MEDIUM", "LOW"}).all()
    assert (scores["baseline_tier"] == scores["ml_susceptibility_0_100"].map(
        lambda score: "HIGH" if score >= 60 else "MEDIUM" if score >= 30 else "LOW"
    )).all()
    assert "explicit taluk holdout loop" in data["spatial_validation_method"]
    assert data["metric_interpretation"].startswith("ROC-AUC, PR-AUC, and Brier")
    assert data["future_sensor_features"]["soil_moisture"]["included_in_training"] is False

    xgb_metrics = data["model_comparison"]["pu_xgboost"]
    assert xgb_metrics["loto_roc_auc"] >= 0.80, f"Expected ROC-AUC >= 0.80, got {xgb_metrics['loto_roc_auc']}"
    assert xgb_metrics["loto_pr_auc"] >= 0.85, f"Expected PR-AUC >= 0.85, got {xgb_metrics['loto_pr_auc']}"
