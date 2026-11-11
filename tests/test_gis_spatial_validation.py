"""Evidence-based regression checks for event points and exact-LGD polygons."""

from pathlib import Path

import pandas as pd
from shapely.geometry import Point

from pipeline.spatial.event_village_audit import (
    exact_code_records,
    link_events,
    read_nilgiris_kmz,
)


ROOT = Path(__file__).resolve().parents[1]


def test_historical_event_point_in_polygon_and_saved_linkage_are_consistent() -> None:
    events = pd.read_csv(ROOT / "data/processed/landslide_events.csv")
    villages = pd.read_csv(
        ROOT / "data/processed/nilgiris_villages.csv", dtype=str
    )
    source_polygons = read_nilgiris_kmz(ROOT / "data/raw/admin/vb_soi_tn.kmz")
    exact_polygons = exact_code_records(source_polygons, villages)

    assert len(source_polygons) == 58
    assert len(exact_polygons) == 40
    assert all(
        record.geometry is not None and record.geometry.is_valid
        for record in source_polygons
    )
    assert len(events) == 772
    assert events["latitude"].between(-90, 90).all()
    assert events["longitude"].between(-180, 180).all()

    audited = link_events(events, exact_polygons)
    assert audited["spatial_match_status"].value_counts().to_dict() == {
        "NO_EXACT_POLYGON_MATCH": 423,
        "EXACT_POLYGON_MATCH": 349,
    }
    inside_any_nilgiris_polygon = [
        any(
            record.geometry.contains(Point(float(row.longitude), float(row.latitude)))
            for record in source_polygons
        )
        for row in events.itertuples()
    ]
    assert sum(inside_any_nilgiris_polygon) == 693
    assert sum(
        status == "NO_EXACT_POLYGON_MATCH" and inside_any
        for status, inside_any in zip(
            audited["spatial_match_status"], inside_any_nilgiris_polygon
        )
    ) == 344
    assert len(events) - sum(inside_any_nilgiris_polygon) == 79

    saved = pd.read_csv(
        ROOT / "data/processed/event_village_linkage.csv", dtype=str
    ).fillna("")
    assert saved["spatial_linkage_status"].tolist() == audited[
        "spatial_match_status"
    ].tolist()
    assert saved["matched_village_lgd_code"].tolist() == audited[
        "matched_village_lgd_code"
    ].tolist()
    assert saved["latitude"].astype(float).tolist() == audited["latitude"].tolist()
    assert saved["longitude"].astype(float).tolist() == audited["longitude"].tolist()
