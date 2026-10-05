"""Read precomputed DEM-derived hydrology outputs for API responses."""

from __future__ import annotations

import csv
import json
import logging
from functools import lru_cache
from pathlib import Path

from fastapi import HTTPException

from backend.app.data_catalog import (
    DATA,
    DATA_SOURCE_BY_ID,
    Availability,
    source_provenance,
    source_status,
)


logger = logging.getLogger(__name__)
HYDROLOGY_DIR = DATA / "processed/hydrology"
VILLAGES_PATH = DATA / "processed/nilgiris_villages.csv"


@lru_cache(maxsize=1)
def _village_codes() -> frozenset[str]:
    with VILLAGES_PATH.open("r", encoding="utf-8-sig", newline="") as handle:
        return frozenset(
            row["village_lgd_code"] for row in csv.DictReader(handle)
        )


@lru_cache(maxsize=1)
def _hydrology_metadata() -> dict[str, object]:
    with (HYDROLOGY_DIR / "hydrology_metadata.json").open(
        "r", encoding="utf-8"
    ) as handle:
        return json.load(handle)


@lru_cache(maxsize=1)
def _drainage_network() -> dict[str, object]:
    with (HYDROLOGY_DIR / "drainage_network.geojson").open(
        "r", encoding="utf-8"
    ) as handle:
        return json.load(handle)


@lru_cache(maxsize=1)
def _village_features() -> dict[str, dict[str, str]]:
    with (HYDROLOGY_DIR / "village_hydrology_features.csv").open(
        "r", encoding="utf-8-sig", newline=""
    ) as handle:
        return {
            row["village_lgd_code"]: row for row in csv.DictReader(handle)
        }


def _provenance(
    source_id: str,
    status: str,
    generated_at: str | None = None,
) -> dict[str, object]:
    value = source_provenance(source_id, generated_at=generated_at)
    value["status"] = status
    return value


def _as_float(record: dict[str, str], field: str) -> float:
    return float(record[field])


def _as_int(record: dict[str, str], field: str) -> int:
    return int(record[field])


def get_hydrology_map_data() -> dict[str, object]:
    """Return cached metadata and map overlays; never run raster processing here."""
    status = source_status(DATA_SOURCE_BY_ID["hydrology"]).value
    if status != Availability.AVAILABLE.value:
        return {
            "status": status,
            "data_type": "derived",
            "metadata": None,
            "flow_accumulation": None,
            "drainage_network": None,
            "provenance": _provenance("hydrology", status),
        }
    try:
        metadata = _hydrology_metadata()
        drainage = _drainage_network()
    except FileNotFoundError:
        logger.exception("Hydrology outputs became unavailable while loading map data")
        _hydrology_metadata.cache_clear()
        _drainage_network.cache_clear()
        return {
            "status": Availability.UNAVAILABLE.value,
            "data_type": "derived",
            "metadata": None,
            "flow_accumulation": None,
            "drainage_network": None,
            "provenance": _provenance("hydrology", Availability.UNAVAILABLE.value),
        }
    return {
        "status": Availability.AVAILABLE.value,
        "data_type": "derived",
        "metadata": metadata,
        "flow_accumulation": {
            "raster_url": "/data/processed/hydrology/flow_accumulation.tif",
            "preview_url": "/data/processed/hydrology/flow_accumulation_preview.png",
            "preview_bounds": metadata[
                "flow_accumulation_preview_bounds_south_west_north_east"
            ],
            "display": "Thresholded, log-scaled display preview; the raw raster contains unnormalized cell counts.",
            "provenance": _provenance(
                "flow_accumulation",
                Availability.AVAILABLE.value,
                str(metadata["generated_at"]),
            ),
        },
        "drainage_network": {
            "url": "/data/processed/hydrology/drainage_network.geojson",
            "preview_url": "/data/processed/hydrology/drainage_network_preview.png",
            "preview_bounds": metadata[
                "drainage_network_preview_bounds_south_west_north_east"
            ],
            "line_count": (
                drainage.get("features", [{}])[0]
                .get("properties", {})
                .get("line_count", 0)
            ),
            "provenance": _provenance(
                "drainage_network",
                Availability.AVAILABLE.value,
                str(metadata["generated_at"]),
            ),
        },
        "provenance": _provenance(
            "hydrology",
            Availability.AVAILABLE.value,
            str(metadata["generated_at"]),
        ),
    }


