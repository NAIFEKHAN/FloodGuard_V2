"""Email notification endpoints over current FloodGuard risk results."""

from __future__ import annotations

import smtplib
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.app.services.email_alert import (
    send_high_risk_alert,
    send_test_email,
    smtp_configured,
)
from pipeline.run_rainfall_scenario import classify_scenario_tier


router = APIRouter(prefix="/api/alerts", tags=["email-alerts"])


class RiskAlertRequest(BaseModel):
    village_lgd_code: str = Field(min_length=1, max_length=32)
    mode: Literal["auto", "scenario"] = "auto"
    scenario: Literal["moderate", "baseline", "heavy", "extreme"] = "baseline"
    multiplier: float | None = Field(default=None, ge=0.1, le=5.0)


@router.get("/status")
def email_alert_status() -> dict[str, object]:
    from backend.app.services.email_alert import recipient_count

    return {
        "configured": smtp_configured(),
        "recipient_count": recipient_count(),
    }


@router.post("/test-email")
def send_test_email_endpoint() -> dict[str, object]:
    try:
        return send_test_email()
    except smtplib.SMTPAuthenticationError as exc:
        raise HTTPException(
            status_code=502,
            detail=(
                "Gmail rejected SMTP authentication. Verify SMTP_USER and use a "
                "valid Google App Password; no email was sent."
            ),
        ) from exc
    except smtplib.SMTPServerDisconnected as exc:
        raise HTTPException(
            status_code=502,
            detail=(
                "Gmail closed the SMTP connection before confirming delivery. "
                "Verify the Google App Password and Gmail account security settings; "
                "no email was confirmed sent."
            ),
        ) from exc
    except (
        OSError,
        TimeoutError,
        ValueError,
        RuntimeError,
        smtplib.SMTPException,
    ) as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Test email could not be sent ({type(exc).__name__}).",
        ) from exc


@router.post("/send")
def send_risk_alert(request: RiskAlertRequest) -> dict[str, object]:
    from backend.app import main

    if request.mode == "auto":
        data = main.get_weather_forecast(village_lgd_code=request.village_lgd_code)
        if not data.get("available") or not data.get("records"):
            raise HTTPException(
                status_code=503,
                detail="No live or cached forecast risk result is available for this village.",
            )
        record = data["records"][0]
        weather_source = str(record.get("weather_source") or "Open-Meteo")
        forecast_status = str(record.get("weather_status") or data.get("status") or "unknown")
        rainfall = record.get("forecast_rainfall_input_mm")
        automatic_alert_result = record.get("email_alert")
    else:
        data = main.get_rainfall_scenario(
            scenario=request.scenario,
            multiplier=request.multiplier,
        )
        record = next(
            (
                item
                for item in data["records"]
                if str(item["village_lgd_code"]) == request.village_lgd_code
            ),
            None,
        )
        if record is None:
            raise HTTPException(
                status_code=404,
                detail="Village is not in the validated model coverage.",
            )
        weather_source = "Manual rainfall scenario"
        forecast_status = "scenario"
        rainfall = None
        automatic_alert_result = None

    score = float(record["scenario_susceptibility_0_100"])
    tier = classify_scenario_tier(score)
    if tier != "HIGH":
        return {
            "status": "not_high_risk",
            "village_name": record["village_name_en"],
            "risk_level": tier,
            "risk_score": score,
            "recipient_count": 0,
        }
    if automatic_alert_result is not None:
        result = automatic_alert_result
    else:
        result = send_high_risk_alert(
            village_code=request.village_lgd_code,
            village_name=str(record["village_name_en"]),
            risk_score=score,
            rainfall_mm=float(rainfall) if rainfall is not None else None,
            weather_source=weather_source,
            forecast_status=forecast_status,
        )
    return {
        **result,
        "village_name": record["village_name_en"],
        "risk_level": tier,
        "risk_score": score,
    }
