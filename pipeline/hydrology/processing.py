"""Deterministic D8 hydrology derivation from a local elevation raster."""

from __future__ import annotations

import json
import math
import os
import tempfile
import warnings
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import rasterio
from numba import njit
from pyproj import Transformer
from rasterio.features import geometry_mask, rasterize
from rasterio.errors import NotGeoreferencedWarning
from rasterio.transform import array_bounds, from_origin
from rasterio.warp import Resampling, calculate_default_transform, reproject, transform_bounds
from shapely.geometry import LineString, mapping
from shapely.ops import transform as transform_geometry
from shapely.ops import unary_union

from pipeline.spatial.event_village_audit import exact_code_records, read_nilgiris_kmz


HYDROLOGY_CRS = "EPSG:32643"
WEB_CRS = "EPSG:4326"
DEFAULT_RESOLUTION_M = 30.0
DEFAULT_STREAM_THRESHOLD_CELLS = 1000
DEFAULT_BUFFER_M = 5000.0
NODATA_ELEVATION = -9999.0
OUTPUT_NAMES = (
    "filled_dem.tif",
    "flow_direction.tif",
    "flow_accumulation.tif",
    "flow_accumulation_preview.png",
    "drainage_network_preview.png",
    "drainage_network.geojson",
    "village_hydrology_features.csv",
    "hydrology_metadata.json",
)
VILLAGE_FEATURE_COLUMNS = (
    "village_lgd_code",
    "village_name_en",
    "taluk_lgd_code",
    "valid_dem_cell_count",
    "village_area_km2",
    "mean_flow_accumulation_cells",
    "max_flow_accumulation_cells",
    "max_contributing_area_km2",
    "high_flow_area_fraction",
    "drainage_length_km",
    "drainage_density_km_per_km2",
)

_D8_CODES = np.asarray((1, 2, 4, 8, 16, 32, 64, 128), dtype=np.uint8)
_D8_ROWS = np.asarray((0, 1, 1, 1, 0, -1, -1, -1), dtype=np.int8)
_D8_COLS = np.asarray((1, 1, 0, -1, -1, -1, 0, 1), dtype=np.int8)


class HydrologyPipelineError(ValueError):
    """Raised when DEM inputs cannot safely produce hydrology features."""


def resolve_stream_threshold(
    explicit_value: int | None,
    environment_value: str | None,
) -> int:
    """Resolve and validate the configurable contributing-cell threshold."""
    raw = (
        explicit_value
        if explicit_value is not None
        else environment_value
        if environment_value is not None
        else DEFAULT_STREAM_THRESHOLD_CELLS
    )
    try:
        threshold = int(raw)
    except (TypeError, ValueError) as error:
        raise HydrologyPipelineError(
            "Stream threshold must be an integer number of upstream cells."
        ) from error
    if threshold < 2:
        raise HydrologyPipelineError("Stream threshold must be at least two upstream cells.")
    return threshold


@njit(cache=True)
def _heap_push(heap: np.ndarray, size: int, value: int, filled: np.ndarray) -> int:
    position = size
    size += 1
    while position > 0:
        parent = (position - 1) // 2
        parent_value = heap[parent]
        if filled.flat[parent_value] < filled.flat[value] or (
            filled.flat[parent_value] == filled.flat[value] and parent_value < value
        ):
            break
        heap[position] = parent_value
        position = parent
    heap[position] = value
    return size


@njit(cache=True)
def _heap_pop(heap: np.ndarray, size: int, filled: np.ndarray) -> tuple[int, int]:
    result = heap[0]
    size -= 1
    last = heap[size]
    if size > 0:
        position = 0
        while True:
            left = position * 2 + 1
            if left >= size:
                break
            right = left + 1
            child = left
            if right < size:
                left_value = heap[left]
                right_value = heap[right]
                if filled.flat[right_value] < filled.flat[left_value] or (
                    filled.flat[right_value] == filled.flat[left_value]
                    and right_value < left_value
                ):
                    child = right
            child_value = heap[child]
            if filled.flat[last] < filled.flat[child_value] or (
                filled.flat[last] == filled.flat[child_value] and last < child_value
            ):
                break
            heap[position] = child_value
            position = child
        heap[position] = last
    return result, size