def get_village_hydrology(village_code: str) -> dict[str, object]:
    """Return one exact-LGD hydrology row, or an explicit unavailable response."""
    code = village_code.strip()
    if code not in _village_codes():
        raise HTTPException(status_code=404, detail="Village LGD code was not found.")

    source_id = "village_hydrology_features"
    status = source_status(DATA_SOURCE_BY_ID["hydrology"]).value
    if status != Availability.AVAILABLE.value:
        return {
            "village_lgd_code": code,
            "status": Availability.UNAVAILABLE.value,
            "data_type": "derived",
            "data": None,
            "flow_accumulation": None,
            "drainage_density": None,
            "drainage_network": None,
            "water_accumulation_potential": {
                "status": "not_derived",
                "value": None,
                "reason": (
                    "No validated SRTM-only wetness or water-accumulation category is emitted."
                ),
            },
            "provenance": _provenance(source_id, Availability.UNAVAILABLE.value),
            "limitations": (
                "Run the offline hydrology pipeline; no values are calculated in API requests."
            ),
        }
    try:
        record = _village_features().get(code)
    except FileNotFoundError:
        logger.exception("Village hydrology table became unavailable")
        _village_features.cache_clear()
        return {
            "village_lgd_code": code,
            "status": Availability.UNAVAILABLE.value,
            "data_type": "derived",
            "data": None,
            "flow_accumulation": None,
            "drainage_density": None,
            "drainage_network": None,
            "water_accumulation_potential": {
                "status": "not_derived",
                "value": None,
                "reason": "No validated SRTM-only wetness category is emitted.",
            },
            "provenance": _provenance(source_id, Availability.UNAVAILABLE.value),
            "limitations": "The processed village hydrology table is unavailable.",
        }
    if record is None:
        return {
            "village_lgd_code": code,
            "status": Availability.UNAVAILABLE.value,
            "data_type": "derived",
            "data": None,
            "flow_accumulation": None,
            "drainage_density": None,
            "drainage_network": None,
            "water_accumulation_potential": {
                "status": "not_derived",
                "value": None,
                "reason": "No validated SRTM-only wetness category is emitted.",
            },
            "provenance": _provenance(source_id, Availability.UNAVAILABLE.value),
            "limitations": (
                "No exact-LGD polygon/DEM hydrology summary exists for this village."
            ),
        }

    data = {
        "mean_flow_accumulation_cells": _as_float(
            record, "mean_flow_accumulation_cells"
        ),
        "max_flow_accumulation_cells": _as_int(
            record, "max_flow_accumulation_cells"
        ),
        "max_contributing_area_km2": _as_float(
            record, "max_contributing_area_km2"
        ),
        "high_flow_area_fraction": _as_float(record, "high_flow_area_fraction"),
        "drainage_length_km": _as_float(record, "drainage_length_km"),
        "drainage_density_km_per_km2": _as_float(
            record, "drainage_density_km_per_km2"
        ),
        "village_area_km2": _as_float(record, "village_area_km2"),
        "valid_dem_cell_count": _as_int(record, "valid_dem_cell_count"),
        "stream_threshold_cells": int(
            _hydrology_metadata()["stream_threshold_cells"]
        ),
    }
    generated_at = str(_hydrology_metadata()["generated_at"])
    return {
        "village_lgd_code": code,
        "status": Availability.AVAILABLE.value,
        "data_type": "derived",
        "data": data,
        "flow_accumulation": {
            "status": Availability.AVAILABLE.value,
            "mean_upstream_cells": data["mean_flow_accumulation_cells"],
            "maximum_upstream_cells": data["max_flow_accumulation_cells"],
            "maximum_contributing_area_km2": data["max_contributing_area_km2"],
            "high_flow_area_fraction": data["high_flow_area_fraction"],
            "stream_threshold_cells": data["stream_threshold_cells"],
            "unit": "upstream cells",
            "provenance": _provenance(
                "flow_accumulation",
                Availability.AVAILABLE.value,
                generated_at,
            ),
        },
        "drainage_density": {
            "status": Availability.AVAILABLE.value,
            "value_km_per_km2": data["drainage_density_km_per_km2"],
            "drainage_length_km": data["drainage_length_km"],
            "village_area_km2": data["village_area_km2"],
            "unit": "km/km2",
            "provenance": _provenance(
                "village_hydrology_features",
                Availability.AVAILABLE.value,
                generated_at,
            ),
        },
        "drainage_network": {
            "status": Availability.AVAILABLE.value,
            "drainage_length_km": data["drainage_length_km"],
            "provenance": _provenance(
                "drainage_network",
                Availability.AVAILABLE.value,
                generated_at,
            ),
        },
        "water_accumulation_potential": {
            "status": "not_derived",
            "value": None,
            "reason": (
                "SRTM-derived contributing area alone does not establish wetness, "
                "inundation, soil transmissivity, or observed water accumulation."
            ),
        },
        "provenance": _provenance(
            source_id, Availability.AVAILABLE.value, generated_at
        ),
        "limitations": (
            "Terrain-derived drainage metrics are not water levels, flood probabilities, "
            "or official hydrography. They are not model inputs."
        ),
    }
