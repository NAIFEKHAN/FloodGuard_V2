from pathlib import Path
import struct

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box

from pipeline.hydrology.processing import (
    _village_features,
    d8_flow_direction,
    flow_accumulation_d8,
    priority_flood_fill,
    resolve_stream_threshold,
)


def test_priority_flood_fills_a_depression_and_d8_accumulation_is_valid() -> None:
    elevation = np.full((7, 7), 10.0, dtype=np.float32)
    elevation[1:6, 1:6] = 5.0
    elevation[3, 3] = 0.0
    elevation[0, 3] = 1.0
    valid = np.ones(elevation.shape, dtype=bool)

    filled, parent = priority_flood_fill(elevation, valid)
    directions = d8_flow_direction(filled, valid, parent, 30.0)
    accumulation, _, _, valid_count = flow_accumulation_d8(directions, valid)

    assert filled[3, 3] >= 5.0
    assert np.array_equal(filled[[0, -1], :], elevation[[0, -1], :])
    assert np.all((directions >= 0) & (directions <= 255))
    assert np.all(accumulation[valid] >= 1)
    assert valid_count == valid.size


def test_d8_accumulation_counts_upstream_cells_without_normalizing() -> None:
    elevation = np.tile(np.arange(7, 0, -1, dtype=np.float32), (7, 1))
    valid = np.ones(elevation.shape, dtype=bool)
    filled, parent = priority_flood_fill(elevation, valid)
    directions = d8_flow_direction(filled, valid, parent, 30.0)
    accumulation, _, _, _ = flow_accumulation_d8(directions, valid)

    assert np.array_equal(
        accumulation[1:-1, -1],
        np.full(5, 6, dtype=np.uint32),
    )
    assert np.all(directions[[0, -1], :] == 0)
    assert int(accumulation.max()) == 6


def test_threshold_environment_override_and_validation() -> None:
    assert resolve_stream_threshold(None, "128") == 128
    assert resolve_stream_threshold(256, "128") == 256
    assert resolve_stream_threshold(None, None) == 1000
    try:
        resolve_stream_threshold(None, "invalid")
    except ValueError as error:
        assert "integer" in str(error)
    else:
        raise AssertionError("Invalid threshold must be rejected.")


def test_village_hydrology_features_use_lgd_code_and_only_raster_metrics() -> None:
    transformer = gpd.GeoSeries(
        [box(700000, 1249850, 700150, 1250000)],
        crs="EPSG:32643",
    ).to_crs("EPSG:4326")
    boundary = type(
        "Boundary",
        (),
        {
            "attributes": {"vlcode": "635099"},
            "geometry": transformer.iloc[0],
        },
    )()
    villages = pd.DataFrame(
        [
            {
                "village_lgd_code": "635099",
                "village_name_en": "Test",
                "taluk_lgd_code": "5755",
            }
        ]
    )
    elevation = np.tile(np.arange(5, 0, -1, dtype=np.float32), (5, 1))
    valid = np.ones(elevation.shape, dtype=bool)
    filled, parent = priority_flood_fill(elevation, valid)
    directions = d8_flow_direction(filled, valid, parent, 30.0)
    accumulation, downstream, _, _ = flow_accumulation_d8(directions, valid)
    features = _village_features(
        villages,
        [boundary],
        accumulation,
        accumulation >= 2,
        downstream,
        valid,
        from_origin(700000, 1250000, 30, 30),
        threshold_cells=2,
        resolution_m=30,
    )

    assert "village_lgd_code" in features.columns
    assert features.loc[0, "village_lgd_code"] == "635099"
    assert features.loc[0, "valid_dem_cell_count"] == 25
    assert features.loc[0, "max_flow_accumulation_cells"] >= 1
    assert 0 <= features.loc[0, "high_flow_area_fraction"] <= 1
    assert features.loc[0, "drainage_density_km_per_km2"] >= 0


def test_log_display_preview_returns_bounds_and_does_not_replace_raw_counts(
    tmp_path: Path,
) -> None:
    from pipeline.hydrology.processing import _write_accumulation_preview

    counts = np.tile(np.arange(1, 6, dtype=np.uint32), (5, 1))
    valid = np.ones(counts.shape, dtype=bool)
    transform = from_origin(700000, 1250000, 30, 30)
    output = tmp_path / "preview.png"

    bounds = _write_accumulation_preview(
        output,
        counts,
        transform,
        threshold_cells=2,
    )

    header = output.read_bytes()[:26]
    width, height = struct.unpack(">II", header[16:24])
    assert header[:8] == b"\x89PNG\r\n\x1a\n"
    assert header[24:26] == b"\x08\x06"
    assert width > 0 and height > 0
    assert len(bounds) == 4
    assert np.array_equal(counts, np.tile(np.arange(1, 6), (5, 1)))
