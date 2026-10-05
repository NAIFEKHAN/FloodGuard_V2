"""Tests for ML susceptibility and rainfall scenario API endpoints."""

import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend.app import main
from backend.app.main import app
from pipeline.spatial_features import MODEL_FEATURES

client = TestClient(app)


def test_status_endpoint_reports_pu_spatial_xgboost_model() -> None:
    response = client.get("/api/status")
    assert response.status_code == 200
    payload = response.json()
    assert payload["ml_status"] == "CONDITIONAL_SPATIAL_SUSCEPTIBILITY_MODEL"
    assert payload["data_type"] == "modeled"
    assert "Positive-Unlabeled" in payload["model_type"]
    assert payload["baseline_output"] == "BASELINE MODELED SUSCEPTIBILITY"
    assert payload["scenario_output"] == "SCENARIO-ADJUSTED MODELED RISK"
    assert payload["hydrology_join"]["matched_count"] == 40
    assert payload["hydrology_join"]["join_key"] == "village_lgd_code"
    assert payload["current_conditions"]["status"] == "not_in_model"
    assert payload["estimator_parameters"]["max_depth"] == 3
    assert payload["loto_roc_auc"] >= 0.80
    assert payload["susceptibility_scores"] == "AVAILABLE"
    assert payload["scenario_engine"] == "AVAILABLE"


def test_ml_susceptibility_endpoint_returns_40_villages() -> None:
    response = client.get("/api/ml-susceptibility")
    assert response.status_code == 200
    payload = response.json()
    assert payload["classification"] == "SPATIAL_VILLAGE_SUSCEPTIBILITY_PU_MODEL"
    assert payload["data_type"] == "modeled"
    assert payload["record_count"] == 40
    assert payload["labeled_positive_count"] == 25
    assert payload["unlabeled_count"] == 15
    assert payload["baseline_output"] == "BASELINE MODELED SUSCEPTIBILITY"
    assert payload["feature_groups"]["hydrology"]
    assert sum(
        payload["feature_importances"][feature]
        for feature in payload["feature_groups"]["hydrology"]
    ) > 0
    assert payload["folds_using_hydrology"] == 5
    assert payload["training_coverage"]["matched_count"] == 40
    assert len(payload["records"]) == 40

    records = payload["records"]
    lgds = {r["village_lgd_code"] for r in records}
    assert len(lgds) == 40

    for r in records:
        assert r["pu_status"] in ("POSITIVE", "UNLABELED")
        assert 0.0 <= float(r["ml_susceptibility_0_100"]) <= 100.0
        assert r["baseline_susceptibility_0_100"] == r["ml_susceptibility_0_100"]
        assert r["baseline_tier"] in ("HIGH", "MEDIUM", "LOW")
        assert "drainage_density_km_per_km2" in r


def test_rainfall_scenario_endpoint_baseline() -> None:
    response = client.get("/api/rainfall-scenario?scenario=baseline")
    assert response.status_code == 200
    payload = response.json()
    assert payload["classification"] == "RAINFALL_SCENARIO_DEMONSTRATION_SIMULATION"
    assert payload["data_type"] == "simulated"
    assert payload["mode"] == "scenario"
    assert payload["output_data_type"] == "modeled"
    assert payload["baseline_output"] == "BASELINE MODELED SUSCEPTIBILITY"
    assert payload["scenario_output"] == "SCENARIO-ADJUSTED MODELED RISK"
    assert payload["scenario_key"] == "baseline"
    assert payload["record_count"] == 40
    assert len(payload["records"]) == 40

    for r in payload["records"]:
        assert 0.0 <= float(r["scenario_susceptibility_0_100"]) <= 100.0
        assert r["scenario_tier"] in ("HIGH", "MEDIUM", "LOW")
        assert r["output_classification"] == "SCENARIO_ADJUSTED_MODELED_RISK"
        assert r["baseline_output_classification"] == "BASELINE_MODELED_SUSCEPTIBILITY"
        assert float(r["susceptibility_delta"]) == 0.0


def test_rainfall_scenario_endpoint_extreme() -> None:
    response = client.get("/api/rainfall-scenario?scenario=extreme")
    assert response.status_code == 200
    payload = response.json()
    assert payload["scenario_key"] == "extreme"
    assert payload["rainfall_factor"] == 2.2
    assert len(payload["records"]) == 40

    scores = [float(r["scenario_susceptibility_score_0_100"]) if "scenario_susceptibility_score_0_100" in r else float(r["scenario_susceptibility_0_100"]) for r in payload["records"]]
    assert max(scores) <= 100.0
    assert min(scores) >= 0.0


