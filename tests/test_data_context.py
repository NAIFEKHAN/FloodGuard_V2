"""Tests for provenance catalog and unified village context responses."""

from pathlib import Path
from dataclasses import replace

from fastapi.testclient import TestClient

from backend.app import village_context
from backend.app.data_catalog import DATA_SOURCE_BY_ID
from backend.app.main import app


client = TestClient(app)


def test_data_sources_report_available_and_planned_sources() -> None:
    response = client.get("/api/data-sources")

    assert response.status_code == 200
    sources = {source["id"]: source for source in response.json()}
    assert sources["srtm_terrain"]["status"] == "available"
    assert sources["historical_rainfall_villages"]["status"] == "available"
    assert sources["current_weather"]["status"] == "available"
    assert sources["hydrology"]["status"] in {"available", "unavailable"}
    assert sources["flow_direction"]["type"] == "derived"
    assert sources["flow_accumulation"]["type"] == "derived"
    assert sources["drainage_network"]["type"] == "derived"
    assert sources["village_hydrology_features"]["type"] == "derived"
    assert sources["soil_moisture"]["status"] == "planned"
    assert sources["historical_event_linkage"]["status"] == "available"
    assert "conditional" in sources["historical_event_linkage"]["description"].lower()


def test_village_context_uses_lgd_code_and_real_available_sources() -> None:
    response = client.get("/api/villages/635099/context")

    assert response.status_code == 200
    payload = response.json()
    assert payload["village"]["code"] == "635099"
    assert payload["village"]["village_lgd_code"] == "635099"
    assert payload["village"]["name"] == "Kodanad"
    assert payload["village"]["taluk"] == "Kotagiri"
    assert payload["village"]["source_village_code"] != payload["village"]["code"]

    assert payload["risk"]["status"] == "available"
    assert payload["risk"]["data_type"] == "modeled"
    assert 0 <= payload["risk"]["score"] <= 100
    assert payload["risk"]["tier"] in {"low", "medium", "high"}

    assert payload["terrain"]["status"] == "available"
    assert payload["terrain"]["data_type"] == "derived"
    assert payload["terrain"]["data"]["elevation_mean_m"] > 0
    assert 0 <= payload["terrain"]["data"]["slope_mean_deg"] <= 90

    assert payload["rainfall"]["status"] == "available"
    assert payload["rainfall"]["historical"]["date"] == "2024-12-31"
    assert payload["rainfall"]["historical"]["rainfall_1d_mm"] is not None
    assert payload["rainfall"]["data_type"] == "historical"
    assert payload["rainfall"]["scenario"]["data_type"] == "simulated"
    assert payload["rainfall"]["scenario"]["multiplier"] == 1.0

    assert payload["historical_evidence"]["count"] == 2
    assert all(
        event["linkage_status"] == "EXACT_POLYGON_MATCH"
        for event in payload["historical_evidence"]["data"]
    )
    assert payload["historical_evidence"]["data"][0]["event_id"]
    assert payload["historical_evidence"]["limitations"]

    assert payload["model"]["validation"].startswith("6-Fold Leave-One-Taluk-Out")
    assert payload["weather"]["data"] is None
    assert payload["hydrology"]["status"] in {"available", "unavailable"}
    assert payload["hydrology"]["data_type"] == "derived"
    assert payload["hydrology"]["water_accumulation_potential"]["value"] is None
    assert payload["hydrology"]["water_accumulation_potential"]["status"] == "not_derived"
    assert payload["soil_moisture"]["status"] == "unavailable"
    assert payload["soil_moisture"]["data"] is None
    assert payload["sensor"]["data"] is None


def test_village_context_keeps_unmatched_master_village_unavailable() -> None:
    response = client.get("/api/villages/932021/context")

    assert response.status_code == 200
    payload = response.json()
    assert payload["village"]["name"] == "Sholur"
    assert payload["risk"]["status"] == "unavailable"
    assert payload["risk"]["score"] is None
    assert payload["terrain"]["status"] == "unavailable"
    assert payload["terrain"]["data"] is None
    assert payload["rainfall"]["status"] == "unavailable"
    assert payload["rainfall"]["historical"] is None
    assert payload["historical_evidence"]["data"] == []


def test_village_context_returns_not_found_for_unknown_lgd_code() -> None:
    response = client.get("/api/villages/does-not-exist/context")

    assert response.status_code == 404


