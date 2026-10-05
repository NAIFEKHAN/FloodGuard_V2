"""Build DEM-derived D8 hydrology rasters and village summaries offline."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from pipeline.hydrology.processing import (
    DEFAULT_BUFFER_M,
    DEFAULT_RESOLUTION_M,
    HydrologyPipelineError,
    build_hydrology_features,
    resolve_stream_threshold,
)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Derive depression-conditioned D8 hydrology from the local Nilgiris DEM."
    )
    parser.add_argument(
        "--dem",
        type=Path,
        default=Path("data/processed/dem_nilgiris_mosaic.tif"),
    )
    parser.add_argument(
        "--villages",
        type=Path,
        default=Path("data/processed/nilgiris_villages.csv"),
    )
    parser.add_argument(
        "--kmz",
        type=Path,
        default=Path("data/raw/admin/vb_soi_tn.kmz"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/processed/hydrology"),
    )
    parser.add_argument(
        "--stream-threshold-cells",
        type=int,
        default=None,
        help=(
            "Minimum upstream D8 cells for drainage linework. Defaults to "
            "HYDROLOGY_STREAM_THRESHOLD_CELLS or 1000."
        ),
    )
    parser.add_argument("--buffer-m", type=float, default=DEFAULT_BUFFER_M)
    parser.add_argument("--resolution-m", type=float, default=DEFAULT_RESOLUTION_M)
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    try:
        threshold = resolve_stream_threshold(
            arguments.stream_threshold_cells,
            os.environ.get("HYDROLOGY_STREAM_THRESHOLD_CELLS"),
        )
        metadata = build_hydrology_features(
            arguments.dem,
            arguments.villages,
            arguments.kmz,
            arguments.output_dir,
            stream_threshold_cells=threshold,
            buffer_m=arguments.buffer_m,
            resolution_m=arguments.resolution_m,
        )
    except (HydrologyPipelineError, ValueError) as error:
        print(f"Hydrology pipeline failed: {error}")
        return 1

    print(f"Hydrology outputs written to: {arguments.output_dir}")
    print(
        f"CRS: {metadata['output_crs']}; resolution: "
        f"{metadata['resolution_m']} m; valid cells: {metadata['valid_dem_cell_count']}"
    )
    print(
        f"Drainage threshold: {metadata['stream_threshold_cells']} cells "
        f"(~{metadata['stream_threshold_contributing_area_km2']:.3f} km2)"
    )
    print(
        f"Village features: {metadata['village_hydrology_count']} matched; "
        f"{metadata['village_unmatched_count']} Village Master records unavailable"
    )
    print(f"Drainage line features: {metadata['drainage_feature_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
