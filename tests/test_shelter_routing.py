"""Tests for source-verified shelter inventory and road-route support."""

import csv

from fastapi.testclient import TestClient
from shapely.geometry import Polygon

from backend.app import route_risk, shelter_api
from backend.app import shelter_service
from backend.app.main import app
from backend.app.routing_service import (
    RoutingUnavailableError,
    _normalize_route,
    _provider_config,
)


client = TestClient(app)

ROUTE_GEOMETRY = {
    "type": "LineString",
    "coordinates": [[76.7, 11.4], [76.71, 11.41]],
}


def _configure_route_api(monkeypatch, get_routes) -> None:
    facility = {
        "shelter_id": "verified-1",
        "name": "Verified facility",
        "latitude": 11.41,
        "longitude": 76.71,
        "operational": True,
        "verification": {"status": "verified", "source": "test", "verified_at": "2026-01-01"},
        "provenance": {"source": "test"},
    }
    monkeypatch.setattr(
        shelter_api.shelter_service,
        "get_shelter_records",
        lambda: [facility],
    )
    monkeypatch.setattr(
        shelter_api.shelter_service,
        "shelter_is_route_eligible",
        lambda _: True,
    )
    monkeypatch.setattr(shelter_api, "get_route_alternatives", get_routes)
    monkeypatch.setattr(
        shelter_api,
        "evaluate_all_warnings",
        lambda **_: {"records": [], "mode_label": "RAINFALL SCENARIO"},
    )
    monkeypatch.setattr(
        shelter_api,
        "assess_route_risk",
        lambda *_: {
            "status": "preferred",
            "warning_intersections": [],
            "high_susceptibility_intersections": [],
            "historical_event_proximity": [],
        },
    )


def test_shelter_inventory_reports_no_verified_coverage() -> None:
    response = client.get("/api/shelters")

    assert response.status_code == 200
    payload = response.json()
    assert payload["records"] == []
    assert payload["route_eligible_count"] == 0
    assert payload["coverage"]["source_coverage"] == "no_verified_shelter_records"
    assert payload["status"] == "unavailable"


