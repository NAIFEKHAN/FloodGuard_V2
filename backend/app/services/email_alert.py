"""SMTP email delivery and per-village alert cooldown."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
import logging
import os
import re
import sqlite3
import smtplib
import threading
from email.utils import getaddresses
from zoneinfo import ZoneInfo

from backend.app.sensor_store import database_path


logger = logging.getLogger(__name__)
DEFAULT_ALERT_EMAILS = (
    "nathiyaranga96@gmail.com",
    "gugank04@gmail.com",
    "naifekhanit@gmail.com",
    "kumaraguru485@gmail.com",
)
ALERT_COOLDOWN = timedelta(minutes=30)
_delivery_lock = threading.Lock()


def _recipients() -> list[str]:
    configured = os.getenv("ALERT_EMAILS")
    recipients = (
        [address.strip() for address in configured.split(",") if address.strip()]
        if configured is not None
        else list(DEFAULT_ALERT_EMAILS)
    )
    valid = [
        address
        for address in recipients
        if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", address)
    ]
    if len(valid) != len(recipients) or not valid:
        raise ValueError("ALERT_EMAILS contains an invalid or empty recipient list.")
    return valid


def _claim_alert(village_code: str) -> tuple[bool, str | None]:
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path, timeout=10) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS email_alerts (
                village_lgd_code TEXT PRIMARY KEY,
                sent_at TEXT NOT NULL
            )
            """
        )
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT sent_at FROM email_alerts WHERE village_lgd_code = ?",
            (village_code,),
        ).fetchone()
        now = datetime.now(UTC)
        if row is not None and now - datetime.fromisoformat(row[0]) < ALERT_COOLDOWN:
            return True, row[0]
        claimed_at = now.isoformat()
        connection.execute(
            """
            INSERT INTO email_alerts (village_lgd_code, sent_at)
            VALUES (?, ?)
            ON CONFLICT(village_lgd_code) DO UPDATE SET sent_at = excluded.sent_at
            """,
            (village_code, claimed_at),
        )
    return False, claimed_at


def _release_failed_claim(village_code: str, claimed_at: str) -> None:
    with sqlite3.connect(database_path(), timeout=10) as connection:
        connection.execute(
            "DELETE FROM email_alerts WHERE village_lgd_code = ? AND sent_at = ?",
            (village_code, claimed_at),
        )


def _smtp_configuration() -> tuple[str, int, str, str, str]:
    host = os.getenv("SMTP_HOST", "smtp.gmail.com").strip()
    port_text = os.getenv("SMTP_PORT", "465").strip()
    username = os.getenv("SMTP_USER", "").strip()
    password = os.getenv("SMTP_APP_PASSWORD", "").strip()
    sender = os.getenv("SMTP_FROM", username).strip()
    if not username or not password:
        raise RuntimeError("SMTP_USER and SMTP_APP_PASSWORD are not configured.")
    try:
        port = int(port_text)
    except ValueError as exc:
        raise RuntimeError("SMTP_PORT must be a valid port number.") from exc
    if not 1 <= port <= 65535:
        raise RuntimeError("SMTP_PORT must be between 1 and 65535.")
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", sender):
        raise RuntimeError("SMTP sender address is invalid.")
    return host, port, username, password, sender


def smtp_configured() -> bool:
    return bool(os.getenv("SMTP_USER", "").strip() and os.getenv("SMTP_APP_PASSWORD", "").strip())


def recipient_count() -> int:
    try:
        return len(_recipients())
    except ValueError:
        return 0


def _send(message: EmailMessage) -> tuple[int, int]:
    host, port, username, password, _sender = _smtp_configuration()
    with smtplib.SMTP_SSL(host, port, timeout=15) as smtp:
        smtp.login(username, password)
        refused = smtp.send_message(message)
    attempted = len(getaddresses(message.get_all("To", [])))
    return attempted - len(refused), len(refused)


def _message(subject: str, body: str, recipients: list[str]) -> EmailMessage:
    _host, _port, _username, _password, sender = _smtp_configuration()
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message.set_content(body)
    return message


