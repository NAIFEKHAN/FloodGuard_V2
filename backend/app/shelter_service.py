"""Source-faithful shelter ingestion, coordinate checks, and GIS coverage."""

from __future__ import annotations

import csv
import logging
import zipfile
import xml.etree.ElementTree as ET
from functools import lru_cache
from pathlib import Path
from typing import Any

from shapely.geometry import MultiPolygon, Point, Polygon
from shapely.ops import unary_union

from backend.app.data_catalog import DATA


logger = logging.getLogger(__name__)
SHELTER_PATH = DATA / "raw/shelters/shelters.csv"
VILLAGE_PATH = DATA / "processed/nilgiris_villages.csv"
BOUNDARY_PATH = DATA / "raw/admin/vb_soi_tn.kmz"
VERIFICATION_STATUSES = {"verified", "unverified", "needs_review"}
OPERATIONAL_STATUSES = {"operational", "not_operational", "unknown"}


@lru_cache(maxsize=1)
def _village_records() -> dict[str, dict[str, str]]:
    with VILLAGE_PATH.open("r", encoding="utf-8-sig", newline="") as handle:
        return {
            row["village_lgd_code"]: row
            for row in csv.DictReader(handle)
            if row.get("village_lgd_code")
        }


def _tag(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _coordinate_ring(raw: str) -> list[tuple[float, float]]:
    coordinates: list[tuple[float, float]] = []
    for item in raw.split():
        values = item.split(",")
        if len(values) < 2:
            continue
        longitude, latitude = float(values[0]), float(values[1])
        coordinates.append((longitude, latitude))
    return coordinates


def _placemark_metadata(placemark: ET.Element) -> dict[str, str]:
    values: dict[str, str] = {}
    for child in placemark.iter():
        if _tag(child) in {"Data", "SimpleData"} and child.get("name"):
            value = next(
                (node.text or "" for node in child if _tag(node) == "value"),
                child.text or "",
            )
            values[child.get("name", "").strip()] = value.strip()
    return values


def _placemark_geometry(placemark: ET.Element):
    polygons = []
    for polygon_node in placemark.iter():
        if _tag(polygon_node) != "Polygon":
            continue
        shell: list[tuple[float, float]] = []
        holes: list[list[tuple[float, float]]] = []
        for boundary in polygon_node:
            if _tag(boundary) not in {"outerBoundaryIs", "innerBoundaryIs"}:
                continue
            ring = next(
                (
                    _coordinate_ring(node.text or "")
                    for node in boundary.iter()
                    if _tag(node) == "coordinates"
                ),
                [],
            )
            if len(ring) < 4:
                continue
            if _tag(boundary) == "outerBoundaryIs":
                shell = ring
            else:
                holes.append(ring)
        if len(shell) >= 4:
            geometry = Polygon(shell, holes)
            if not geometry.is_valid:
                logger.warning("Ignoring invalid source village polygon in KMZ.")
                continue
            polygons.append(geometry)
    if not polygons:
        return None
    result = unary_union(polygons)
    return result if result.is_valid and not result.is_empty else None


@lru_cache(maxsize=1)
def village_geometries() -> dict[str, Any]:
    """Load only exact-LGD village polygons from the supplied KMZ."""
    known_codes = set(_village_records())
    geometries: dict[str, list[Any]] = {}
    try:
        with zipfile.ZipFile(BOUNDARY_PATH) as archive:
            kml_name = next(
                name for name in archive.namelist() if name.lower().endswith(".kml")
            )
            with archive.open(kml_name) as stream:
                for _, placemark in ET.iterparse(stream, events=("end",)):
                    if _tag(placemark) != "Placemark":
                        continue
                    code = _placemark_metadata(placemark).get("vlcode")
                    if code in known_codes:
                        geometry = _placemark_geometry(placemark)
                        if geometry is not None:
                            geometries.setdefault(code, []).append(geometry)
                    placemark.clear()
    except (OSError, zipfile.BadZipFile, StopIteration, ET.ParseError):
        logger.exception("Could not read exact-LGD village boundaries for shelter validation.")
        raise
    return {
        code: unary_union(items)
        for code, items in geometries.items()
        if items
    }


@lru_cache(maxsize=1)
def study_extent() -> tuple[float, float, float, float] | None:
    geometries = village_geometries()
    if not geometries:
        return None
    extent = unary_union(list(geometries.values()))
    return tuple(float(value) for value in extent.bounds)


def validate_coordinates(
    latitude: float | None,
    longitude: float | None,
    *,
    extent: tuple[float, float, float, float] | None = None,
) -> dict[str, Any]:
    if latitude is None or longitude is None:
        return {
            "status": "missing",
            "valid": False,
            "in_study_extent": None,
            "reason": "Coordinates are not supplied.",
        }
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return {
            "status": "invalid",
            "valid": False,
            "in_study_extent": False,
            "reason": "Coordinates are outside WGS84 latitude/longitude bounds.",
        }
    bounds = extent if extent is not None else study_extent()
    if bounds is None:
        return {
            "status": "needs_review",
            "valid": True,
            "in_study_extent": None,
            "reason": "Study extent is unavailable; location requires manual review.",
        }
    min_lon, min_lat, max_lon, max_lat = bounds
    in_extent = min_lon <= longitude <= max_lon and min_lat <= latitude <= max_lat
    return {
        "status": "valid" if in_extent else "outside_study_extent",
        "valid": True,
        "in_study_extent": in_extent,
        "reason": None if in_extent else "Coordinates are outside the supplied study extent.",
    }


def _float_or_none(value: str | None) -> float | None:
    if value is None or not value.strip():
        return None
    return float(value)


def _int_or_none(value: str | None) -> int | None:
    if value is None or not value.strip():
        return None
    return int(value)


@lru_cache(maxsize=1)
def get_shelter_records() -> tuple[dict[str, Any], ...]:
    if not SHELTER_PATH.exists():
        logger.error("Shelter ingestion file is missing: %s", SHELTER_PATH)
        raise FileNotFoundError(f"Shelter source file not found: {SHELTER_PATH}")
    with SHELTER_PATH.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {
            "shelter_id", "name", "type", "village_lgd_code", "taluk",
            "latitude", "longitude", "address", "capacity", "contact",
            "source", "last_updated", "verification_status", "verified_at",
            "operational_status",
        }
        if not required.issubset(reader.fieldnames or []):
            raise RuntimeError("Shelter CSV is missing required standardized fields.")
        result = []
        for row in reader:
            shelter_id = (row.get("shelter_id") or "").strip()
            if not shelter_id:
                raise RuntimeError("Shelter record is missing shelter_id.")
            verification_status = (
                (row.get("verification_status") or "needs_review").strip().lower()
            )
            if verification_status not in VERIFICATION_STATUSES:
                raise RuntimeError(
                    f"Shelter {shelter_id} has an invalid verification status."
                )
            operational_status = (
                (row.get("operational_status") or "unknown").strip().lower()
            )
            if operational_status not in OPERATIONAL_STATUSES:
                raise RuntimeError(
                    f"Shelter {shelter_id} has an invalid operational status."
                )
            latitude = _float_or_none(row.get("latitude"))
            longitude = _float_or_none(row.get("longitude"))
            coordinate_check = validate_coordinates(latitude, longitude)
            source = (row.get("source") or "").strip() or None
            verified_at = (row.get("verified_at") or "").strip() or None
            if (
                verification_status == "verified"
                and (not source or not verified_at or not coordinate_check["valid"]
                     or coordinate_check["in_study_extent"] is not True)
            ):
                verification_status = "needs_review"
            village_code = (row.get("village_lgd_code") or "").strip() or None
            village = _village_records().get(village_code or "")
            result.append(
                {
                    "shelter_id": shelter_id,
                    "name": (row.get("name") or "").strip() or None,
                    "type": (row.get("type") or "").strip() or None,
                    "village_lgd_code": village_code,
                    "village": village["village_name_en"] if village else None,
                    "taluk": (row.get("taluk") or "").strip() or (
                        village["taluk_name_en"] if village else None
                    ),
                    "latitude": latitude,
                    "longitude": longitude,
                    "coordinate_validation": coordinate_check,
                    "address": (row.get("address") or "").strip() or None,
                    "capacity": _int_or_none(row.get("capacity")),
                    "contact": (row.get("contact") or "").strip() or None,
                    "verification": {
                        "status": verification_status,
                        "source": source,
                        "verified_at": verified_at,
                    },
                    "operational_status": operational_status,
                    "last_updated": (row.get("last_updated") or "").strip() or None,
                    "provenance": {
                        "source": source,
                        "last_updated": (row.get("last_updated") or "").strip() or None,
                    },
                    "operational": (
                        verification_status == "verified"
                        and operational_status == "operational"
                    ),
                }
            )
        if len({item["shelter_id"] for item in result}) != len(result):
            raise RuntimeError("Shelter source contains duplicate shelter_id values.")
        return tuple(result)


def shelter_coverage() -> dict[str, Any]:
    records = get_shelter_records()
    counts = {
        status: sum(item["verification"]["status"] == status for item in records)
        for status in VERIFICATION_STATUSES
    }
    return {
        "total_records": len(records),
        "route_eligible_records": sum(item["operational"] for item in records),
        **counts,
        "source_file": str(SHELTER_PATH.relative_to(DATA.parent)),
        "source_coverage": (
            "authoritative" if counts["verified"] else "no_verified_shelter_records"
        ),
    }


def village_origin(village_code: str) -> dict[str, Any] | None:
    code = village_code.strip()
    village = _village_records().get(code)
    geometry = village_geometries().get(code)
    if village is None or geometry is None:
        return None
    point = geometry.representative_point()
    return {
        "village_lgd_code": code,
        "village_name": village["village_name_en"],
        "taluk": village["taluk_name_en"],
        "latitude": float(point.y),
        "longitude": float(point.x),
        "coordinate_method": "polygon_representative_point_routing_provider_snap_required",
    }


def village_code_exists(village_code: str) -> bool:
    return village_code.strip() in _village_records()


def shelter_is_route_eligible(shelter: dict[str, Any]) -> bool:
    return bool(
        shelter["operational"]
        and shelter["coordinate_validation"]["valid"]
        and shelter["coordinate_validation"]["in_study_extent"] is True
        and shelter["latitude"] is not None
        and shelter["longitude"] is not None
    )


def distance_km(origin_lat: float, origin_lon: float, destination_lat: float, destination_lon: float) -> float:
    point_a = Point(origin_lon, origin_lat)
    point_b = Point(destination_lon, destination_lat)
    from math import asin, cos, radians, sin, sqrt

    lat1, lat2 = radians(point_a.y), radians(point_b.y)
    delta_lat = lat2 - lat1
    delta_lon = radians(point_b.x - point_a.x)
    value = sin(delta_lat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(delta_lon / 2) ** 2
    return 6371.0 * 2 * asin(sqrt(value))
