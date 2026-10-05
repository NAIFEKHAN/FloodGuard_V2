"""Live system status and data-coverage API behavior."""

from fastapi.testclient import TestClient

from backend.app import sensor_store, warning_api
from backend.app.main import app

client = TestClient(app)


def test_system_coverage_reports_artifact_derived_counts() -> None:
    response = client.get("/api/system/coverage")

    assert response.status_code == 200
    payload = response.json()
    coverage = payload["coverage"]
    assert payload["status"] == "available"
    assert coverage["village_master"]["total"] == 102
    assert coverage["village_boundaries"]["validated_model_polygons"] == 40
    assert coverage["model"]["supported_villages"] == 40
    assert coverage["hydrology"]["model_villages_matched"] == 40
    assert coverage["historical_rainfall"]["supported_villages"] == 40
    assert coverage["current_rainfall"]["status"] == "unavailable"
    assert coverage["rainfall_scenario"]["classification"] == "simulated"
    assert len(payload["feature_audit"]) >= 10


def test_system_status_distinguishes_backend_health_from_missing_inputs() -> None:
    response = client.get("/api/system/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["components"]["backend"]["status"] == "available"
    assert payload["components"]["current_rainfall"]["status"] == "unavailable"
    assert payload["components"]["shelters"]["status"] == "unavailable"
    assert payload["components"]["routing"]["status"] == "degraded"
    assert payload["coverage"]["shelters"]["verified_records"] == 0
    assert payload["coverage"]["shelters"]["route_eligible"] == 0
    assert payload["coverage"]["sensors"]["registered_real"] == 0
    assert payload["coverage"]["sensors"]["online_real"] == 0
    assert payload["coverage"]["sensors"]["status"] == "unavailable"
    assert "credentials" not in response.text.lower()
    assert {item["feature"] for item in payload["feature_audit"]}


def test_optional_sensor_store_failure_does_not_hide_system_status(
    monkeypatch,
) -> None:
    def unavailable_sensor_store():
        raise sensor_store.sqlite3.OperationalError("sensor database is unavailable")

    monkeypatch.setattr(sensor_store, "list_sensors", unavailable_sensor_store)
    response = client.get("/api/system/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["components"]["backend"]["status"] == "available"
    assert payload["components"]["sensor_storage"]["status"] == "unavailable"
    assert payload["coverage"]["sensors"]["registered_real"] == 0
    assert payload["components"]["shelters"]["status"] == "unavailable"


def test_status_checks_warning_readiness_without_running_evaluation(monkeypatch) -> None:
    def unexpected_warning_evaluation(*args, **kwargs):
        raise AssertionError("status checks must not run warning evaluations")

    monkeypatch.setattr(warning_api, "evaluate_all_warnings", unexpected_warning_evaluation)
    response = client.get("/api/system/status")

    assert response.status_code == 200
    assert response.json()["components"]["warning_engine"]["status"] == "available"