@njit(cache=True)
def priority_flood_fill(
    elevation: np.ndarray, valid: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Fill depressions and retain a deterministic parent direction for flats."""
    rows, cols = elevation.shape
    count = elevation.size
    filled = elevation.copy()
    visited = np.zeros((rows, cols), dtype=np.uint8)
    parent_direction = np.zeros((rows, cols), dtype=np.uint8)
    heap = np.empty(count, dtype=np.int32)
    heap_size = 0

    for row in range(rows):
        for col in range(cols):
            if not valid[row, col]:
                continue
            is_outlet = row == 0 or col == 0 or row == rows - 1 or col == cols - 1
            if not is_outlet:
                for direction in range(8):
                    next_row = row + _D8_ROWS[direction]
                    next_col = col + _D8_COLS[direction]
                    if not valid[next_row, next_col]:
                        is_outlet = True
                        break
            if is_outlet:
                visited[row, col] = 1
                heap_size = _heap_push(heap, heap_size, row * cols + col, filled)

    if heap_size == 0:
        raise ValueError("The DEM contains no valid hydrology outlet cells.")

    while heap_size > 0:
        current, heap_size = _heap_pop(heap, heap_size, filled)
        row = current // cols
        col = current % cols
        for direction in range(8):
            next_row = row + _D8_ROWS[direction]
            next_col = col + _D8_COLS[direction]
            if (
                next_row < 0
                or next_row >= rows
                or next_col < 0
                or next_col >= cols
                or not valid[next_row, next_col]
                or visited[next_row, next_col] != 0
            ):
                continue
            visited[next_row, next_col] = 1
            if filled[next_row, next_col] < filled[row, col]:
                filled[next_row, next_col] = filled[row, col]
            parent_direction[next_row, next_col] = _D8_CODES[(direction + 4) % 8]
            heap_size = _heap_push(
                heap,
                heap_size,
                next_row * cols + next_col,
                filled,
            )
    return filled, parent_direction


@njit(cache=True)
def d8_flow_direction(
    filled: np.ndarray, valid: np.ndarray, parent_direction: np.ndarray, resolution: float
) -> np.ndarray:
    """Choose the steepest downslope neighbor; route exact flats by flood parents."""
    rows, cols = filled.shape
    result = np.full((rows, cols), 255, dtype=np.uint8)
    diagonal = math.sqrt(2.0)
    for row in range(rows):
        for col in range(cols):
            if not valid[row, col]:
                continue
            if parent_direction[row, col] == 0:
                result[row, col] = 0
                continue
            current = filled[row, col]
            best_slope = 0.0
            best_code = 0
            for direction in range(8):
                next_row = row + _D8_ROWS[direction]
                next_col = col + _D8_COLS[direction]
                if (
                    next_row < 0
                    or next_row >= rows
                    or next_col < 0
                    or next_col >= cols
                    or not valid[next_row, next_col]
                ):
                    continue
                drop = current - filled[next_row, next_col]
                if drop <= 0:
                    continue
                distance = resolution * (diagonal if direction % 2 == 1 else 1.0)
                gradient = drop / distance
                if gradient > best_slope:
                    best_slope = gradient
                    best_code = _D8_CODES[direction]
            if best_code == 0:
                best_code = parent_direction[row, col]
            result[row, col] = best_code
    return result


@njit(cache=True)
def flow_accumulation_d8(
    directions: np.ndarray, valid: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray, int]:
    """Accumulate upstream cell counts on the acyclic D8 flow graph."""
    rows, cols = directions.shape
    count = directions.size
    downstream = np.full(count, -1, dtype=np.int32)
    indegree = np.zeros(count, dtype=np.uint8)
    total_valid = 0
    for row in range(rows):
        for col in range(cols):
            if not valid[row, col]:
                continue
            total_valid += 1
            code = directions[row, col]
            if code == 0 or code == 255:
                continue
            for direction in range(8):
                if code != _D8_CODES[direction]:
                    continue
                next_row = row + _D8_ROWS[direction]
                next_col = col + _D8_COLS[direction]
                if (
                    0 <= next_row < rows
                    and 0 <= next_col < cols
                    and valid[next_row, next_col]
                ):
                    target = next_row * cols + next_col
                    downstream[row * cols + col] = target
                    indegree[target] += 1
                break

    initial_indegree = indegree.copy()
    accumulation = np.zeros(count, dtype=np.uint32)
    queue = np.empty(total_valid, dtype=np.int32)
    tail = 0
    for index in range(count):
        if valid.flat[index]:
            accumulation[index] = 1
            if indegree[index] == 0:
                queue[tail] = index
                tail += 1

    head = 0
    processed = 0
    while head < tail:
        source = queue[head]
        head += 1
        processed += 1
        target = downstream[source]
        if target >= 0:
            accumulation[target] += accumulation[source]
            indegree[target] -= 1
            if indegree[target] == 0:
                queue[tail] = target
                tail += 1
    if processed != total_valid:
        raise ValueError("D8 flow graph contains a cycle or a disconnected valid cell.")
    return accumulation.reshape((rows, cols)), downstream, initial_indegree, total_valid


def _read_village_inputs(villages_path: Path, kmz_path: Path):
    villages = pd.read_csv(villages_path, dtype=str)
    required = (
        "village_lgd_code",
        "village_name_en",
        "district_lgd_code",
        "taluk_lgd_code",
    )
    missing = [column for column in required if column not in villages.columns]
    if missing or villages.empty:
        raise HydrologyPipelineError(
            "Village table is empty or missing required columns: " + ", ".join(missing)
        )
    if villages["village_lgd_code"].duplicated().any():
        raise HydrologyPipelineError("Village table contains duplicate LGD codes.")

    boundaries = read_nilgiris_kmz(kmz_path)
    valid_boundaries = [
        record for record in boundaries
        if record.geometry is not None and record.geometry.is_valid
    ]
    if not valid_boundaries:
        raise HydrologyPipelineError("No valid Nilgiris boundary geometries were found.")
    exact_records = exact_code_records(valid_boundaries, villages)
    if not exact_records:
        raise HydrologyPipelineError("No exact village LGD-code boundary matches were found.")
    return villages, valid_boundaries, exact_records


def _write_raster(
    path: Path,
    values: np.ndarray,
    transform,
    *,
    crs: str,
    nodata: int | float,
    dtype: str,
    compression: str = "deflate",
) -> None:
    profile: dict[str, Any] = {
        "driver": "GTiff",
        "height": values.shape[0],
        "width": values.shape[1],
        "count": 1,
        "dtype": dtype,
        "crs": crs,
        "transform": transform,
        "nodata": nodata,
        "compress": compression,
        "predictor": 3 if dtype.startswith("float") else 2,
        "tiled": True,
        "blockxsize": 256,
        "blockysize": 256,
    }
    with rasterio.open(path, "w", **profile) as destination:
        destination.write(values.astype(dtype, copy=False), 1)
        destination.update_tags(
            AREA_OR_POINT="Area",
            DERIVATION="FloodGuard DEM-derived hydrology; see hydrology_metadata.json",
        )


def _write_accumulation_preview(
    path: Path,
    accumulation: np.ndarray,
    transform,
    threshold_cells: int,
) -> tuple[float, float, float, float]:
    """Write a transparent, log-scaled WGS84 PNG; raw counts remain in the GeoTIFF."""
    bounds = array_bounds(accumulation.shape[0], accumulation.shape[1], transform)
    preview_transform, width, height = calculate_default_transform(
        HYDROLOGY_CRS,
        WEB_CRS,
        accumulation.shape[1],
        accumulation.shape[0],
        *bounds,
    )
    preview_counts = np.zeros((height, width), dtype=np.uint32)
    reproject(
        source=accumulation,
        destination=preview_counts,
        src_transform=transform,
        src_crs=HYDROLOGY_CRS,
        src_nodata=0,
        dst_transform=preview_transform,
        dst_crs=WEB_CRS,
        dst_nodata=0,
        resampling=Resampling.nearest,
    )
    above_threshold = preview_counts >= threshold_cells
    rgba = np.zeros((4, height, width), dtype=np.uint8)
    if above_threshold.any():
        upper = max(
            float(np.percentile(np.log10(preview_counts[above_threshold]), 99.5)),
            math.log10(threshold_cells) + 1e-6,
        )
        scaled = np.clip(
            (np.log10(np.maximum(preview_counts, threshold_cells)) - math.log10(threshold_cells))
            / (upper - math.log10(threshold_cells)),
            0.0,
            1.0,
        )
        rgba[0, above_threshold] = 2
        rgba[1, above_threshold] = 132
        rgba[2, above_threshold] = 199
        rgba[3, above_threshold] = (
            75 + 165 * scaled[above_threshold]
        ).astype(np.uint8)
    # The PNG's WGS84 bounds are carried alongside the URL in hydrology_metadata.json.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", NotGeoreferencedWarning)
        with rasterio.open(
            path,
            "w",
            driver="PNG",
            width=width,
            height=height,
            count=4,
            dtype="uint8",
        ) as destination:
            destination.write(rgba)
    preview_bounds = array_bounds(height, width, preview_transform)
    return tuple(float(value) for value in preview_bounds)


def _write_drainage_preview(
    path: Path,
    accumulation: np.ndarray,
    transform,
    threshold_cells: int,
) -> tuple[float, float, float, float]:
    """Render the thresholded D8 channel cells as a lightweight transparent overlay."""
    bounds = array_bounds(accumulation.shape[0], accumulation.shape[1], transform)
    preview_transform, width, height = calculate_default_transform(
        HYDROLOGY_CRS,
        WEB_CRS,
        accumulation.shape[1],
        accumulation.shape[0],
        *bounds,
    )
    preview_counts = np.zeros((height, width), dtype=np.uint32)
    reproject(
        source=accumulation,
        destination=preview_counts,
        src_transform=transform,
        src_crs=HYDROLOGY_CRS,
        src_nodata=0,
        dst_transform=preview_transform,
        dst_crs=WEB_CRS,
        dst_nodata=0,
        resampling=Resampling.nearest,
    )
    channel = preview_counts >= threshold_cells
    rgba = np.zeros((4, height, width), dtype=np.uint8)
    rgba[0, channel] = 7
    rgba[1, channel] = 89
    rgba[2, channel] = 65
    rgba[3, channel] = 205
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", NotGeoreferencedWarning)
        with rasterio.open(
            path,
            "w",
            driver="PNG",
            width=width,
            height=height,
            count=4,
            dtype="uint8",
        ) as destination:
            destination.write(rgba)
    preview_bounds = array_bounds(height, width, preview_transform)
    return tuple(float(value) for value in preview_bounds)


def _accumulation_geojson(
    accumulation: np.ndarray,
    valid: np.ndarray,
    downstream: np.ndarray,
    upstream_count: np.ndarray,
    transform,
    threshold_cells: int,
    stream_features_path: Path,
) -> int:
    rows, cols = accumulation.shape
    stream = valid & (accumulation >= threshold_cells)
    upstream = upstream_count.reshape((rows, cols))
    downstream_2d = downstream.reshape((rows, cols))
    starts = np.flatnonzero(stream & (upstream != 1))
    visited = np.zeros(accumulation.size, dtype=np.uint8)
    to_web = Transformer.from_crs(HYDROLOGY_CRS, WEB_CRS, always_xy=True).transform
    lines: list[list[tuple[float, float]]] = []
    maximum_upstream_cells = 0

    for start in starts:
        start = int(start)
        if visited[start]:
            continue
        path_indices: list[int] = []
        current = start
        while current >= 0 and stream.flat[current] and not visited[current]:
            visited[current] = 1
            path_indices.append(current)
            next_index = int(downstream_2d.flat[current])
            if (
                next_index < 0
                or not stream.flat[next_index]
                or upstream.flat[next_index] != 1
            ):
                if next_index >= 0 and stream.flat[next_index]:
                    path_indices.append(next_index)
                break
            current = next_index
        if len(path_indices) < 2:
            continue
        xy = []
        for index in path_indices:
            row, col = divmod(index, cols)
            x = transform.c + (col + 0.5) * transform.a
            y = transform.f + (row + 0.5) * transform.e
            longitude, latitude = to_web(x, y)
            xy.append((longitude, latitude))
        line = LineString(xy)
        if not line.is_valid or line.length == 0:
            continue
        projected_xy = []
        for index in path_indices:
            row, col = divmod(index, cols)
            projected_xy.append(
                (
                    transform.c + (col + 0.5) * transform.a,
                    transform.f + (row + 0.5) * transform.e,
                )
            )
        simplified_projected = LineString(projected_xy).simplify(
            15.0, preserve_topology=False
        )
        simplified_web = transform_geometry(to_web, simplified_projected)
        coordinates = [
            (round(float(x), 5), round(float(y), 5))
            for x, y in simplified_web.coords
        ]
        if len(coordinates) >= 2:
            lines.append(coordinates)
            maximum_upstream_cells = max(
                maximum_upstream_cells,
                int(max(accumulation.flat[index] for index in path_indices)),
            )

    collection = {
        "type": "FeatureCollection",
        "name": "D8 drainage network derived from SRTM",
        "features": (
            [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "MultiLineString",
                        "coordinates": lines,
                    },
                    "properties": {
                        "line_count": len(lines),
                        "max_upstream_cells": maximum_upstream_cells,
                        "threshold_cells": threshold_cells,
                    },
                }
            ]
            if lines
            else []
        ),
    }
    stream_features_path.write_text(
        json.dumps(collection, separators=(",", ":")),
        encoding="utf-8",
    )
    return len(lines)


def _village_features(
    villages: pd.DataFrame,
    exact_records,
    accumulation: np.ndarray,
    stream: np.ndarray,
    downstream: np.ndarray,
    valid: np.ndarray,
    transform,
    threshold_cells: int,
    resolution_m: float,
) -> pd.DataFrame:
    rows, cols = accumulation.shape
    by_code = villages.set_index("village_lgd_code")
    records: list[dict[str, object]] = []
    for boundary in sorted(
        exact_records, key=lambda item: item.attributes["vlcode"]
    ):
        code = boundary.attributes["vlcode"]
        geometry = transform_geometry(
            Transformer.from_crs(WEB_CRS, HYDROLOGY_CRS, always_xy=True).transform,
            boundary.geometry,
        )
        inside = geometry_mask(
            [mapping(geometry)],
            out_shape=(rows, cols),
            transform=transform,
            invert=True,
            all_touched=False,
        )
        selected = inside & valid
        if not selected.any():
            continue
        values = accumulation[selected]
        cell_count = int(selected.sum())
        drainage_length_m = 0.0
        for index in np.flatnonzero(selected & stream):
            target = int(downstream[int(index)])
            if target < 0:
                continue
            row, col = divmod(int(index), cols)
            target_row, target_col = divmod(target, cols)
            drainage_length_m += resolution_m * (
                math.sqrt(2.0)
                if row != target_row and col != target_col
                else 1.0
            )
        village = by_code.loc[code]
        village_area_km2 = cell_count * resolution_m**2 / 1_000_000.0
        drainage_length_km = drainage_length_m / 1000.0
        records.append(
            {
                "village_lgd_code": code,
                "village_name_en": village["village_name_en"],
                "taluk_lgd_code": village["taluk_lgd_code"],
                "valid_dem_cell_count": cell_count,
                "village_area_km2": village_area_km2,
                "mean_flow_accumulation_cells": float(values.mean()),
                "max_flow_accumulation_cells": int(values.max()),
                "max_contributing_area_km2": float(values.max())
                * resolution_m**2
                / 1_000_000.0,
                "high_flow_area_fraction": float(
                    np.count_nonzero(values >= threshold_cells) / cell_count
                ),
                "drainage_length_km": drainage_length_km,
                "drainage_density_km_per_km2": drainage_length_km
                / village_area_km2,
            }
        )
    return pd.DataFrame(records, columns=VILLAGE_FEATURE_COLUMNS)


def build_hydrology_features(
    dem_path: Path,
    villages_path: Path,
    kmz_path: Path,
    output_dir: Path,
    *,
    stream_threshold_cells: int = DEFAULT_STREAM_THRESHOLD_CELLS,
    buffer_m: float = DEFAULT_BUFFER_M,
    resolution_m: float = DEFAULT_RESOLUTION_M,
) -> dict[str, object]:
    """Condition the buffered Nilgiris DEM and derive D8, streams, and village metrics."""
    if stream_threshold_cells < 2:
        raise HydrologyPipelineError("Stream threshold must be at least two upstream cells.")
    if buffer_m <= 0 or resolution_m <= 0:
        raise HydrologyPipelineError("Processing buffer and DEM resolution must be positive.")
    if not dem_path.is_file():
        raise HydrologyPipelineError(f"DEM file was not found: {dem_path}")
    try:
        villages, boundaries, exact_records = _read_village_inputs(villages_path, kmz_path)
        study_geometry = unary_union([record.geometry for record in boundaries])
        project = Transformer.from_crs(WEB_CRS, HYDROLOGY_CRS, always_xy=True).transform
        analysis_geometry = transform_geometry(project, study_geometry).buffer(buffer_m)
        min_x, min_y, max_x, max_y = analysis_geometry.bounds
        left = math.floor(min_x / resolution_m) * resolution_m
        bottom = math.floor(min_y / resolution_m) * resolution_m
        right = math.ceil(max_x / resolution_m) * resolution_m
        top = math.ceil(max_y / resolution_m) * resolution_m
        width = int(round((right - left) / resolution_m))
        height = int(round((top - bottom) / resolution_m))
        transform = from_origin(left, top, resolution_m, resolution_m)

        elevation = np.full((height, width), NODATA_ELEVATION, dtype=np.float32)
        with rasterio.open(dem_path) as source:
            if source.crs is None or source.count != 1:
                raise HydrologyPipelineError("Input DEM must have one band and a declared CRS.")
            input_dem_crs = source.crs.to_string()
            source_bounds = transform_bounds(
                HYDROLOGY_CRS, source.crs, left, bottom, right, top, densify_pts=21
            )
            if (
                source_bounds[0] < source.bounds.left
                or source_bounds[1] < source.bounds.bottom
                or source_bounds[2] > source.bounds.right
                or source_bounds[3] > source.bounds.top
            ):
                raise HydrologyPipelineError(
                    "Buffered Nilgiris study extent is not fully covered by the supplied DEM."
                )
            reproject(
                source=rasterio.band(source, 1),
                destination=elevation,
                src_transform=source.transform,
                src_crs=source.crs,
                src_nodata=source.nodata,
                dst_transform=transform,
                dst_crs=HYDROLOGY_CRS,
                dst_nodata=NODATA_ELEVATION,
                resampling=Resampling.bilinear,
            )

        analysis_mask = rasterize(
            [(mapping(analysis_geometry), 1)],
            out_shape=(height, width),
            transform=transform,
            fill=0,
            dtype="uint8",
            all_touched=False,
        ).astype(bool)
        valid = analysis_mask & np.isfinite(elevation) & (elevation != NODATA_ELEVATION)
        if int(valid.sum()) < 100:
            raise HydrologyPipelineError("Buffered study area has insufficient valid DEM coverage.")

        try:
            filled, parent_direction = priority_flood_fill(elevation, valid)
            directions = d8_flow_direction(
                filled, valid, parent_direction, resolution_m
            )
            accumulation, downstream, upstream_count, valid_count = flow_accumulation_d8(
                directions, valid
            )
        except ValueError as error:
            raise HydrologyPipelineError(f"Hydrology routing failed: {error}") from error

        stream = valid & (accumulation >= stream_threshold_cells)
        exact_code_set = {
            record.attributes["vlcode"] for record in exact_records
        }
        matched_codes = villages["village_lgd_code"].isin(exact_code_set)
        village_features = _village_features(
            villages,
            exact_records,
            accumulation,
            stream,
            downstream,
            valid,
            transform,
            stream_threshold_cells,
            resolution_m,
        )
        if village_features.empty or village_features["village_lgd_code"].duplicated().any():
            raise HydrologyPipelineError("Village hydrology aggregation produced no unique rows.")

        output_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".hydrology-", dir=output_dir.parent) as temp_name:
            stage = Path(temp_name)
            _write_raster(
                stage / "filled_dem.tif",
                np.where(valid, filled, NODATA_ELEVATION),
                transform,
                crs=HYDROLOGY_CRS,
                nodata=NODATA_ELEVATION,
                dtype="float32",
            )
            _write_raster(
                stage / "flow_direction.tif",
                directions,
                transform,
                crs=HYDROLOGY_CRS,
                nodata=255,
                dtype="uint8",
            )
            _write_raster(
                stage / "flow_accumulation.tif",
                np.where(valid, accumulation, 0),
                transform,
                crs=HYDROLOGY_CRS,
                nodata=0,
                dtype="uint32",
            )
            stream_count = _accumulation_geojson(
                accumulation,
                valid,
                downstream,
                upstream_count,
                transform,
                stream_threshold_cells,
                stage / "drainage_network.geojson",
            )
            preview_bounds = _write_accumulation_preview(
                stage / "flow_accumulation_preview.png",
                accumulation,
                transform,
                stream_threshold_cells,
            )
            drainage_preview_bounds = _write_drainage_preview(
                stage / "drainage_network_preview.png",
                accumulation,
                transform,
                stream_threshold_cells,
            )
            village_features.to_csv(
                stage / "village_hydrology_features.csv", index=False
            )
            valid_elevations = elevation[valid]
            metadata: dict[str, object] = {
                "algorithm": "D8 steepest-descent flow routing with Priority-Flood depression conditioning",
                "input_dem": dem_path.as_posix(),
                "input_dem_crs": input_dem_crs,
                "output_crs": HYDROLOGY_CRS,
                "resolution_m": resolution_m,
                "analysis_extent": "Union of supplied Nilgiris KMZ village boundaries buffered outward; rectangular raster clipped to the buffered envelope",
                "processing_buffer_m": buffer_m,
                "valid_dem_cell_count": valid_count,
                "nodata_cell_count": int(valid.size - valid.sum()),
                "nodata_explanation": (
                    "Cells outside the 5 km buffered union of the supplied boundary polygons "
                    "are intentionally NoData; remaining NoData is inherited from the source DEM."
                ),
                "elevation_min_m": float(valid_elevations.min()),
                "elevation_max_m": float(valid_elevations.max()),
                "stream_threshold_cells": stream_threshold_cells,
                "stream_threshold_contributing_area_km2": (
                    stream_threshold_cells * resolution_m**2 / 1_000_000.0
                ),
                "stream_cell_count": int(stream.sum()),
                "drainage_feature_count": stream_count,
                "drainage_line_count": stream_count,
                "flow_accumulation_preview_bounds_south_west_north_east": [
                    preview_bounds[1],
                    preview_bounds[0],
                    preview_bounds[3],
                    preview_bounds[2],
                ],
                "drainage_network_preview_bounds_south_west_north_east": [
                    drainage_preview_bounds[1],
                    drainage_preview_bounds[0],
                    drainage_preview_bounds[3],
                    drainage_preview_bounds[2],
                ],
                "village_master_count": int(len(villages)),
                "kmz_boundary_count": int(len(boundaries)),
                "exact_lgd_boundary_match_count": int(matched_codes.sum()),
                "village_hydrology_count": int(len(village_features)),
                "village_unmatched_count": int(len(villages) - len(village_features)),
                "flow_direction_encoding": {
                    "1": "east",
                    "2": "southeast",
                    "4": "south",
                    "8": "southwest",
                    "16": "west",
                    "32": "northwest",
                    "64": "north",
                    "128": "northeast",
                    "0": "outlet",
                    "255": "NoData",
                },
                "flow_accumulation": "Unnormalized upstream contributing-cell count including the cell itself",
                "conditioning": "Priority-Flood raises only cells below the lowest spill path; valid cells adjacent to the raster or NoData boundary are outlets. Filled elevations are kept separate from the source DEM.",
                "stream_threshold_rationale": (
                    "Default 1000 upstream cells is approximately 0.9 km2 at 30 m resolution, "
                    "a transparent initial channel-initiation area for map-scale headwater linework; "
                    "it is configurable and is not an official hydrography threshold."
                ),
                "stream_threshold_environment_variable": "HYDROLOGY_STREAM_THRESHOLD_CELLS",
                "wetness_proxy": None,
                "wetness_proxy_limitation": (
                    "No TWI or water-accumulation category is emitted: the available SRTM-only "
                    "inputs do not establish soil transmissivity, rainfall, or a validated flat-slope "
                    "floor, so those categories would imply unsupported wetness or flood conditions."
                ),
                "generated_at": datetime.now(UTC).isoformat(),
            }
            (stage / "hydrology_metadata.json").write_text(
                json.dumps(metadata, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            for name in OUTPUT_NAMES:
                os.replace(stage / name, output_dir / name)
        return metadata
    except HydrologyPipelineError:
        raise
    except (OSError, rasterio.errors.RasterioError, ValueError) as error:
        raise HydrologyPipelineError(f"Hydrology processing failed: {error}") from error