def test_missing_optional_event_source_does_not_hide_other_context(
    monkeypatch,
) -> None:
    missing_path = Path("missing-test-event-linkage.csv")
    village_context._read_csv.cache_clear()
    with monkeypatch.context() as patch:
        patch.setitem(
            village_context.CONTEXT_DATASETS,
            "historical_event_linkage",
            missing_path,
        )
        response = client.get("/api/villages/635099/context")

    village_context._read_csv.cache_clear()
    assert response.status_code == 200
    payload = response.json()
    assert payload["risk"]["status"] == "available"
    assert payload["terrain"]["status"] == "available"
    assert payload["rainfall"]["status"] == "available"
    assert payload["historical_evidence"]["status"] == "unavailable"
    assert payload["historical_evidence"]["data"] is None


def test_village_context_accepts_current_scenario_metadata_without_recalculation() -> None:
    response = client.get(
        "/api/villages/635099/context?scenario=heavy"
    )

    assert response.status_code == 200
    scenario = response.json()["scenario"]
    assert scenario["key"] == "heavy"
    assert scenario["multiplier"] == 1.5
    assert scenario["data_type"] == "simulated"
    assert scenario["mode"] == "scenario"


def test_custom_village_context_requires_valid_multiplier() -> None:
    assert client.get("/api/villages/635099/context?scenario=custom").status_code == 422
    response = client.get(
        "/api/villages/635099/context?scenario=custom&multiplier=1.8"
    )
    assert response.status_code == 200
    assert response.json()["scenario"]["multiplier"] == 1.8


def test_hydrology_map_endpoint_is_available_or_explicitly_unavailable() -> None:
    response = client.get("/api/hydrology")

    assert response.status_code == 200
    payload = response.json()
    assert payload["data_type"] == "derived"
    assert payload["status"] in {"available", "unavailable"}
    if payload["status"] == "available":
        assert payload["metadata"]["output_crs"] == "EPSG:32643"
        assert payload["metadata"]["valid_dem_cell_count"] > 0
        assert payload["flow_accumulation"]["raster_url"].endswith(
            "flow_accumulation.tif"
        )
        network = client.get(payload["drainage_network"]["url"])
        assert network.status_code == 200
        network = network.json()
        assert network["type"] == "FeatureCollection"
        assert all(feature["geometry"] for feature in network["features"])
        assert payload["drainage_network"]["line_count"] > 0
    else:
        assert payload["metadata"] is None
        assert payload["drainage_network"] is None


def test_village_hydrology_endpoint_returns_only_available_joined_values() -> None:
    matched = client.get("/api/villages/635099/hydrology")
    assert matched.status_code == 200
    payload = matched.json()
    assert payload["village_lgd_code"] == "635099"
    assert payload["data_type"] == "derived"
    if payload["status"] == "available":
        assert payload["data"]["valid_dem_cell_count"] > 0
        assert payload["data"]["max_flow_accumulation_cells"] >= 1
        assert payload["data"]["drainage_density_km_per_km2"] >= 0
        assert payload["water_accumulation_potential"]["value"] is None
        assert payload["water_accumulation_potential"]["status"] == "not_derived"
        assert "village_lgd_code" not in payload["data"]
    else:
        assert payload["data"] is None

    unmatched = client.get("/api/villages/932021/hydrology")
    assert unmatched.status_code == 200
    assert unmatched.json()["status"] == "unavailable"
    assert unmatched.json()["data"] is None


def test_hydrology_endpoint_rejects_unknown_village_code() -> None:
    assert client.get("/api/villages/unknown/hydrology").status_code == 404


def test_missing_hydrology_outputs_are_reported_without_calculation(
    monkeypatch, tmp_path: Path
) -> None:
    source = DATA_SOURCE_BY_ID["hydrology"]
    monkeypatch.setitem(
        DATA_SOURCE_BY_ID,
        "hydrology",
        replace(source, artifacts=(tmp_path / "not-generated.tif",)),
    )

    map_response = client.get("/api/hydrology")
    village_response = client.get("/api/villages/635099/hydrology")
    context_response = client.get("/api/villages/635099/context")

    assert map_response.status_code == 200
    assert map_response.json()["status"] == "unavailable"
    assert village_response.status_code == 200
    assert village_response.json()["status"] == "unavailable"
    assert village_response.json()["data"] is None
    assert context_response.status_code == 200
    assert context_response.json()["hydrology"]["status"] == "unavailable"
