from __future__ import annotations

from email.message import EmailMessage
import smtplib

from fastapi.testclient import TestClient

from backend.app import main
from backend.app.main import app
from backend.app.services import email_alert


client = TestClient(app)


def _configure_email(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setenv("SMTP_PORT", "465")
    monkeypatch.setenv("SMTP_USER", "floodguard@example.com")
    monkeypatch.setenv("SMTP_APP_PASSWORD", "test-only-secret")
    monkeypatch.delenv("ALERT_EMAILS", raising=False)
    monkeypatch.setattr(
        email_alert,
        "database_path",
        lambda: tmp_path / "alerts.sqlite3",
    )


def test_high_risk_email_is_sent_once_during_cooldown(monkeypatch, tmp_path) -> None:
    _configure_email(monkeypatch, tmp_path)
    sent_messages: list[EmailMessage] = []

    def record_send(message: EmailMessage) -> tuple[int, int]:
        sent_messages.append(message)
        return 4, 0

    monkeypatch.setattr(email_alert, "_send", record_send)

    first = email_alert.send_high_risk_alert(
        village_code="635111",
        village_name="Yedapally",
        risk_score=93.4,
        rainfall_mm=20.7,
        weather_source="Open-Meteo",
        forecast_status="cached",
    )
    second = email_alert.send_high_risk_alert(
        village_code="635111",
        village_name="Yedapally",
        risk_score=93.4,
        rainfall_mm=20.7,
        weather_source="Open-Meteo",
        forecast_status="cached",
    )

    assert first["status"] == "sent"
    assert second["status"] == "skipped_cooldown"
    assert first["recipient_count"] == 4
    assert first["accepted_recipient_count"] == 4
    assert len(sent_messages) == 1
    assert sent_messages[0]["Subject"] == (
        "FloodGuard Emergency Alert - HIGH Flood Risk - Yedapally"
    )
    assert "20.7 mm / next 24 hours" in sent_messages[0].get_content()
    assert "Cached" in sent_messages[0].get_content()
    assert "test-only-secret" not in sent_messages[0].as_string()


def test_high_risk_auto_forecast_triggers_email_once(monkeypatch) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(main, "fetch_forecast", lambda force_refresh=False: {
        "status": "cached",
        "available": True,
        "forecast_status": "CACHED",
        "source": "Open-Meteo",
        "record_count": 40,
        "villages": [
            {
                "village_lgd_code": str(row["village_lgd_code"]),
                "village_name_en": row["village_name_en"],
                "latitude": 11.5,
                "longitude": 76.75,
                "source": "Open-Meteo",
                "status": "cached",
                "updated_at": "2026-10-05T00:00:00+00:00",
                "current_precipitation_mm": 0.0,
                "chosen_feature_value_mm": 400.0,
                "next_24h_rainfall_mm": 400.0,
                "forecast": [],
            }
            for row in main.spatial_dataset()
        ],
    })

    class HighRiskModel:
        def predict_proba(self, values):
            import numpy as np

            return np.tile([0.1, 0.9], (len(values), 1))

    monkeypatch.setattr(main, "ml_model", HighRiskModel)

    def record_alert(**kwargs):
        calls.append(kwargs)
        return {"status": "sent", "recipient_count": 4}

    monkeypatch.setattr(email_alert, "send_high_risk_alert", record_alert)
    selected_code = str(main.spatial_dataset()[0]["village_lgd_code"])

    response = client.get(
        f"/api/weather/forecast?village_lgd_code={selected_code}"
    )
    assert response.status_code == 200
    assert len(calls) == 1
    assert calls[0]["village_code"] == selected_code
    assert response.json()["records"][0]["scenario_tier"] == "HIGH"


def test_test_email_endpoint_does_not_expose_smtp_credentials(monkeypatch) -> None:
    monkeypatch.delenv("SMTP_USER", raising=False)
    monkeypatch.delenv("SMTP_APP_PASSWORD", raising=False)

    response = client.post("/api/alerts/test-email")

    assert response.status_code == 502
    assert "SMTP_USER and SMTP_APP_PASSWORD are not configured" not in response.text
    assert "SMTP_APP_PASSWORD" not in response.text


def test_test_email_reports_smtp_acceptance_without_claiming_inbox_delivery(
    monkeypatch,
) -> None:
    _configure_email(monkeypatch, tmp_path=None)
    monkeypatch.setattr(email_alert, "_send", lambda _message: (4, 0))

    response = client.post("/api/alerts/test-email")

    assert response.status_code == 200
    assert response.json() == {
        "status": "accepted_by_smtp",
        "recipient_count": 4,
        "accepted_recipient_count": 4,
        "refused_recipient_count": 0,
    }


def test_test_email_reports_partial_smtp_acceptance(monkeypatch) -> None:
    _configure_email(monkeypatch, tmp_path=None)
    monkeypatch.setattr(email_alert, "_send", lambda _message: (3, 1))

    response = client.post("/api/alerts/test-email")

    assert response.status_code == 200
    assert response.json()["status"] == "partial"
    assert response.json()["accepted_recipient_count"] == 3
    assert response.json()["refused_recipient_count"] == 1


def test_smtp_authentication_failure_is_safe_and_does_not_crash(monkeypatch, tmp_path, caplog) -> None:
    _configure_email(monkeypatch, tmp_path)

    def fail_send(_message: EmailMessage) -> None:
        raise smtplib.SMTPAuthenticationError(535, b"authentication failed")

    monkeypatch.setattr(email_alert, "_send", fail_send)
    result = email_alert.send_high_risk_alert(
        village_code="635111",
        village_name="Yedapally",
        risk_score=93.4,
        rainfall_mm=20.7,
        weather_source="Open-Meteo",
        forecast_status="live",
    )

    assert result["status"] == "failed"
    assert "test-only-secret" not in caplog.text


def test_status_endpoint_exposes_only_configuration_and_recipient_count() -> None:
    response = client.get("/api/alerts/status")

    assert response.status_code == 200
    assert response.json()["recipient_count"] == 4
    assert "SMTP_APP_PASSWORD" not in response.text
    assert "SMTP_USER" not in response.text
