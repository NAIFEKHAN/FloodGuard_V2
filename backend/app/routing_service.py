"""Configurable backend adapters for road-routing providers."""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any

import httpx


logger = logging.getLogger(__name__)
OSRM_DEFAULT_URL = "https://router.project-osrm.org"
ORS_DEFAULT_URL = "https://api.openrouteservice.org"
MAX_ROUTE_ALTERNATIVES = 3
REQUEST_TIMEOUT_SECONDS = 12.0


class RoutingUnavailableError(RuntimeError):
    """Raised when a configured road-routing provider cannot return a route."""


def _validate_coordinate(latitude: float, longitude: float) -> None:
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        raise ValueError("Route coordinates must be valid WGS84 latitude/longitude.")


def _normalize_route(
    *,
    geometry: dict[str, Any],
    distance_m: Any,
    duration_s: Any,
    provider: str,
    snapped_origin: list[float] | None = None,
    snapped_destination: list[float] | None = None,
) -> dict[str, Any]:
    if geometry.get("type") != "LineString":
        raise RoutingUnavailableError("Routing provider returned non-LineString geometry.")
    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, list) or len(coordinates) < 2:
        raise RoutingUnavailableError("Routing provider returned invalid route geometry.")
    for coordinate in coordinates:
        if (
            not isinstance(coordinate, (list, tuple))
            or len(coordinate) < 2
            or not all(isinstance(value, (int, float)) for value in coordinate[:2])
            or not (-180 <= coordinate[0] <= 180 and -90 <= coordinate[1] <= 90)
        ):
            raise RoutingUnavailableError("Routing provider returned invalid route coordinates.")
    try:
        distance = float(distance_m)
        duration = float(duration_s)
    except (TypeError, ValueError) as error:
        raise RoutingUnavailableError("Routing provider omitted route distance or time.") from error
    if distance <= 0 or duration <= 0:
        raise RoutingUnavailableError("Routing provider returned non-positive route distance or time.")
    return {
        "geometry": geometry,
        "distance_m": distance,
        "duration_s": duration,
        "provider": provider,
        "snapped_origin": snapped_origin,
        "snapped_destination": snapped_destination,
        "generated_at": datetime.now(UTC).isoformat(),
    }


def _provider_config() -> tuple[str, str, str | None]:
    provider = os.environ.get("ROUTING_PROVIDER", "osrm").strip().lower()
    base_url = os.environ.get("ROUTING_BASE_URL", "").strip().rstrip("/")
    api_key = os.environ.get("ROUTING_API_KEY") or None
    if provider == "osrm":
        return provider, base_url or OSRM_DEFAULT_URL, api_key
    if provider == "openrouteservice":
        if not api_key:
            raise RoutingUnavailableError(
                "ROUTING_API_KEY is required for the OpenRouteService provider."
            )
        return provider, base_url or ORS_DEFAULT_URL, api_key
    raise RoutingUnavailableError(
        f"Unsupported ROUTING_PROVIDER {provider!r}; configure osrm or openrouteservice."
    )


def _osrm_routes(
    client: httpx.Client,
    base_url: str,
    origin: tuple[float, float],
    destination: tuple[float, float],
) -> list[dict[str, Any]]:
    origin_lat, origin_lon = origin
    destination_lat, destination_lon = destination
    response = client.get(
        f"{base_url}/route/v1/driving/"
        f"{origin_lon},{origin_lat};{destination_lon},{destination_lat}",
        params={
            "alternatives": str(MAX_ROUTE_ALTERNATIVES),
            "steps": "false",
            "overview": "full",
            "geometries": "geojson",
        },
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != "Ok" or not payload.get("routes"):
        raise RoutingUnavailableError(
            f"OSRM did not find a route ({payload.get('code', 'no_route')})."
        )
    waypoints = payload.get("waypoints") or []
    snapped_origin = waypoints[0].get("location") if len(waypoints) > 0 else None
    snapped_destination = waypoints[1].get("location") if len(waypoints) > 1 else None
    return [
        _normalize_route(
            geometry=route.get("geometry") or {},
            distance_m=route.get("distance"),
            duration_s=route.get("duration"),
            provider="OSRM / OpenStreetMap",
            snapped_origin=snapped_origin,
            snapped_destination=snapped_destination,
        )
        for route in payload["routes"]
    ]


def _ors_routes(
    client: httpx.Client,
    base_url: str,
    api_key: str,
    origin: tuple[float, float],
    destination: tuple[float, float],
) -> list[dict[str, Any]]:
    response = client.post(
        f"{base_url}/v2/directions/driving-car/geojson",
        headers={"Authorization": api_key},
        json={
            "coordinates": [
                [origin[1], origin[0]],
                [destination[1], destination[0]],
            ],
            "alternative_routes": {
                "target_count": MAX_ROUTE_ALTERNATIVES,
                "share_factor": 0.6,
                "weight_factor": 1.4,
            },
        },
    )
    response.raise_for_status()
    payload = response.json()
    features = payload.get("features") or []
    if not features:
        raise RoutingUnavailableError("OpenRouteService did not find a road route.")
    routes = []
    for feature in features:
        summary = (feature.get("properties") or {}).get("summary") or {}
        routes.append(
            _normalize_route(
                geometry=feature.get("geometry") or {},
                distance_m=summary.get("distance"),
                duration_s=summary.get("duration"),
                provider="OpenRouteService / OpenStreetMap",
            )
        )
    return routes


def get_route_alternatives(
    origin: tuple[float, float],
    destination: tuple[float, float],
) -> list[dict[str, Any]]:
    """Request road routes and normalize their geometry, distance and duration."""
    for latitude, longitude in (origin, destination):
        _validate_coordinate(latitude, longitude)
    provider, base_url, api_key = _provider_config()
    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            if provider == "osrm":
                return _osrm_routes(client, base_url, origin, destination)
            assert api_key is not None
            return _ors_routes(client, base_url, api_key, origin, destination)
    except (httpx.HTTPError, ValueError) as error:
        logger.warning("%s road-routing request failed: %s", provider, error)
        raise RoutingUnavailableError(
            f"{provider} road-routing request failed."
        ) from error
