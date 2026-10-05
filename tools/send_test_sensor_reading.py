"""Send an explicitly labelled software-only sensor test reading to FloodGuard."""

from __future__ import annotations

import argparse
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def _request(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    api_key: str | None = None,
    payload: dict[str, object] | None = None,
) -> tuple[int, dict[str, object]]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-Sensor-Key"] = api_key
    request = Request(
        f"{base_url.rstrip('/')}{path}",
        data=body,
        headers=headers,
        method=method,
    )
    try:
        with urlopen(request, timeout=10) as response:
            data = response.read()
            return response.status, json.loads(data) if data else {}
    except HTTPError as error:
        data = error.read()
        try:
            details = json.loads(data) if data else {"detail": error.reason}
        except json.JSONDecodeError:
            details = {"detail": "The API returned a non-JSON error response."}
        return error.code, details
    except URLError as error:
        raise RuntimeError(f"Could not reach FloodGuard API: {error.reason}") from error


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--percent",
        type=float,
        default=50.0,
        help="Synthetic TEST DATA percentage (default: 50.0; never a hardware observation).",
    )
    args = parser.parse_args()
    if not 0 <= args.percent <= 100:
        parser.error("--percent must be between 0 and 100.")

    api_key = os.environ.get("SENSOR_API_KEY")
    sensor_id = os.environ.get("TEST_SENSOR_ID")
    village_code = os.environ.get("TEST_VILLAGE_LGD_CODE")
    base_url = os.environ.get("FLOODGUARD_API_URL", "http://127.0.0.1:8000")
    if not api_key or not sensor_id or not village_code:
        print(
            "Set SENSOR_API_KEY, TEST_SENSOR_ID, and TEST_VILLAGE_LGD_CODE first.",
            file=sys.stderr,
        )
        return 2

    try:
        code, sensor = _request(
            base_url,
            f"/api/sensors/{sensor_id}",
        )
        if code == 404:
            code, sensor = _request(
                base_url,
                "/api/sensors",
                method="POST",
                api_key=api_key,
                payload={
                    "sensor_id": sensor_id,
                    "sensor_name": f"TEST DATA - {sensor_id}",
                    "village_lgd_code": village_code,
                    "sensor_type": "soil_moisture",
                    "is_test": True,
                },
            )
        if code not in (200, 201):
            raise RuntimeError(f"Test sensor setup failed (HTTP {code}): {sensor}")
        if (
            sensor.get("village_lgd_code") != village_code
            or sensor.get("is_test") is not True
        ):
            raise RuntimeError(
                "The configured test sensor already exists but is not marked as TEST DATA "
                "for the configured village."
            )
        code, acknowledgement = _request(
            base_url,
            "/api/sensors/readings",
            method="POST",
            api_key=api_key,
            payload={
                "sensor_id": sensor_id,
                "village_lgd_code": village_code,
                "soil_moisture_percent": args.percent,
                "is_test": True,
            },
        )
        if code != 202:
            raise RuntimeError(
                f"Test reading was rejected (HTTP {code}): {acknowledgement}"
            )
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        return 1

    print("TEST DATA — synthetic software check, not a physical sensor observation.")
    print(json.dumps(acknowledgement, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
