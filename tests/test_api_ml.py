"""Tests for ML susceptibility and rainfall scenario API endpoints."""

from fastapi.testclient import TestClient

from backend.app.main import app

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
