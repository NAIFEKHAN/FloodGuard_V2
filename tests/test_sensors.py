"""Tests for persistent, authenticated ESP32 sensor ingestion and context."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import sensor_api, sensor_store
from backend.app.data_catalog import DATA_SOURCE_BY_ID, list_data_sources
from backend.app.main import app


API_KEY = "local-development-sensor-key"
VILLAGE_CODE = "635099"


@pytest.fixture
def sensor_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("SENSOR_DATABASE_PATH", str(tmp_path / "sensor-test.sqlite3"))
    monkeypatch.setenv("SENSOR_API_KEY", API_KEY)
    sensor_api._village_codes.cache_clear()
    with TestClient(app) as client:
        yield client
    sensor_api._village_codes.cache_clear()


def _register(
    client: TestClient,
    *,
    sensor_id: str = "FG-NIL-TEST-001",
    village_code: str = VILLAGE_CODE,
    is_test: bool = False,
    latitude: float | None = None,
    longitude: float | None = None,
) -> dict[str, object]:
    response = client.post(
        "/api/sensors",
        headers={"X-Sensor-Key": API_KEY},
        json={
            "sensor_id": sensor_id,
            "sensor_name": "Test soil sensor",
            "village_lgd_code": village_code,
            "latitude": latitude,
            "longitude": longitude,
            "sensor_type": "soil_moisture",
            "is_test": is_test,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_sensor_registration_validates_lgd_and_duplicate_id(sensor_client: TestClient) -> None:
    registered = _register(sensor_client, latitude=11.2, longitude=76.7)
    assert registered["village_lgd_code"] == VILLAGE_CODE
    assert registered["village"]["village_name_en"]
    assert registered["latitude"] == 11.2
    assert registered["status"] == "no_data"
    assert registered["last_seen_at"] is None

    duplicate = sensor_client.post(
        "/api/sensors",
        headers={"X-Sensor-Key": API_KEY},
        json={
            "sensor_id": "FG-NIL-TEST-001",
            "sensor_name": "Duplicate",
            "village_lgd_code": VILLAGE_CODE,
        },
    )
    assert duplicate.status_code == 409

    unknown_village = sensor_client.post(
        "/api/sensors",
        headers={"X-Sensor-Key": API_KEY},
        json={
            "sensor_id": "FG-UNKNOWN",
            "sensor_name": "Unknown village",
            "village_lgd_code": "999999",
        },
    )
    assert unknown_village.status_code == 404


def test_sensor_registration_requires_key_and_configuration(
    sensor_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = {
        "sensor_id": "FG-NIL-TEST-001",
        "sensor_name": "Test sensor",
        "village_lgd_code": VILLAGE_CODE,
    }
    assert sensor_client.post("/api/sensors", json=payload).status_code == 401
    assert (
        sensor_client.post(
            "/api/sensors",
            headers={"X-Sensor-Key": "incorrect"},
            json=payload,
        ).status_code
        == 401
    )

    monkeypatch.delenv("SENSOR_API_KEY")
    assert (
        sensor_client.post(
            "/api/sensors",
            headers={"X-Sensor-Key": API_KEY},
            json=payload,
        ).status_code
        == 503
    )


@pytest.mark.parametrize("value", [-0.1, 100.1])
def test_reading_rejects_percent_outside_range(
    sensor_client: TestClient, value: float
) -> None:
    _register(sensor_client)
    response = sensor_client.post(
        "/api/sensors/readings",
        headers={"X-Sensor-Key": API_KEY},
        json={
            "sensor_id": "FG-NIL-TEST-001",
            "village_lgd_code": VILLAGE_CODE,
            "soil_moisture_percent": value,
        },
    )
    assert response.status_code == 422


def test_authenticated_reading_is_persisted_and_exposed_in_village_context(
    sensor_client: TestClient,
) -> None:
    _register(sensor_client)
    response = sensor_client.post(
        "/api/sensors/readings",
        headers={"X-Sensor-Key": API_KEY},
        json={
            "sensor_id": "FG-NIL-TEST-001",
            "village_lgd_code": VILLAGE_CODE,
            "soil_moisture_percent": 68.4,
            "raw_value": 1750,
            "recorded_at": "2026-10-05T10:30:00+05:30",
        },
    )
    assert response.status_code == 202
    acknowledgement = response.json()
    assert acknowledgement["status"] == "accepted"
    assert acknowledgement["sensor_status"] == "online"
    assert acknowledgement["soil_moisture_percent"] == 68.4
    assert acknowledgement["recorded_at"] == "2026-10-05T05:00:00+00:00"
    assert acknowledgement["received_at"]

    latest = sensor_client.get(
        f"/api/villages/{VILLAGE_CODE}/soil-moisture"
    ).json()
    assert latest["status"] == "available"
    assert latest["data_type"] == "observed"
    assert latest["latest"]["raw_value"] == 1750
    assert latest["latest"]["freshness"] == "online"
    assert latest["provenance"]["sensor_id"] == "FG-NIL-TEST-001"
    assert latest["provenance"]["received_at"] == acknowledgement["received_at"]

    context = sensor_client.get(
        f"/api/villages/{VILLAGE_CODE}/context"
    ).json()
    assert context["soil_moisture"]["value"] == 68.4
    assert context["soil_moisture"]["freshness"] == "online"
    assert context["current_conditions"]["soil_moisture"]["value"] == 68.4
    assert context["current_conditions"]["status"] == "not_in_model"
    assert context["sensor"]["data"][0]["sensor_id"] == "FG-NIL-TEST-001"

    sensor = sensor_client.get("/api/sensors/FG-NIL-TEST-001").json()
    assert sensor["latest"]["raw_value"] == 1750
    assert sensor["last_seen_at"] == acknowledgement["received_at"]
    records = sensor_client.get(
        "/api/sensors/FG-NIL-TEST-001/readings?limit=1"
    ).json()
    assert records["record_count"] == 1
    assert records["records"][0]["soil_moisture_percent"] == 68.4
    assert sensor_client.get("/api/sensors").json()["records"][0][
        "soil_moisture_percent"
    ] == 68.4


def test_reading_auth_unknown_sensor_village_mismatch_and_body_limit(
    sensor_client: TestClient,
) -> None:
    body = {
        "sensor_id": "FG-NIL-TEST-001",
        "village_lgd_code": VILLAGE_CODE,
        "soil_moisture_percent": 42.0,
    }
    assert sensor_client.post("/api/sensors/readings", json=body).status_code == 401
    assert (
        sensor_client.post(
            "/api/sensors/readings",
            headers={"X-Sensor-Key": "bad"},
            json=body,
        ).status_code
        == 401
    )
    _register(sensor_client)
    unknown = sensor_client.post(
        "/api/sensors/readings",
        headers={"X-Sensor-Key": API_KEY},
        json={**body, "sensor_id": "not-registered"},
    )
    assert unknown.status_code == 404
    mismatch = sensor_client.post(
        "/api/sensors/readings",
        headers={"X-Sensor-Key": API_KEY},
        json={**body, "village_lgd_code": "932021"},
    )
    assert mismatch.status_code == 409
    oversized = sensor_client.post(
        "/api/sensors/readings",
        headers={
            "X-Sensor-Key": API_KEY,
            "Content-Length": "4097",
        },
        json=body,
    )
    assert oversized.status_code == 413


def test_status_windows_no_data_persistence_and_real_source_availability(
    sensor_client: TestClient,
) -> None:
    sensor = _register(sensor_client)
    assert sensor["status"] == "no_data"
    assert DATA_SOURCE_BY_ID["soil_moisture"].data_type.value == "observed"
    assert next(
        source for source in list_data_sources() if source["id"] == "soil_moisture"
    )["status"] == "available"

    now = datetime.now(UTC)
    for sensor_id, age, expected in (
        ("FG-ONLINE", 600, "online"),
        ("FG-STALE", 601, "stale"),
        ("FG-STALE-BOUNDARY", 1800, "stale"),
        ("FG-OFFLINE", 1801, "offline"),
    ):
        sensor_store.register_sensor(
            sensor_id=sensor_id,
            sensor_name=sensor_id,
            village_lgd_code=VILLAGE_CODE,
            latitude=None,
            longitude=None,
            sensor_type="soil_moisture",
            installed_at=None,
            is_test=False,
        )
        seen_at = (now - timedelta(seconds=age)).isoformat()
        sensor_store.insert_reading(
            sensor_id=sensor_id,
            village_lgd_code=VILLAGE_CODE,
            soil_moisture_percent=51.0,
            raw_value=None,
            recorded_at=seen_at,
            received_at=seen_at,
            is_test=False,
        )
        current = sensor_client.get(f"/api/sensors/{sensor_id}").json()
        assert current["status"] == expected
        assert current["age_seconds"] >= age - 1


def test_no_sensor_is_unavailable_and_test_data_is_labelled_and_cleanable(
    sensor_client: TestClient,
) -> None:
    empty = sensor_client.get(
        f"/api/villages/{VILLAGE_CODE}/soil-moisture"
    ).json()
    assert empty["status"] == "unavailable"
    assert empty["latest"] is None
    assert empty["data"] is None

    _register(sensor_client, sensor_id="FG-TEST-DATA", is_test=True)
    accepted = sensor_client.post(
        "/api/sensors/readings",
        headers={"X-Sensor-Key": API_KEY},
        json={
            "sensor_id": "FG-TEST-DATA",
            "village_lgd_code": VILLAGE_CODE,
            "soil_moisture_percent": 50.0,
            "is_test": True,
        },
    )
    assert accepted.status_code == 202
    assert accepted.json()["is_test"] is True
    latest = sensor_client.get(
        f"/api/villages/{VILLAGE_CODE}/soil-moisture"
    ).json()
    assert latest["data_type"] == "test"
    assert latest["provenance"]["is_test"] is True
    context = sensor_client.get(f"/api/villages/{VILLAGE_CODE}/context").json()
    assert context["soil_moisture"]["is_test"] is True
    assert context["soil_moisture"]["data_type"] == "test"
    assert context["current_conditions"]["soil_moisture"]["is_test"] is True

    assert sensor_store.test_data_counts() == {"readings": 1, "sensors": 1}
    assert sensor_store.delete_test_data() == {
        "readings_deleted": 1,
        "sensors_deleted": 1,
    }
    assert sensor_store.test_data_counts() == {"readings": 0, "sensors": 0}


def test_test_sensor_requires_test_readings(sensor_client: TestClient) -> None:
    _register(sensor_client, sensor_id="FG-TEST-DATA", is_test=True)
    response = sensor_client.post(
        "/api/sensors/readings",
        headers={"X-Sensor-Key": API_KEY},
        json={
            "sensor_id": "FG-TEST-DATA",
            "village_lgd_code": VILLAGE_CODE,
            "soil_moisture_percent": 20.0,
        },
    )
    assert response.status_code == 409


def test_village_soil_moisture_endpoint_rejects_unknown_lgd(sensor_client: TestClient) -> None:
    assert sensor_client.get("/api/villages/999999/soil-moisture").status_code == 404


def test_existing_health_and_baseline_model_responses_remain_available(
    sensor_client: TestClient,
) -> None:
    assert sensor_client.get("/health").json()["status"] == "ok"
    model = sensor_client.get("/api/ml-susceptibility")
    assert model.status_code == 200
    payload = model.json()
    assert payload["record_count"] == 40
    assert payload["future_sensor_features"]["soil_moisture"]["included_in_training"] is False
