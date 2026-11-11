"""Regression coverage for the exact-LGD village polygons displayed on the map."""

import csv
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.main import app


client = TestClient(app)
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_village_boundary_geojson_contains_all_supported_exact_id_polygons() -> None:
    with (PROJECT_ROOT / "data/processed/ml_village_susceptibility_scores.csv").open(
        encoding="utf-8-sig", newline=""
    ) as handle:
        supported_codes = {
            row["village_lgd_code"] for row in csv.DictReader(handle)
        }

    response = client.get("/api/village-boundaries")

    assert response.status_code == 200
    payload = response.json()
    features = payload["features"]
    codes = {feature["properties"]["village_lgd_code"] for feature in features}
    assert payload["type"] == "FeatureCollection"
    assert payload["classification"] == "EXACT_LGD_VALIDATED_VILLAGE_BOUNDARIES"
    assert payload["feature_count"] == 40
    assert codes == supported_codes
    assert len(codes) == len(features)
    assert all(
        feature["geometry"]["type"] in {"Polygon", "MultiPolygon"}
        and feature["geometry"]["coordinates"]
        for feature in features
    )
