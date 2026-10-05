"""Spatially assess road-route exposure to existing FloodGuard context layers."""

from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path
from typing import Any

from pyproj import Transformer
from shapely.geometry import LineString, Point, shape
from shapely.ops import transform

from backend.app.data_catalog import DATA
from backend.app.shelter_service import village_geometries


ROOT = Path(__file__).resolve().parents[2]
EVENTS_PATH = DATA / "processed/landslide_events.csv"
TO_METRIC = Transformer.from_crs("EPSG:4326", "EPSG:32643", always_xy=True).transform


@lru_cache(maxsize=1)
def _historical_event_points() -> tuple[tuple[float, float, str], ...]:
    if not EVENTS_PATH.exists():
        return ()
    points = []
    with EVENTS_PATH.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                latitude = float(row["latitude"])
                longitude = float(row["longitude"])
            except (KeyError, TypeError, ValueError):
                continue
            if -90 <= latitude <= 90 and -180 <= longitude <= 180:
                points.append(
                    (
                        latitude,
                        longitude,
                        row.get("inventory_serial") or row.get("slide_no") or "Unidentified",
                    )
                )
    return tuple(points)


def assess_route_risk(
    route_geometry: dict[str, Any],
    warning_records: dict[str, dict[str, Any]],
    *,
    historical_corridor_m: float = 50.0,
) -> dict[str, Any]:
    """Return exposure intersections; this is never a physical road-safety claim."""
    route = shape(route_geometry)
    if not isinstance(route, LineString) or route.is_empty or len(route.coords) < 2:
        raise ValueError("Route-risk analysis requires a non-empty LineString.")
    route_metric = transform(TO_METRIC, route)
    corridor = route_metric.buffer(historical_corridor_m)

    warning_intersections = []
    high_susceptibility_intersections = []
    geometries = village_geometries()
    for village_code, geometry in geometries.items():
        if not corridor.intersects(transform(TO_METRIC, geometry)):
            continue
        warning = warning_records.get(village_code)
        if not warning:
            continue
        intersection = {
            "village_lgd_code": village_code,
            "village_name": warning.get("village_name"),
            "warning_stage": warning.get("stage"),
            "warning_label": warning.get("label"),
        }
        if warning.get("stage") in {"red", "orange", "yellow"}:
            warning_intersections.append(intersection)
        try:
            score = float(warning.get("baseline_score"))
        except (TypeError, ValueError):
            score = None
        if score is not None and score >= 80:
            high_susceptibility_intersections.append(
                {
                    "village_lgd_code": village_code,
                    "village_name": warning.get("village_name"),
                    "baseline_susceptibility": score,
                }
            )

    event_intersections = []
    for latitude, longitude, event_id in _historical_event_points():
        point_metric = transform(TO_METRIC, Point(longitude, latitude))
        if corridor.intersects(point_metric):
            event_intersections.append(
                {"event_id": event_id, "latitude": latitude, "longitude": longitude}
            )

    stages = {item["warning_stage"] for item in warning_intersections}
    if "red" in stages:
        status = "high_risk"
    elif stages or high_susceptibility_intersections or event_intersections:
        status = "caution"
    else:
        status = "preferred"

    return {
        "status": status,
        "warning_intersections": warning_intersections,
        "high_susceptibility_intersections": high_susceptibility_intersections,
        "historical_event_proximity": event_intersections,
        "historical_event_corridor_m": historical_corridor_m,
        "limitations": [
            "Route-risk intersections do not establish road closure, current hazard, or physical safety.",
            "Historical inventory coordinates are assessed under the project's existing WGS84 interpretation, whose source datum remains undocumented.",
            "A single soil-moisture sensor is not treated as a road-corridor hazard.",
        ],
    }


def route_ranking_key(route: dict[str, Any]) -> tuple[int, int, int, int, int, float, float]:
    assessment = route["risk_assessment"]
    status_rank = {"preferred": 0, "caution": 1, "high_risk": 2}.get(
        assessment["status"], 3
    )
    warnings = assessment["warning_intersections"]
    return (
        status_rank,
        sum(item["warning_stage"] == "red" for item in warnings),
        sum(item["warning_stage"] == "orange" for item in warnings),
        len(assessment["high_susceptibility_intersections"]),
        len(assessment["historical_event_proximity"]),
        route["distance_m"],
        route["duration_s"],
    )
