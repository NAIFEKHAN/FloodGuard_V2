"""Run leakage-safe taluk holdout evaluation without replacing model artifacts."""

from __future__ import annotations

import json

from pipeline.train_spatial_model import build_spatial_dataset, train_and_evaluate_spatial_models


def main() -> None:
    dataset = build_spatial_dataset()
    results = train_and_evaluate_spatial_models(dataset)
    report = {
        "evaluation_only": True,
        "model_artifacts_replaced": False,
        "sample_size": len(dataset),
        "positive_linked_records": int(dataset["label_s"].sum()),
        "unlabeled_records": int((dataset["label_s"] == 0).sum()),
        "verified_negative_records": 0,
        "validation": "Leave-One-Taluk-Out; threshold metrics use fixed 0.5 cutoff",
        "interpretation": (
            "All label-based metrics compare inventory-linked positives with unlabeled records. "
            "They are proxy metrics, not verified-negative discrimination, calibrated probabilities, "
            "or operational hazard performance. Folds without both classes have undefined ROC-AUC."
        ),
        "models": results["metrics"],
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
