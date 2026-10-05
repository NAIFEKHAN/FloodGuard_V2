"""Persistent local storage and freshness calculations for ESP32 sensors."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATABASE_PATH = ROOT / "data" / "sensors.sqlite3"
ONLINE_WINDOW_SECONDS = 10 * 60
STALE_WINDOW_SECONDS = 30 * 60


class DuplicateSensorError(Exception):
    """Raised when a sensor ID is already registered."""


class TestReadingNotAllowed(Exception):
    """Raised when test data is submitted for a non-test sensor."""


class SensorNotFoundError(Exception):
    """Raised when a reading references an unregistered sensor."""


class VillageMismatchError(Exception):
    """Raised when a reading village differs from its sensor registration."""


def database_path() -> Path:
    configured_path = os.environ.get("SENSOR_DATABASE_PATH")
    return Path(configured_path).expanduser() if configured_path else DEFAULT_DATABASE_PATH


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def sensor_status(last_seen_at: str | None, *, now: datetime | None = None) -> tuple[str, int | None]:
    if not last_seen_at:
        return "no_data", None
    age_seconds = max(0, int(((now or utc_now()) - parse_timestamp(last_seen_at)).total_seconds()))
    if age_seconds <= ONLINE_WINDOW_SECONDS:
        return "online", age_seconds
    if age_seconds <= STALE_WINDOW_SECONDS:
        return "stale", age_seconds
    return "offline", age_seconds


@contextmanager
def _connection() -> Iterator[sqlite3.Connection]:
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS sensors (
                sensor_id TEXT PRIMARY KEY,
                sensor_name TEXT NOT NULL,
                village_lgd_code TEXT,
                latitude REAL,
                longitude REAL,
                sensor_type TEXT NOT NULL CHECK (sensor_type = 'soil_moisture'),
                installed_at TEXT,
                registered_at TEXT NOT NULL,
                is_test INTEGER NOT NULL DEFAULT 0 CHECK (is_test IN (0, 1))
            );
            CREATE TABLE IF NOT EXISTS sensor_readings (
                reading_id INTEGER PRIMARY KEY AUTOINCREMENT,
                sensor_id TEXT NOT NULL REFERENCES sensors(sensor_id) ON DELETE CASCADE,
                village_lgd_code TEXT,
                soil_moisture_percent REAL NOT NULL
                    CHECK (soil_moisture_percent >= 0 AND soil_moisture_percent <= 100),
                raw_value INTEGER,
                rain_raw INTEGER,
                rain_detected INTEGER CHECK (rain_detected IS NULL OR rain_detected IN (0, 1)),
                recorded_at TEXT NOT NULL,
                received_at TEXT NOT NULL,
                is_test INTEGER NOT NULL DEFAULT 0 CHECK (is_test IN (0, 1))
            );
            CREATE INDEX IF NOT EXISTS sensor_readings_latest
                ON sensor_readings(sensor_id, received_at DESC, reading_id DESC);
            CREATE INDEX IF NOT EXISTS sensor_readings_village_latest
                ON sensor_readings(village_lgd_code, received_at DESC, reading_id DESC);
            CREATE TABLE IF NOT EXISTS warning_history (
                warning_id INTEGER PRIMARY KEY AUTOINCREMENT,
                village_lgd_code TEXT NOT NULL,
                stage TEXT NOT NULL CHECK (stage IN ('green', 'yellow', 'orange', 'red')),
                reason_summary TEXT NOT NULL,
                generated_at TEXT NOT NULL,
                baseline_score REAL,
                scenario_score REAL,
                sensor_status TEXT,
                soil_moisture REAL,
                is_test INTEGER NOT NULL DEFAULT 0 CHECK (is_test IN (0, 1)),
                rule_version TEXT NOT NULL,
                rainfall_mode TEXT NOT NULL,
                rainfall_scenario_multiplier REAL,
                rule_id TEXT NOT NULL,
                soil_moisture_band TEXT,
                factors_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS warning_history_latest
                ON warning_history(village_lgd_code, generated_at DESC, warning_id DESC);
            """
        )
        reading_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(sensor_readings)")
        }
        if "rain_raw" not in reading_columns:
            connection.execute("ALTER TABLE sensor_readings ADD COLUMN rain_raw INTEGER")
        if "rain_detected" not in reading_columns:
            connection.execute(
                "ALTER TABLE sensor_readings ADD COLUMN rain_detected INTEGER"
            )
        sensor_columns = {
            row["name"]: row
            for row in connection.execute("PRAGMA table_info(sensors)")
        }
        reading_columns = {
            row["name"]: row
            for row in connection.execute("PRAGMA table_info(sensor_readings)")
        }
        if sensor_columns["village_lgd_code"]["notnull"] or reading_columns[
            "village_lgd_code"
        ]["notnull"]:
            connection.execute("PRAGMA foreign_keys = OFF")
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    CREATE TABLE sensors_v2 (
                        sensor_id TEXT PRIMARY KEY,
                        sensor_name TEXT NOT NULL,
                        village_lgd_code TEXT,
                        latitude REAL,
                        longitude REAL,
                        sensor_type TEXT NOT NULL CHECK (sensor_type = 'soil_moisture'),
                        installed_at TEXT,
                        registered_at TEXT NOT NULL,
                        is_test INTEGER NOT NULL DEFAULT 0 CHECK (is_test IN (0, 1))
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE sensor_readings_v2 (
                        reading_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        sensor_id TEXT NOT NULL REFERENCES sensors_v2(sensor_id) ON DELETE CASCADE,
                        village_lgd_code TEXT,
                        soil_moisture_percent REAL NOT NULL
                            CHECK (soil_moisture_percent >= 0 AND soil_moisture_percent <= 100),
                        raw_value INTEGER,
                        rain_raw INTEGER,
                        rain_detected INTEGER CHECK (rain_detected IS NULL OR rain_detected IN (0, 1)),
                        recorded_at TEXT NOT NULL,
                        received_at TEXT NOT NULL,
                        is_test INTEGER NOT NULL DEFAULT 0 CHECK (is_test IN (0, 1))
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO sensors_v2 (
                        sensor_id, sensor_name, village_lgd_code, latitude,
                        longitude, sensor_type, installed_at, registered_at, is_test
                    )
                    SELECT sensor_id, sensor_name, village_lgd_code, latitude,
                           longitude, sensor_type, installed_at, registered_at, is_test
                    FROM sensors
                    """
                )
                connection.execute(
                    """
                    INSERT INTO sensor_readings_v2 (
                        reading_id, sensor_id, village_lgd_code,
                        soil_moisture_percent, raw_value, rain_raw, rain_detected,
                        recorded_at, received_at, is_test
                    )
                    SELECT reading_id, sensor_id, village_lgd_code,
                           soil_moisture_percent, raw_value, rain_raw, rain_detected,
                           recorded_at, received_at, is_test
                    FROM sensor_readings
                    """
                )
                connection.execute("DROP TABLE sensor_readings")
                connection.execute("DROP TABLE sensors")
                connection.execute("ALTER TABLE sensors_v2 RENAME TO sensors")
                connection.execute(
                    "ALTER TABLE sensor_readings_v2 RENAME TO sensor_readings"
                )
                connection.execute(
                    """
                    CREATE INDEX sensor_readings_latest
                    ON sensor_readings(sensor_id, received_at DESC, reading_id DESC)
                    """
                )
                connection.execute(
                    """
                    CREATE INDEX sensor_readings_village_latest
                    ON sensor_readings(village_lgd_code, received_at DESC, reading_id DESC)
                    """
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.execute("PRAGMA foreign_keys = ON")
        yield connection
    finally:
        connection.close()


def _reading_dict(row: sqlite3.Row | None) -> dict[str, object] | None:
    if row is None:
        return None
    return {
        "reading_id": row["reading_id"],
        "sensor_id": row["sensor_id"],
        "village_lgd_code": row["village_lgd_code"],
        "soil_moisture_percent": row["soil_moisture_percent"],
        "raw_value": row["raw_value"],
        "rain_raw": row["rain_raw"],
        "rain_detected": (
            bool(row["rain_detected"]) if row["rain_detected"] is not None else None
        ),
        "recorded_at": row["recorded_at"],
        "received_at": row["received_at"],
        "is_test": bool(row["is_test"]),
    }


def _sensor_dict(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    reading_row = connection.execute(
        """
        SELECT * FROM sensor_readings
        WHERE sensor_id = ?
        ORDER BY received_at DESC, reading_id DESC
        LIMIT 1
        """,
        (row["sensor_id"],),
    ).fetchone()
    reading = _reading_dict(reading_row)
    last_seen_at = reading["received_at"] if reading else None
    status, age_seconds = sensor_status(last_seen_at, now=now)
    return {
        "sensor_id": row["sensor_id"],
        "sensor_name": row["sensor_name"],
        "village_lgd_code": row["village_lgd_code"],
        "latitude": row["latitude"],
        "longitude": row["longitude"],
        "sensor_type": row["sensor_type"],
        "installed_at": row["installed_at"],
        "registered_at": row["registered_at"],
        "is_test": bool(row["is_test"]),
        "status": status,
        "last_seen_at": last_seen_at,
        "age_seconds": age_seconds,
        "latest": reading,
    }


def register_sensor(
    *,
    sensor_id: str,
    sensor_name: str,
    village_lgd_code: str | None,
    latitude: float | None,
    longitude: float | None,
    sensor_type: str,
    installed_at: str | None,
    is_test: bool,
) -> dict[str, object]:
    if village_lgd_code is None and not is_test:
        raise ValueError("A village LGD code is required for non-test sensors.")
    try:
        with _connection() as connection:
            with connection:
                connection.execute(
                    """
                    INSERT INTO sensors (
                        sensor_id, sensor_name, village_lgd_code, latitude, longitude,
                        sensor_type, installed_at, registered_at, is_test
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        sensor_id,
                        sensor_name,
                        village_lgd_code,
                        latitude,
                        longitude,
                        sensor_type,
                        installed_at,
                        iso_utc(utc_now()),
                        int(is_test),
                    ),
                )
    except sqlite3.IntegrityError as error:
        if "sensors.sensor_id" in str(error):
            raise DuplicateSensorError(sensor_id) from error
        raise
    sensor = get_sensor(sensor_id)
    if sensor is None:
        raise RuntimeError(f"Registered sensor {sensor_id!r} could not be read back.")
    return sensor


def get_sensor(sensor_id: str) -> dict[str, object] | None:
    with _connection() as connection:
        row = connection.execute(
            "SELECT * FROM sensors WHERE sensor_id = ?", (sensor_id,)
        ).fetchone()
        return _sensor_dict(connection, row) if row else None


def list_sensors() -> list[dict[str, object]]:
    with _connection() as connection:
        rows = connection.execute(
            "SELECT * FROM sensors ORDER BY sensor_id"
        ).fetchall()
        now = utc_now()
        return [_sensor_dict(connection, row, now=now) for row in rows]


def insert_reading(
    *,
    sensor_id: str,
    village_lgd_code: str | None,
    soil_moisture_percent: float,
    raw_value: int | None,
    rain_raw: int | None = None,
    rain_detected: bool | None = None,
    recorded_at: str,
    received_at: str,
    is_test: bool | None,
) -> dict[str, object]:
    with _connection() as connection:
        with connection:
            sensor = connection.execute(
                "SELECT * FROM sensors WHERE sensor_id = ?", (sensor_id,)
            ).fetchone()
            if sensor is None:
                raise SensorNotFoundError(sensor_id)
            registered_is_test = bool(sensor["is_test"])
            if is_test is not None and registered_is_test != is_test:
                raise TestReadingNotAllowed(sensor_id)
            if sensor["village_lgd_code"] != village_lgd_code:
                raise VillageMismatchError(sensor_id)
            connection.execute(
                """
                INSERT INTO sensor_readings (
                    sensor_id, village_lgd_code, soil_moisture_percent, raw_value,
                    rain_raw, rain_detected, recorded_at, received_at, is_test
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sensor_id,
                    village_lgd_code,
                    soil_moisture_percent,
                    raw_value,
                    rain_raw,
                    int(rain_detected) if rain_detected is not None else None,
                    recorded_at,
                    received_at,
                    int(registered_is_test),
                ),
            )
    updated_sensor = get_sensor(sensor_id)
    if updated_sensor is None:
        raise RuntimeError(f"Sensor {sensor_id!r} disappeared after accepting a reading.")
    return updated_sensor


def readings_for_sensor(sensor_id: str, limit: int) -> list[dict[str, object]]:
    with _connection() as connection:
        rows = connection.execute(
            """
            SELECT * FROM sensor_readings
            WHERE sensor_id = ?
            ORDER BY received_at DESC, reading_id DESC
            LIMIT ?
            """,
            (sensor_id, limit),
        ).fetchall()
        return [_reading_dict(row) for row in rows if row is not None]


def sensors_for_village(village_lgd_code: str) -> list[dict[str, object]]:
    return [
        sensor
        for sensor in list_sensors()
        if sensor["village_lgd_code"] == village_lgd_code
    ]


def latest_village_reading(village_lgd_code: str) -> dict[str, object] | None:
    with _connection() as connection:
        row = connection.execute(
            """
            SELECT r.*, s.sensor_name
            FROM sensor_readings AS r
            JOIN sensors AS s ON s.sensor_id = r.sensor_id
            WHERE r.village_lgd_code = ?
            ORDER BY r.received_at DESC, r.reading_id DESC
            LIMIT 1
            """,
            (village_lgd_code,),
        ).fetchone()
        if row is None:
            return None
        result = _reading_dict(row)
        status, age_seconds = sensor_status(str(row["received_at"]))
        result.update(
            {
                "sensor_name": row["sensor_name"],
                "sensor_status": status,
                "age_seconds": age_seconds,
                "freshness": status,
            }
        )
        return result


def has_registered_sensors(*, include_test: bool = False) -> bool:
    with _connection() as connection:
        query = "SELECT 1 FROM sensors"
        if not include_test:
            query += " WHERE is_test = 0"
        return connection.execute(query + " LIMIT 1").fetchone() is not None


def delete_test_data() -> dict[str, int]:
    with _connection() as connection:
        with connection:
            readings = connection.execute(
                "DELETE FROM sensor_readings WHERE is_test = 1"
            ).rowcount
            sensors = connection.execute(
                "DELETE FROM sensors WHERE is_test = 1"
            ).rowcount
        return {"readings_deleted": readings, "sensors_deleted": sensors}


def test_data_counts() -> dict[str, int]:
    with _connection() as connection:
        readings = connection.execute(
            "SELECT COUNT(*) FROM sensor_readings WHERE is_test = 1"
        ).fetchone()[0]
        sensors = connection.execute(
            "SELECT COUNT(*) FROM sensors WHERE is_test = 1"
        ).fetchone()[0]
        return {"readings": int(readings), "sensors": int(sensors)}


def record_warning_if_changed(warning: dict[str, object]) -> bool:
    soil_factor = next(
        (
            factor
            for factor in warning["factors"]
            if factor["type"] == "soil_moisture"
        ),
        {},
    )
    signature = {
        "stage": warning["stage"],
        "rule_version": warning["rule_version"],
        "rainfall_mode": warning["rainfall_mode"],
        "rainfall_scenario_multiplier": warning["rainfall_scenario_multiplier"],
        "rule_id": warning["rule_id"],
        "sensor_status": soil_factor.get("freshness"),
        "soil_moisture_band": soil_factor.get("status")
        if soil_factor.get("used_as_current_evidence")
        else None,
        "is_test": warning["is_test"],
    }
    with _connection() as connection:
        with connection:
            previous = connection.execute(
                """
                SELECT stage, rule_version, rainfall_mode,
                       rainfall_scenario_multiplier, rule_id, sensor_status,
                       soil_moisture_band, is_test
                FROM warning_history
                WHERE village_lgd_code = ?
                ORDER BY generated_at DESC, warning_id DESC
                LIMIT 1
                """,
                (warning["village_lgd_code"],),
            ).fetchone()
            if previous is not None and all(
                previous[key] == value for key, value in signature.items()
            ):
                return False
            connection.execute(
                """
                INSERT INTO warning_history (
                    village_lgd_code, stage, reason_summary, generated_at,
                    baseline_score, scenario_score, sensor_status, soil_moisture,
                    is_test, rule_version, rainfall_mode,
                    rainfall_scenario_multiplier, rule_id, soil_moisture_band,
                    factors_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    warning["village_lgd_code"],
                    warning["stage"],
                    warning["reason"],
                    warning["generated_at"],
                    warning["baseline_score"],
                    warning["scenario_score"],
                    signature["sensor_status"],
                    soil_factor.get("value"),
                    int(bool(warning["is_test"])),
                    warning["rule_version"],
                    warning["rainfall_mode"],
                    warning["rainfall_scenario_multiplier"],
                    warning["rule_id"],
                    signature["soil_moisture_band"],
                    json.dumps(warning["factors"], separators=(",", ":")),
                ),
            )
            return True


def get_warning_history(
    *,
    village_lgd_code: str | None = None,
    limit: int = 100,
) -> list[dict[str, object]]:
    with _connection() as connection:
        if village_lgd_code is None:
            rows = connection.execute(
                """
                SELECT * FROM warning_history
                ORDER BY generated_at DESC, warning_id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT * FROM warning_history
                WHERE village_lgd_code = ?
                ORDER BY generated_at DESC, warning_id DESC
                LIMIT ?
                """,
                (village_lgd_code, limit),
            ).fetchall()
        return [
            {
                "warning_id": row["warning_id"],
                "village_lgd_code": row["village_lgd_code"],
                "stage": row["stage"],
                "reason_summary": row["reason_summary"],
                "generated_at": row["generated_at"],
                "baseline_score": row["baseline_score"],
                "scenario_score": row["scenario_score"],
                "sensor_status": row["sensor_status"],
                "soil_moisture": row["soil_moisture"],
                "is_test": bool(row["is_test"]),
                "rule_version": row["rule_version"],
                "rainfall_mode": row["rainfall_mode"],
                "rainfall_scenario_multiplier": row[
                    "rainfall_scenario_multiplier"
                ],
                "rule_id": row["rule_id"],
                "soil_moisture_band": row["soil_moisture_band"],
                "factors": json.loads(row["factors_json"]),
            }
            for row in rows
        ]
