"""Tests for persistent, authenticated ESP32 sensor ingestion and context."""

from __future__ import annotations

import sqlite3
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


def test_bench_sensor_registers_without_village_and_is_labelled(sensor_client: TestClient) -> None:
    response = sensor_client.post(
        "/api/sensors",
        headers={"X-Sensor-Key": API_KEY},
        json={
            "sensor_id": "FG-BENCH-001",
            "sensor_name": "FloodGuard Chennai Bench Sensor",
            "sensor_type": "soil_moisture",
            "is_test": True,
            "village_lgd_code": None,
            "latitude": None,
            "longitude": None,
        },
    )
    assert response.status_code == 201, response.text
    sensor = response.json()
    assert sensor["sensor_id"] == "FG-BENCH-001"
    assert sensor["village_lgd_code"] is None
    assert sensor["village"] is None
    assert sensor["is_test"] is True
    assert sensor["record_class"] == "TEST / BENCH"

    inventory = sensor_client.get("/api/sensors").json()
    listed = next(item for item in inventory["records"] if item["sensor_id"] == "FG-BENCH-001")
    assert listed["record_class"] == "TEST / BENCH"
    assert listed["latitude"] is None
    assert listed["longitude"] is None


def test_non_test_sensor_requires_village(sensor_client: TestClient) -> None:
    response = sensor_client.post(
        "/api/sensors",
        headers={"X-Sensor-Key": API_KEY},
        json={
            "sensor_id": "FG-NO-VILLAGE",
            "sensor_name": "Production sensor",
            "sensor_type": "soil_moisture",
            "is_test": False,
        },
    )
    assert response.status_code == 422


def test_bench_reading_is_inferred_from_registered_test_sensor_and_isolated(
    sensor_client: TestClient,
) -> None:
    registration = sensor_client.post(
        "/api/sensors",
        headers={"X-Sensor-Key": API_KEY},
        json={
            "sensor_id": "FG-BENCH-001",
            "sensor_name": "FloodGuard Chennai Bench Sensor",
            "sensor_type": "soil_moisture",
            "is_test": True,
        },
    )
    assert registration.status_code == 201, registration.text

    response = sensor_client.post(
        "/api/sensors/readings",
        headers={"X-Sensor-Key": API_KEY},
        json={
            "sensor_id": "FG-BENCH-001",
            "soil_moisture_percent": 0.0,
            "raw_value": 4095,
        },
    )
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "accepted"
    assert response.json()["is_test"] is True
    assert response.json()["sensor_status"] == "online"

    listed = next(
        item
        for item in sensor_client.get("/api/sensors").json()["records"]
        if item["sensor_id"] == "FG-BENCH-001"
    )
    assert listed["record_class"] == "TEST / BENCH"
    assert listed["is_test"] is True
    assert listed["village_lgd_code"] is None
    assert listed["latest"]["soil_moisture_percent"] == 0.0
    assert listed["latest"]["raw_value"] == 4095
    assert listed["latest"]["is_test"] is True
    assert listed["latest"]["received_at"] == response.json()["received_at"]

    village_soil = sensor_client.get(
        f"/api/villages/{VILLAGE_CODE}/soil-moisture"
    ).json()
    assert village_soil["status"] == "unavailable"
    assert village_soil["latest"] is None
    context = sensor_client.get(
        f"/api/villages/{VILLAGE_CODE}/context"
    ).json()
    assert context["soil_moisture"]["value"] is None
    assert context["sensor"]["data"] is None
    warning = sensor_client.get(
        f"/api/villages/{VILLAGE_CODE}/warning"
    )
    assert warning.status_code == 200
    soil_factor = next(
        factor
        for factor in warning.json()["factors"]
        if factor["type"] == "soil_moisture"
    )
    assert soil_factor["freshness"] == "no_data"
    assert soil_factor["is_test"] is False


def test_production_reading_without_village_is_rejected(sensor_client: TestClient) -> None:
    _register(sensor_client)
    response = sensor_client.post(
        "/api/sensors/readings",
        headers={"X-Sensor-Key": API_KEY},
        json={
            "sensor_id": "FG-NIL-TEST-001",
            "soil_moisture_percent": 40.0,
            "raw_value": 2000,
        },
    )
    assert response.status_code == 422


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
    assert acknowledgement["rain_raw"] is None
    assert acknowledgement["rain_detected"] is None
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
    assert records["records"][0]["rain_raw"] is None
    assert records["records"][0]["rain_detected"] is None
    assert sensor_client.get("/api/sensors").json()["records"][0][
        "soil_moisture_percent"
    ] == 68.4