def send_test_email() -> dict[str, object]:
    recipients = _recipients()
    logger.info("[ALERT] Preparing test email alert")
    logger.info("[ALERT] Sending to %s recipients", len(recipients))
    message = _message(
        "FloodGuard Test Alert",
        "FloodGuard email notification system is working successfully.\n",
        recipients,
    )
    accepted_count, refused_count = _send(message)
    status = "accepted_by_smtp" if refused_count == 0 else "partial"
    logger.info(
        "[ALERT] SMTP accepted test email for %s of %s recipients; refused=%s",
        accepted_count,
        len(recipients),
        refused_count,
    )
    return {
        "status": status,
        "recipient_count": len(recipients),
        "accepted_recipient_count": accepted_count,
        "refused_recipient_count": refused_count,
    }


def send_high_risk_alert(
    *,
    village_code: str,
    village_name: str,
    risk_score: float,
    rainfall_mm: float | None,
    weather_source: str,
    forecast_status: str,
) -> dict[str, object]:
    logger.info("[ALERT] HIGH risk detected for %s", village_name)
    try:
        recipients = _recipients()
    except ValueError as exc:
        logger.warning("[ALERT] Email delivery unavailable: %s", exc)
        return {"status": "not_configured", "recipient_count": 0}
    try:
        _smtp_configuration()
    except RuntimeError as exc:
        logger.warning("[ALERT] Email delivery unavailable: %s", exc)
        return {"status": "not_configured", "recipient_count": len(recipients)}

    with _delivery_lock:
        try:
            duplicate, claimed_at = _claim_alert(village_code)
            if duplicate:
                logger.info(
                    "[ALERT] Alert already sent recently for %s - skipped",
                    village_name,
                )
                return {
                    "status": "skipped_cooldown",
                    "recipient_count": len(recipients),
                    "last_sent_at": claimed_at,
                }

            logger.info("[ALERT] Preparing email alert")
            now = datetime.now(ZoneInfo("Asia/Kolkata"))
            rainfall_line = (
                f"{rainfall_mm:.1f} mm / next 24 hours"
                if rainfall_mm is not None
                else "Unavailable"
            )
            body = (
                "FLOODGUARD FLOOD RISK ALERT\n\n"
                f"Village: {village_name}\n"
                "Risk Level: HIGH\n"
                f"Risk Score: {risk_score:.1f}\n"
                f"Forecast Rainfall: {rainfall_line}\n"
                f"Weather Source: {weather_source}\n"
                f"Forecast Status: {forecast_status.title()}\n"
                f"Time: {now:%d %b %Y, %I:%M %p}\n\n"
                "A high flood-risk condition has been detected for this location.\n\n"
                "Recommended action:\n"
                "- Monitor local conditions.\n"
                "- Avoid low-lying and flood-prone areas.\n"
                "- Prepare for possible evacuation if instructed by authorities.\n\n"
                "This is a FloodGuard prototype warning generated from forecast and "
                "risk-model data. It is not an official government emergency warning.\n"
            )
            message = _message(
                f"FloodGuard Emergency Alert - HIGH Flood Risk - {village_name}",
                body,
                recipients,
            )
            logger.info("[ALERT] Sending to %s recipients", len(recipients))
            accepted_count, refused_count = _send(message)
            sent_at = datetime.now(UTC)
            status = "sent" if refused_count == 0 else "partial"
            logger.info(
                "[ALERT] SMTP accepted HIGH-risk email for %s of %s recipients; refused=%s",
                accepted_count,
                len(recipients),
                refused_count,
            )
            return {
                "status": status,
                "recipient_count": len(recipients),
                "accepted_recipient_count": accepted_count,
                "refused_recipient_count": refused_count,
                "sent_at": sent_at.isoformat(),
            }
        except (
            OSError,
            TimeoutError,
            ValueError,
            RuntimeError,
            sqlite3.Error,
            smtplib.SMTPException,
        ) as exc:
            safe_detail = type(exc).__name__
            if isinstance(exc, smtplib.SMTPResponseException):
                safe_detail += f" (SMTP {exc.smtp_code})"
            claimed_at = locals().get("claimed_at")
            if isinstance(claimed_at, str):
                try:
                    _release_failed_claim(village_code, claimed_at)
                except sqlite3.Error:
                    logger.exception("[ALERT] Could not release failed alert cooldown claim.")
            logger.error("[ALERT] Email delivery failed: %s", safe_detail)
            return {"status": "failed", "recipient_count": len(recipients)}
