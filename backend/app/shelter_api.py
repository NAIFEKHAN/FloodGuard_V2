"""Read-only shelter inventory and road-route endpoints."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from backend.app import shelter_service
from backend.app.route_risk import assess_route_risk, route_ranking_key
from backend.app.routing_service import (
    RoutingUnavailableError,
    get_route_alternatives,
)
from backend.app.warning_api import evaluate_all_warnings


logger = logging.getLogger(__name__)
router = APIRouter(tags=["shelters and evacuation routes"])
NO_SHELTER_MESSAGE = (
    "No source-verified shelter records are available; road routing is disabled."
)


def _readable_shelters() -> list[dict[str, Any]]:
    try:
        return list(shelter_service.get_shelter_records())
    except FileNotFoundError as error:
        raise HTTPException(status_code=503, detail="Shelter source is unavailable.") from error


def _route_eligible_shelters(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        shelter for shelter in records
        if shelter_service.shelter_is_route_eligible(shelter)
    ]


@router.get("/api/shelters")
def get_shelters() -> dict[str, Any]:
    records = _readable_shelters()
    coverage = shelter_service.shelter_coverage()
    eligible = _route_eligible_shelters(records)
    return {
        "status": "available" if eligible else "unavailable",
        "records": records,
        "coverage": coverage,
        "route_eligible_count": len(eligible),
        "message": None if eligible else NO_SHELTER_MESSAGE,
        "data_type": "static",
        "provenance": {
            "source": "data/raw/shelters/shelters.csv",
            "source_status": coverage["source_coverage"],
            "last_updated": max(
                (record["last_updated"] for record in records if record["last_updated"]),
                default=None,
            ),
        },
    }


@router.get("/api/shelters/{shelter_id}")
def get_shelter(shelter_id: str) -> dict[str, Any]:
    record = next(
        (
            item for item in _readable_shelters()
            if item["shelter_id"] == shelter_id
        ),
        None,
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Shelter was not found.")
    return record


@router.get("/api/villages/{village_code}/shelters")
def get_village_shelters(village_code: str) -> dict[str, Any]:
    if not shelter_service.village_code_exists(village_code):
        raise HTTPException(status_code=404, detail="Village LGD code was not found.")
    origin = shelter_service.village_origin(village_code)
    shelters = _route_eligible_shelters(_readable_shelters())
    if origin:
        for record in shelters:
            record["direct_distance_km_prefilter"] = shelter_service.distance_km(
                origin["latitude"],
                origin["longitude"],
                record["latitude"],
                record["longitude"],
            )
        shelters.sort(key=lambda record: record["direct_distance_km_prefilter"])
    return {
        "status": "available" if shelters else "unavailable",
        "village_lgd_code": village_code,
        "origin": origin,
        "records": shelters,
        "sort_basis": "straight_line_distance_prefilter_only; road_routes_required_for_comparison" if shelters and origin else None,
        "message": None if shelters else NO_SHELTER_MESSAGE,
        "coverage": shelter_service.shelter_coverage(),
    }


@router.get("/api/evacuation-route")
def get_evacuation_route(
    shelter_id: str = Query(min_length=1, max_length=100),
    village_code: str | None = Query(default=None, min_length=1, max_length=32),
    origin_lat: float | None = Query(default=None, ge=-90, le=90),
    origin_lon: float | None = Query(default=None, ge=-180, le=180),
    mode: str = Query(default="scenario", pattern="^(scenario|current)$"),
    scenario: str = Query(default="baseline"),
    multiplier: float | None = Query(default=None, ge=0.1, le=5.0),
) -> dict[str, Any]:
    if (origin_lat is None) != (origin_lon is None):
        raise HTTPException(
            status_code=422,
            detail="origin_lat and origin_lon must be supplied together.",
        )
    if origin_lat is not None:
            origin_validation = shelter_service.validate_coordinates(
                origin_lat,
                origin_lon,
            )
            if origin_validation["in_study_extent"] is not True:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "Route origin must be within the validated study extent; "
                        "the extent must also be available."
                    ),
                )
            origin = {
                "latitude": origin_lat,
                "longitude": origin_lon,
            "coordinate_method": "user_provided_location",
        }
    elif village_code:
        if not shelter_service.village_code_exists(village_code):
            raise HTTPException(status_code=404, detail="Village LGD code was not found.")
        origin = shelter_service.village_origin(village_code)
        if origin is None:
            raise HTTPException(
                status_code=422,
                detail="No validated village polygon is available for a route origin.",
            )
    else:
        raise HTTPException(
            status_code=422,
            detail="Provide village_code or a user-permitted origin_lat/origin_lon pair.",
        )

    shelter = next(
        (
            record for record in _readable_shelters()
            if record["shelter_id"] == shelter_id
        ),
        None,
    )
    if shelter is None:
        raise HTTPException(status_code=404, detail="Shelter was not found.")
    if not shelter_service.shelter_is_route_eligible(shelter):
        raise HTTPException(
            status_code=409,
            detail="Shelter is not verified and route-eligible.",
        )

    try:
        routes = get_route_alternatives(
            (float(origin["latitude"]), float(origin["longitude"])),
            (float(shelter["latitude"]), float(shelter["longitude"])),
        )
    except RoutingUnavailableError as error:
        logger.warning("Road route unavailable for shelter %s: %s", shelter_id, error)
        raise HTTPException(
            status_code=503,
            detail="Road route currently unavailable.",
        ) from error

    evaluation = evaluate_all_warnings(
        mode=mode,
        scenario=scenario,
        multiplier=multiplier,
        persist_history=False,
    )
    warning_by_village = {
        item["village_lgd_code"]: item
        for item in evaluation["records"]
        if item.get("status") == "available"
    }
    evaluated_routes = []
    for route in routes:
        risk = assess_route_risk(route["geometry"], warning_by_village)
        evaluated_routes.append({**route, "risk_assessment": risk})
    evaluated_routes.sort(key=route_ranking_key)
    selected = evaluated_routes[0]
    snapped_origin = selected.get("snapped_origin")
    snapped_destination = selected.get("snapped_destination")
    return {
        "status": "available",
        "origin": {
            **origin,
            "original_coordinate": [
                origin["latitude"], origin["longitude"]
            ],
            "route_snapped_coordinate": (
                [snapped_origin[1], snapped_origin[0]]
                if snapped_origin else None
            ),
        },
        "destination": {
            "shelter_id": shelter["shelter_id"],
            "name": shelter["name"],
            "latitude": shelter["latitude"],
            "longitude": shelter["longitude"],
            "verification": shelter["verification"],
            "provenance": shelter["provenance"],
            "original_coordinate": [shelter["latitude"], shelter["longitude"]],
            "route_snapped_coordinate": (
                [snapped_destination[1], snapped_destination[0]]
                if snapped_destination else None
            ),
        },
        "route": {
            "geometry": selected["geometry"],
            "distance_m": selected["distance_m"],
            "distance_km": selected["distance_m"] / 1000,
            "duration_s": selected["duration_s"],
            "estimated_minutes": selected["duration_s"] / 60,
            "provider": selected["provider"],
            "generated_at": selected["generated_at"],
            "recommendation_label": (
                "Lowest assessed exposure among returned alternatives"
                if selected["risk_assessment"]["status"] != "high_risk"
                else "Available alternative with high-risk context"
            ),
        },
        "risk_assessment": selected["risk_assessment"],
        "alternatives": [
            {
                "geometry": route["geometry"],
                "distance_m": route["distance_m"],
                "duration_s": route["duration_s"],
                "provider": route["provider"],
                "risk_assessment": route["risk_assessment"],
                "rank": index + 1,
                "recommendation_label": (
                    "Lower-risk available route"
                    if index == 0 and route["risk_assessment"]["status"] != "high_risk"
                    else "Available alternative"
                ),
            }
            for index, route in enumerate(evaluated_routes)
        ],
        "warning_mode": evaluation["mode_label"],
        "generated_at": datetime.now(UTC).isoformat(),
        "disclaimer": (
            "Route guidance is based on available map and FloodGuard risk data. "
            "Confirm road accessibility and follow instructions from local "
            "authorities during emergencies. A returned route is not a safety guarantee."
        ),
    }