def test_optional_rain_sensor_context_is_persisted_separately(sensor_client: TestClient) -> None:
    _register(sensor_client)
    response = sensor_client.post(
        "/api/sensors/readings",
        headers={"X-Sensor-Key": API_KEY},
        json={
            "sensor_id": "FG-NIL-TEST-001",
            "village_lgd_code": VILLAGE_CODE,
            "soil_moisture_percent": 68.4,
            "raw_value": 1750,
            "rain_raw": 2310,
            "rain_detected": True,
        },
    )
    assert response.status_code == 202, response.text
    assert response.json()["rain_raw"] == 2310
    assert response.json()["rain_detected"] is True

    stored = sensor_client.get("/api/sensors/FG-NIL-TEST-001").json()["latest"]
    assert stored["rain_raw"] == 2310
    assert stored["rain_detected"] is True
    context = sensor_client.get(
        f"/api/villages/{VILLAGE_CODE}/context"
    ).json()
    local_rain = context["current_conditions"]["local_rain_sensor"]
    assert local_rain["status"] == "available"
    assert local_rain["data_type"] == "local_observation"
    assert local_rain["rain_raw"] == 2310
    assert local_rain["rain_detected"] is True
    assert "not an imd rainfall measurement" in local_rain["note"].lower()
    assert all(
        factor["type"] != "rain_sensor"
        for factor in context["warning"]["factors"]
    )


def test_existing_sensor_database_is_migrated_without_losing_readings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    database = tmp_path / "legacy-sensor.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE sensors (
                sensor_id TEXT PRIMARY KEY,
                sensor_name TEXT NOT NULL,
                village_lgd_code TEXT NOT NULL,
                latitude REAL,
                longitude REAL,
                sensor_type TEXT NOT NULL,
                installed_at TEXT,
                registered_at TEXT NOT NULL,
                is_test INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE sensor_readings (
                reading_id INTEGER PRIMARY KEY AUTOINCREMENT,
                sensor_id TEXT NOT NULL,
                village_lgd_code TEXT NOT NULL,
                soil_moisture_percent REAL NOT NULL,
                raw_value INTEGER,
                recorded_at TEXT NOT NULL,
                received_at TEXT NOT NULL,
                is_test INTEGER NOT NULL DEFAULT 0
            );
            INSERT INTO sensors VALUES (
                'FG-LEGACY', 'Legacy sensor', '635099', NULL, NULL,
                'soil_moisture', NULL, '2026-10-05T00:00:00+00:00', 0
            );
            INSERT INTO sensor_readings (
                sensor_id, village_lgd_code, soil_moisture_percent, raw_value,
                recorded_at, received_at, is_test
            ) VALUES (
                'FG-LEGACY', '635099', 42.0, 1700,
                '2026-10-05T00:00:00+00:00', '2026-10-05T00:00:00+00:00', 0
            );
            """
        )
    monkeypatch.setenv("SENSOR_DATABASE_PATH", str(database))
    migrated = sensor_store.get_sensor("FG-LEGACY")
    assert migrated is not None
    assert migrated["latest"]["soil_moisture_percent"] == 42.0
    assert migrated["latest"]["rain_raw"] is None
    assert migrated["latest"]["rain_detected"] is None
    bench = sensor_store.register_sensor(
        sensor_id="FG-MIGRATION-BENCH",
        sensor_name="Migration bench sensor",
        village_lgd_code=None,
        latitude=None,
        longitude=None,
        sensor_type="soil_moisture",
        installed_at=None,
        is_test=True,
    )
    assert bench["village_lgd_code"] is None
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

    with sqlite3.connect(database) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(sensor_readings)")}
    assert {"rain_raw", "rain_detected"} <= columns


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


def test_test_sensor_reading_inherits_test_status_when_flag_omitted(
    sensor_client: TestClient,
) -> None:
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
    assert response.status_code == 202
    assert response.json()["is_test"] is True


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
