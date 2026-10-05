"""Tests for Open-Meteo hourly forecast extraction and cache fallback."""

from __future__ import annotations

import json
from email.message import Message
from datetime import UTC, datetime, timedelta
from urllib.error import HTTPError

import pytest

from backend.app.services import open_meteo


class _Response:
    status = 200

    def __init__(self, body: str) -> None:
        self.body = body

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body.encode("utf-8")


def _hourly_payload(*, current: str = "2026-10-05T13:00") -> dict[str, object]:
    start = datetime.fromisoformat(current) + timedelta(hours=1)
    times = [(start + timedelta(hours=index)).isoformat(timespec="minutes") for index in range(48)]
    return {
        "timezone": "Asia/Kolkata",
        "current": {"time": current, "precipitation": 0.2},
        "hourly": {
            "time": times,
            "rain": [0.5] * 48,
            "precipitation": [1.0] * 48,
            "precipitation_probability": [70] * 48,
        },
    }


def _cache_village(code: int) -> dict[str, object]:
    time = "2026-10-05T14:00+05:30"
    records = [
        {
            "forecast_time": (
                datetime.fromisoformat(time) + timedelta(hours=index)
            ).isoformat(),
            "rain_mm": 0.5,
            "precipitation_mm": 1.0,
            "precipitation_probability_percent": 70,
        }
        for index in range(24)
    ]
    return {
        "village_lgd_code": str(635000 + code),
        "village_name_en": f"Village {code}",
        "latitude": 11.5,
        "longitude": 76.75,
        "forecast": records,
        "next_24h_rainfall_mm": 24.0,
        "forecast_period_start": records[0]["forecast_time"],
        "forecast_period_end": records[-1]["forecast_time"],
        "current_precipitation_mm": 0.2,
        "updated_at": datetime.now(UTC).isoformat(),
        "source": open_meteo.SOURCE,
        "status": "live",
        "forecast_status": "LIVE",
    }


def _cache_payload() -> dict[str, object]:
    return {
        "source": open_meteo.SOURCE,
        "status": "live",
        "available": True,
        "forecast_status": "LIVE",
        "retrieved_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
        "villages": [_cache_village(code) for code in range(40)],
    }


def test_sums_exact_next_24_hour_precipitation_and_preserves_weather_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _hourly_payload()
    monkeypatch.setattr(
        open_meteo.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(json.dumps(payload)),
    )

    result = open_meteo._fetch_coordinate_forecast(11.5, 76.75)

    assert result["next_24h_rainfall_mm"] == 24.0
    assert len(result["forecast"]) == 24
    assert result["forecast"][0]["forecast_time"] == "2026-10-05T14:00:00+05:30"
    assert result["forecast"][0]["rain_mm"] == 0.5
    assert result["forecast"][0]["precipitation_mm"] == 1.0
    assert result["forecast"][0]["precipitation_probability_percent"] == 70.0
    assert result["current_precipitation_mm"] == 0.2


def test_uses_rain_when_hourly_precipitation_is_null(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _hourly_payload()
    payload["hourly"]["precipitation"][0] = None
    monkeypatch.setattr(
        open_meteo.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(json.dumps(payload)),
    )

    result = open_meteo._fetch_coordinate_forecast(11.5, 76.75)

    assert result["forecast"][0]["precipitation_mm"] == 0.5


def test_retries_open_meteo_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _hourly_payload()
    calls = 0

    def urlopen(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise HTTPError(
                open_meteo.FORECAST_URL,
                429,
                "Too Many Requests",
                Message(),
                None,
            )
        return _Response(json.dumps(payload))

    monkeypatch.setattr(open_meteo.request, "urlopen", urlopen)
    monkeypatch.setattr(open_meteo.time, "sleep", lambda _delay: None)

    result = open_meteo._fetch_coordinate_forecast(11.5, 76.75)

    assert calls == 2
    assert len(result["forecast"]) == 24
    assert result["next_24h_rainfall_mm"] == 24.0


def test_rejects_missing_hourly_rainfall_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _hourly_payload()
    del payload["hourly"]["rain"]
    monkeypatch.setattr(
        open_meteo.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(json.dumps(payload)),
    )

    with pytest.raises(ValueError, match="missing hourly"):
        open_meteo._fetch_coordinate_forecast(11.5, 76.75)


def test_returns_fresh_cache_without_upstream_calls(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    cache_path = tmp_path / "open_meteo_latest.json"
    cache_path.write_text(json.dumps(_cache_payload()), encoding="utf-8")
    monkeypatch.setenv("WEATHER_CACHE_PATH", str(cache_path))
    monkeypatch.setattr(
        open_meteo,
        "_fetch_live_forecasts",
        lambda: pytest.fail("Fresh cache should not trigger an upstream request."),
    )

    result = open_meteo.fetch_forecast()

    assert result["status"] == "cached"
    assert result["forecast_status"] == "CACHED"
    assert result["villages"][0]["status"] == "cached"


def test_uses_cached_forecast_when_upstream_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    cache_path = tmp_path / "open_meteo_latest.json"
    payload = _cache_payload()
    payload["retrieved_at"] = "2020-01-01T00:00:00+00:00"
    cache_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("WEATHER_CACHE_PATH", str(cache_path))
    monkeypatch.setattr(
        open_meteo,
        "_fetch_live_forecasts",
        lambda: (_ for _ in ()).throw(OSError("network unavailable")),
    )

    result = open_meteo.fetch_forecast()

    assert result["status"] == "cached"
    assert result["forecast_status"] == "CACHED"
    assert result["cache_status"] == "stale_fallback"
    assert "Open-Meteo unavailable" in result["warning"]


def test_falls_back_to_manual_scenario_when_live_and_cache_fail(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("WEATHER_CACHE_PATH", str(tmp_path / "missing.json"))
    monkeypatch.setattr(
        open_meteo,
        "_fetch_live_forecasts",
        lambda: (_ for _ in ()).throw(TimeoutError("request timed out")),
    )

    result = open_meteo.fetch_forecast()

    assert result["status"] == "unavailable"
    assert result["available"] is False
    assert result["warning"] == "Live forecast unavailable; Scenario Mode remains active."


def test_forecast_request_includes_timezone_and_required_hourly_variables() -> None:
    query = open_meteo._forecast_url(11.5, 76.75)

    assert query.startswith(open_meteo.FORECAST_URL)
    assert "hourly=rain%2Cprecipitation%2Cprecipitation_probability" in query
    assert "timezone=Asia%2FKolkata" in query
    assert "current=precipitation" in query