def test_rainfall_scenario_endpoint_custom_multiplier() -> None:
    response = client.get("/api/rainfall-scenario?multiplier=1.8")
    assert response.status_code == 200
    payload = response.json()
    assert payload["rainfall_factor"] == 1.8
    assert len(payload["records"]) == 40


@pytest.mark.parametrize(
    ("forecast_status", "status_label"),
    [("live", "LIVE"), ("cached", "CACHED")],
)
def test_open_meteo_forecast_endpoint_passes_24_hour_rainfall_to_existing_model(
    monkeypatch, forecast_status, status_label
) -> None:
    villages = main.spatial_dataset()
    forecast_villages = [
        {
            "village_lgd_code": row["village_lgd_code"],
            "village_name_en": f"Village {index}",
            "latitude": 11.5,
            "longitude": 76.75,
            "source": "Open-Meteo",
            "status": forecast_status,
            "updated_at": "2026-10-05T00:00:00+00:00",
            "current_precipitation_mm": 0.0,
            "chosen_feature_value_mm": 12.5 + index,
            "forecast": [],
            "next_24h_rainfall_mm": 12.5 + index,
            "forecast_period_start": "2026-10-05T03:00:00+00:00",
            "forecast_period_end": "2026-10-06T00:00:00+00:00",
        }
        for index, row in enumerate(villages)
    ]
    monkeypatch.setattr(
        main,
        "fetch_forecast",
        lambda force_refresh=False: {
            "status": forecast_status,
            "available": True,
            "forecast_status": status_label,
            "source": "Open-Meteo",
            "retrieved_at": "2026-10-05T00:00:00+00:00",
            "updated_at": "2026-10-05T00:00:00+00:00",
            "record_count": 40,
            "villages": forecast_villages,
        },
    )

    class RecordingModel:
        def __init__(self) -> None:
            self.inputs = []

        def predict_proba(self, values):
            self.inputs.append(values.copy())
            rainfall = values[:, MODEL_FEATURES.index("rainfall_1d_max_mm")]
            probability = np.clip(rainfall / 500.0, 0.05, 0.95)
            return np.column_stack((1.0 - probability, probability))

    model = RecordingModel()
    monkeypatch.setattr(main, "ml_model", lambda: model)

    response = client.get("/api/weather/forecast")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == forecast_status
    assert payload["mode"] == "auto"
    assert payload["source"] == "Open-Meteo"
    assert payload["record_count"] == 40
    assert len(payload["records"]) == 40
    assert all(
        record["output_classification"] == "FORECAST_ADJUSTED_MODELED_RISK"
        for record in payload["records"]
    )
    assert payload["records"][0]["weather_source"] == "Open-Meteo"
    assert payload["records"][0]["weather_status"] == forecast_status
    rainfall_index = MODEL_FEATURES.index("rainfall_1d_max_mm")
    for index, village in enumerate(forecast_villages):
        baseline_input = model.inputs[index * 2][0]
        scenario_input = model.inputs[index * 2 + 1][0]
        baseline_rainfall = float(villages[index]["rainfall_1d_max_mm"])
        expected_factor = village["chosen_feature_value_mm"] / baseline_rainfall
        result = next(
            row for row in payload["records"]
            if str(row["village_lgd_code"]) == str(village["village_lgd_code"])
        )
        assert scenario_input[rainfall_index] == pytest.approx(
            village["chosen_feature_value_mm"]
        )
        assert result["rainfall_factor"] == pytest.approx(expected_factor)
        assert not np.array_equal(baseline_input, scenario_input)

    first_record = next(
        row for row in payload["records"]
        if str(row["village_lgd_code"]) == str(forecast_villages[0]["village_lgd_code"])
    )
    assert first_record["scenario_susceptibility_0_100"] != (
        first_record["baseline_susceptibility_0_100"]
    )
    assert not np.array_equal(model.inputs[1][0], model.inputs[3][0])

    selected_code = forecast_villages[0]["village_lgd_code"]
    selected_response = client.get(
        f"/api/weather/forecast?village_lgd_code={selected_code}"
    )
    assert selected_response.status_code == 200
    selected_payload = selected_response.json()
    assert selected_payload["record_count"] == 1
    assert selected_payload["selected_village_lgd_code"] == selected_code
    assert selected_payload["records"][0]["village_lgd_code"] == selected_code
    assert selected_payload["village"] == forecast_villages[0]["village_name_en"]
    assert selected_payload["next_24h_rainfall_mm"] == (
        selected_payload["records"][0]["next_24h_rainfall_mm"]
    )