def test_route_api_does_not_route_to_unknown_shelter() -> None:
    response = client.get(
        "/api/evacuation-route",
        params={
            "shelter_id": "not-in-inventory",
            "origin_lat": 11.4,
            "origin_lon": 76.7,
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Shelter was not found."


def test_route_api_rejects_user_origin_outside_study_extent() -> None:
    response = client.get(
        "/api/evacuation-route",
        params={
            "shelter_id": "not-in-inventory",
            "origin_lat": 0,
            "origin_lon": 0,
        },
    )

    assert response.status_code == 422
    assert "within the validated study extent" in response.json()["detail"]


def test_shelter_routing_requires_separately_verified_operational_status(
    tmp_path,
    monkeypatch,
) -> None:
    path = tmp_path / "shelters.csv"
    fields = [
        "shelter_id", "name", "type", "village_lgd_code", "taluk",
        "latitude", "longitude", "address", "capacity", "contact",
        "source", "last_updated", "verification_status", "verified_at",
        "operational_status",
    ]
    rows = [
        {
            "shelter_id": "unknown-operation",
            "name": "Facility with unknown operating status",
            "latitude": "11.4",
            "longitude": "76.7",
            "source": "source record",
            "verification_status": "verified",
            "verified_at": "2026-01-01",
            "operational_status": "unknown",
        },
        {
            "shelter_id": "operational",
            "name": "Facility with verified operating status",
            "latitude": "11.4",
            "longitude": "76.7",
            "source": "source record",
            "verification_status": "verified",
            "verified_at": "2026-01-01",
            "operational_status": "operational",
        },
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    monkeypatch.setattr(shelter_service, "SHELTER_PATH", path)
    monkeypatch.setattr(shelter_service, "study_extent", lambda: (76.0, 11.0, 77.0, 12.0))
    monkeypatch.setattr(shelter_service, "_village_records", lambda: {})
    shelter_service.get_shelter_records.cache_clear()
    try:
        records = shelter_service.get_shelter_records()

        assert records[0]["verification"]["status"] == "verified"
        assert records[0]["operational"] is False
        assert shelter_service.shelter_is_route_eligible(records[0]) is False
        assert records[1]["operational"] is True
        assert shelter_service.shelter_is_route_eligible(records[1]) is True
    finally:
        shelter_service.get_shelter_records.cache_clear()


def test_village_shelters_include_route_eligible_facilities_across_villages(
    monkeypatch,
) -> None:
    facilities = [
        {
            "shelter_id": "far",
            "village_lgd_code": "other-village",
            "latitude": 11.5,
            "longitude": 76.8,
        },
        {
            "shelter_id": "near",
            "village_lgd_code": "other-village",
            "latitude": 11.41,
            "longitude": 76.71,
        },
    ]
    monkeypatch.setattr(shelter_api.shelter_service, "village_code_exists", lambda _: True)
    monkeypatch.setattr(
        shelter_api.shelter_service,
        "village_origin",
        lambda _: {"latitude": 11.4, "longitude": 76.7},
    )
    monkeypatch.setattr(
        shelter_api.shelter_service,
        "get_shelter_records",
        lambda: facilities,
    )
    monkeypatch.setattr(
        shelter_api.shelter_service,
        "shelter_is_route_eligible",
        lambda _: True,
    )
    monkeypatch.setattr(
        shelter_api.shelter_service,
        "shelter_coverage",
        lambda: {"total_records": 2, "source_coverage": "test"},
    )

    response = client.get("/api/villages/635099/shelters")

    assert response.status_code == 200
    payload = response.json()
    assert [item["shelter_id"] for item in payload["records"]] == ["near", "far"]
    assert payload["sort_basis"] == (
        "straight_line_distance_prefilter_only; road_routes_required_for_comparison"
    )


def test_routing_adapter_normalizes_only_road_line_geometry() -> None:
    route = _normalize_route(
        geometry={
            "type": "LineString",
            "coordinates": [[76.7, 11.4], [76.8, 11.5]],
        },
        distance_m=1250,
        duration_s=300,
        provider="test provider",
    )

    assert route["distance_m"] == 1250
    assert route["duration_s"] == 300
    assert route["geometry"]["type"] == "LineString"


def test_routing_adapter_rejects_invalid_or_missing_road_geometry() -> None:
    try:
        _normalize_route(
            geometry={"type": "LineString", "coordinates": [[76.7, 11.4]]},
            distance_m=100,
            duration_s=20,
            provider="test provider",
        )
    except RoutingUnavailableError:
        pass
    else:
        raise AssertionError("Invalid road geometry must be rejected.")


def test_openrouteservice_requires_backend_api_key(monkeypatch) -> None:
    monkeypatch.setenv("ROUTING_PROVIDER", "openrouteservice")
    monkeypatch.delenv("ROUTING_API_KEY", raising=False)

    try:
        _provider_config()
    except RoutingUnavailableError as error:
        assert "ROUTING_API_KEY is required" in str(error)
    else:
        raise AssertionError("OpenRouteService must require a server-side API key.")


def test_route_api_returns_provider_geometry_and_context_without_safe_claim(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        shelter_api.shelter_service,
        "validate_coordinates",
        lambda *_: {"valid": True, "in_study_extent": True},
    )
    _configure_route_api(
        monkeypatch,
        lambda *_: [{
            "geometry": ROUTE_GEOMETRY,
            "distance_m": 1200,
            "duration_s": 300,
            "provider": "test road provider",
            "generated_at": "2026-01-01T00:00:00Z",
        }],
    )

    response = client.get(
        "/api/evacuation-route",
        params={
            "shelter_id": "verified-1",
            "origin_lat": 11.4,
            "origin_lon": 76.7,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["route"]["geometry"] == ROUTE_GEOMETRY
    assert payload["route"]["distance_km"] == 1.2
    assert payload["risk_assessment"]["status"] == "preferred"
    assert "not a safety guarantee" in payload["disclaimer"]
    assert "safe route" not in payload["route"]["recommendation_label"].lower()


def test_route_api_surfaces_provider_unavailability(monkeypatch) -> None:
    monkeypatch.setattr(
        shelter_api.shelter_service,
        "validate_coordinates",
        lambda *_: {"valid": True, "in_study_extent": True},
    )

    def unavailable(*_):
        raise RoutingUnavailableError("provider unavailable")

    _configure_route_api(monkeypatch, unavailable)

    response = client.get(
        "/api/evacuation-route",
        params={
            "shelter_id": "verified-1",
            "origin_lat": 11.4,
            "origin_lon": 76.7,
        },
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Road route currently unavailable."


def test_route_risk_reports_existing_layer_intersections(monkeypatch) -> None:
    monkeypatch.setattr(
        route_risk,
        "village_geometries",
        lambda: {"635099": Polygon([(76.69, 11.39), (76.72, 11.39), (76.72, 11.42), (76.69, 11.42)])},
    )
    monkeypatch.setattr(
        route_risk,
        "_historical_event_points",
        lambda: ((11.4, 76.705, "event-1"),),
    )

    assessment = route_risk.assess_route_risk(
        {"type": "LineString", "coordinates": [[76.68, 11.4], [76.73, 11.4]]},
        {
            "635099": {
                "stage": "red",
                "label": "Red warning",
                "village_name": "Test Village",
                "baseline_score": 85,
            }
        },
    )

    assert assessment["status"] == "high_risk"
    assert assessment["warning_intersections"][0]["warning_stage"] == "red"
    assert assessment["high_susceptibility_intersections"][0]["village_lgd_code"] == "635099"
    assert assessment["historical_event_proximity"][0]["event_id"] == "event-1"
