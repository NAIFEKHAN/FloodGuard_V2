"""Compatibility imports for the former IMD forecast service module."""

from backend.app.services.open_meteo import fetch_forecast, get_forecast_status

__all__ = ["fetch_forecast", "get_forecast_status"]
