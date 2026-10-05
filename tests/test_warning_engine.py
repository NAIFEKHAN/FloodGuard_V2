"""Tests for transparent rule-based warning stages and history."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.warning_engine import evaluate_warning


def _evaluate(
    *,
    score: float = 30,
    multiplier: float | None = 1.0,
    mode: str = "scenario",
    soil_value: float | None = None,
    freshness: str = "no_data",
    is_test: bool = False,
) -> dict[str, object]:
    return evaluate_warning(
        village_lgd_code="635099",
        village_name="Kodanad",
        baseline_score=score,
        scenario_score=score,
        rainfall_multiplier=multiplier,
        rainfall_mode=mode,
        soil_moisture={
            "value": soil_value,
            "freshness": freshness,
            "is_test": is_test,
        },
        hydrology={"status": "unavailable"},
        historical_evidence_count=0,
        model_version="test-model",
    )


@pytest.mark.parametrize(
    ("inputs", "expected"),
    [
        ({}, "green"),
        ({"score": 60}, "yellow"),
        ({"score": 60, "multiplier": 1.5}, "orange"),
        ({"score": 80, "multiplier": 2.0}, "red"),
        (
            {
                "score": 80,
                "multiplier": 1.5,
                "soil_value": 80,
                "freshness": "online",
            },
            "red",
        ),
    ],
)
def test_warning_rules_emit_each_configured_stage(
    inputs: dict[str, object], expected: str
) -> None:
    assert _evaluate(**inputs)["stage"] == expected


@pytest.mark.parametrize("freshness", ["stale", "offline"])
def test_stale_or_offline_soil_is_not_current_stage_evidence(
    freshness: str,
) -> None:
    warning = _evaluate(
        score=70,
        soil_value=95,
        freshness=freshness,
    )
    soil_factor = next(
        factor for factor in warning["factors"] if factor["type"] == "soil_moisture"
    )
    assert warning["stage"] == "yellow"
    assert soil_factor["used_as_current_evidence"] is False


def test_current_mode_marks_rainfall_unavailable_and_labels_test_reading() -> None:
    warning = _evaluate(
        score=70,
        multiplier=None,
        mode="current",
        soil_value=75,
        freshness="online",
        is_test=True,
    )
    rainfall_factor = next(
        factor for factor in warning["factors"] if factor["type"] == "rainfall"
    )
    assert warning["stage"] == "orange"
    assert warning["rainfall_mode"] == "current_conditions"
    assert warning["scenario_is_simulated"] is False
    assert warning["is_test"] is True
    assert "validated current rainfall" in warning["missing_inputs"]
    assert rainfall_factor["value"] is None


@pytest.fixture
def warning_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("SENSOR_DATABASE_PATH", str(tmp_path / "warnings.sqlite3"))
    with TestClient(app) as client:
        yield client


def test_warning_api_reports_current_mode_and_unsupported_village_coverage(
    warning_client: TestClient,
) -> None:
    supported = warning_client.get(
        "/api/villages/635099/warning?mode=current"
    )
    assert supported.status_code == 200
    assert supported.json()["rainfall_mode"] == "current_conditions"
    assert supported.json()["rainfall_scenario_multiplier"] is None
    assert supported.json()["scenario_is_simulated"] is False

    unsupported = warning_client.get(
        "/api/villages/932021/warning?mode=current"
    )
    assert unsupported.status_code == 200
    assert unsupported.json()["status"] == "unavailable"
    assert "model/data coverage" in unsupported.json()["reason"]


def test_scenario_warning_api_labels_simulated_rainfall(
    warning_client: TestClient,
) -> None:
    response = warning_client.get("/api/warnings?mode=scenario&scenario=baseline")
    assert response.status_code == 200
    payload = response.json()
    assert payload["scenario_is_simulated"] is True
    assert payload["mode_label"] == "SCENARIO"
    assert payload["coverage"]["supported_village_count"] == 40
    assert sum(payload["stage_counts"].values()) == 40
    assert payload["records"][0]["scenario_is_simulated"] is True
    rainfall = next(
        factor
        for factor in payload["records"][0]["factors"]
        if factor["type"] == "rainfall"
    )
    assert rainfall["data_type"] == "simulated"


def test_warning_history_records_only_material_changes(
    warning_client: TestClient,
) -> None:
    first = warning_client.get("/api/villages/635099/warning?mode=current")
    second = warning_client.get("/api/villages/635099/warning?mode=current")
    assert first.status_code == second.status_code == 200

    history = warning_client.get(
        "/api/warnings/history?village_lgd_code=635099"
    )
    assert history.status_code == 200
    assert history.json()["record_count"] == 1
    record = history.json()["records"][0]
    assert record["rainfall_mode"] == "current_conditions"
    assert record["rule_version"] == first.json()["rule_version"]


def test_warning_mode_validation(warning_client: TestClient) -> None:
    response = warning_client.get(
        "/api/villages/635099/warning?mode=forecast"
    )
    assert response.status_code == 422
