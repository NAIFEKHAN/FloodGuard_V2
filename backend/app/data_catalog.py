"""Known FloodGuard data sources and shared classification metadata."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"


class DataType(StrEnum):
    STATIC = "static"
    OBSERVED = "observed"
    EXTERNAL_CURRENT = "external_current"
    DERIVED = "derived"
    HISTORICAL = "historical"
    MODELED = "modeled"
    SIMULATED = "simulated"


class Availability(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    STALE = "stale"
    PLANNED = "planned"


@dataclass(frozen=True)
class DataSource:
    id: str
    name: str
    data_type: DataType
    provider: str | None
    dataset: str | None
    description: str
    unit: str | None
    refresh_type: str
    artifacts: tuple[Path, ...] = ()
    planned: bool = False


MODEL_VALIDATION_PATH = ROOT / "model/artifacts/spatial_validation_metrics.json"
if not MODEL_VALIDATION_PATH.exists():
    raise RuntimeError(f"Model validation metadata not found at {MODEL_VALIDATION_PATH}")

_model_validation = json.loads(MODEL_VALIDATION_PATH.read_text(encoding="utf-8"))
_xgboost_metrics = _model_validation["model_comparison"]["pu_xgboost"]
MODEL_METADATA: dict[str, object] = {
    "model_type": _model_validation["model_type"],
    "validation": _model_validation["spatial_validation_method"],
    "loto_roc_auc": _xgboost_metrics["loto_roc_auc"],
    "loto_pr_auc": _xgboost_metrics["loto_pr_auc"],
    "loto_brier_score": _xgboost_metrics["loto_brier_score"],
    "baseline_output": _model_validation["baseline_output"],
    "scenario_output": _model_validation["scenario_output"],
    "metric_interpretation": _model_validation["metric_interpretation"],
    "current_conditions": _model_validation["current_conditions"],
    "feature_groups": _model_validation["feature_groups"],
    "feature_importances": _model_validation["feature_importances"],
    "folds_using_hydrology": _model_validation["folds_using_hydrology"],
    "estimator_parameters": _model_validation["estimator_parameters"],
    "hydrology_join": _model_validation["hydrology_join"],
    "future_sensor_features": _model_validation["future_sensor_features"],
}


DATA_SOURCES: tuple[DataSource, ...] = (
    DataSource(
        id="village_master",
        name="Nilgiris Village Master",
        data_type=DataType.STATIC,
        provider=None,
        dataset="Supplied Village Master.xlsx; processed administrative records",
        description="Village names and administrative identifiers. Provider details beyond the supplied workbook are not recorded.",
        unit=None,
        refresh_type="static",
        artifacts=(DATA / "processed/nilgiris_villages.csv", DATA / "raw/admin/Village Master.xlsx"),
    ),
    DataSource(
        id="village_boundaries",
        name="Village boundary polygons",
        data_type=DataType.STATIC,
        provider="Survey of India (attributed in project documentation)",
        dataset="vb_soi_tn.kmz",
        description="KMZ polygons reconcile by exact LGD identifiers for 40 of 102 Village Master records; this is not a complete district boundary set.",
        unit=None,
        refresh_type="static",
        artifacts=(DATA / "raw/admin/vb_soi_tn.kmz",),
    ),
    DataSource(
        id="srtm_terrain",
        name="SRTM GL1 village terrain summaries",
        data_type=DataType.DERIVED,
        provider="OpenTopography (supplied SRTM GL1 source tiles)",
        dataset="SRTM GL1; exact-ID village zonal statistics",
        description="Elevation and slope derived from SRTM for two DEMO settlement points and exact-ID summaries for 40 validated village polygons.",
        unit="elevation: m; slope: degrees",
        refresh_type="derived",
        artifacts=(
            DATA / "raw/dem/N10E076.tif",
            DATA / "raw/dem/N10E077.tif",
            DATA / "raw/dem/N11E076.tif",
            DATA / "raw/dem/N11E077.tif",
            DATA / "processed/terrain_features.csv",
            DATA / "processed/terrain_features_villages.csv",
        ),
    ),
    DataSource(
        id="historical_rainfall_villages",
        name="Historical IMD rainfall by validated village",
        data_type=DataType.HISTORICAL,
        provider="India Meteorological Department (IMD)",
        dataset="0.25-degree gridded RAINFALL; five source years (2017, 2019, 2022-2024)",
        description="Daily rainfall joined to 40 exact-ID villages using the nearest IMD grid cell to each validated polygon interior point. This is historical, not current rainfall.",
        unit="mm",
        refresh_type="historical archive",
        artifacts=(DATA / "processed/village_time_features.csv",),
    ),
    DataSource(
        id="historical_rainfall_demo",
        name="Historical IMD rainfall for DEMO settlements",
        data_type=DataType.HISTORICAL,
        provider="India Meteorological Department (IMD)",
        dataset="0.25-degree gridded RAINFALL; five source years (2017, 2019, 2022-2024)",
        description="Separate historical feature table for two DEMO settlement points; not village-level rainfall.",
        unit="mm",
        refresh_type="historical archive",
        artifacts=(DATA / "processed/historical_rainfall_features.csv", DATA / "processed/historical_rainfall_provenance.json"),
    ),
    DataSource(
        id="historical_events",
        name="Historical landslide inventory",
        data_type=DataType.HISTORICAL,
        provider="GSI/NLFC (as recorded in project documentation)",
        dataset="Supplied landslide_report.pdf; extracted inventory",
        description="772 standalone inventory records. The supplied PDF does not document an original publisher, version, publication date, or source URL.",
        unit=None,
        refresh_type="historical archive",
        artifacts=(DATA / "processed/landslide_events.csv", DATA / "raw/events/landslide_report.pdf"),
    ),
    DataSource(
        id="historical_event_linkage",
        name="Conditional event-to-village spatial linkage",
        data_type=DataType.HISTORICAL,
        provider="Derived from the supplied inventory and exact-ID village polygons",
        dataset="Strict point-in-polygon linkage artifact",
        description="Conditional exact containment under a WGS 84 interpretation; event-release CRS/datum and positional accuracy are not certified. Links are evidence only, not model labels.",
        unit=None,
        refresh_type="derived",
        artifacts=(DATA / "processed/event_village_linkage.csv",),
    ),
    DataSource(
        id="current_weather",
        name="Current weather observations",
        data_type=DataType.EXTERNAL_CURRENT,
        provider="Open-Meteo",
        dataset="Current weather API (queried directly by the browser)",
        description="External contextual observations are fetched in the browser and are not persisted or currently aggregated by the village-context API.",
        unit="temperature: °C; precipitation: mm; humidity: %; wind: km/h",
        refresh_type="on demand in browser",
        artifacts=(ROOT / "frontend/js/dashboard.js",),
    ),
    DataSource(
        id="model_susceptibility",
        name="Baseline modeled susceptibility output",
        data_type=DataType.MODELED,
        provider="FloodGuard model artifact",
        dataset="PU-weighted XGBoost; terrain, DEM-derived hydrology, and historical rainfall; leave-one-taluk-out validation",
        description="Baseline modeled susceptibility scores for 40 exact-LGD villages. Rainfall-scenario-adjusted modeled risk is reported separately; scores are not calibrated probabilities or official warnings.",
        unit="relative score: 0-100",
        refresh_type="model artifact",
        artifacts=(
            DATA / "processed/ml_village_susceptibility_scores.csv",
            DATA / "processed/ml_spatial_dataset.csv",
            ROOT / "model/artifacts/spatial_susceptibility_xgboost.json",
            MODEL_VALIDATION_PATH,
        ),
    ),
    DataSource(
        id="rainfall_scenario",
        name="Rainfall scenario simulation",
        data_type=DataType.SIMULATED,
        provider="FloodGuard scenario engine",
        dataset="Non-destructive in-memory rainfall feature scaling",
        description="Simulated rainfall multipliers and resulting modeled scores; never an observed rainfall feed or forecast.",
        unit="rainfall multiplier: ×; modeled score: 0-100",
        refresh_type="on demand simulation",
        artifacts=(ROOT / "pipeline/run_rainfall_scenario.py",),
    ),
    DataSource(
        id="demo_shelters",
        name="Demonstration designated shelter locations",
        data_type=DataType.STATIC,
        provider="FloodGuard demonstration records",
        dataset="DEMO_DESIGNATED_SHELTERS in frontend/js/dashboard.js",
        description="Ten explicitly labelled DEMO shelter representations; not verified operational shelter records.",
        unit="coordinates: WGS 84 decimal degrees; capacity: persons",
        refresh_type="static demonstration data",
        artifacts=(ROOT / "frontend/js/dashboard.js",),
    ),
    DataSource(
        id="hydrology",
        name="DEM-derived hydrology products",
        data_type=DataType.DERIVED,
        provider="OpenTopography (supplied SRTM GL1 DEM), processed by FloodGuard D8 pipeline",
        dataset="Priority-Flood conditioning; D8 flow direction and accumulation",
        description="Offline, DEM-derived hydrology products over the buffered Nilgiris KMZ boundary extent. Not a flood forecast or observed water level.",
        unit="flow accumulation: upstream cells; drainage density: km/km2",
        refresh_type="reproducible offline pipeline",
        artifacts=tuple(
            DATA / "processed/hydrology" / filename
            for filename in (
                "filled_dem.tif",
                "flow_direction.tif",
                "flow_accumulation.tif",
                "flow_accumulation_preview.png",
                "drainage_network_preview.png",
                "drainage_network.geojson",
                "village_hydrology_features.csv",
                "hydrology_metadata.json",
            )
        ),
    ),
    DataSource(
        id="flow_direction",
        name="D8 flow direction",
        data_type=DataType.DERIVED,
        provider="OpenTopography SRTM GL1; FloodGuard D8 pipeline",
        dataset="Priority-Flood conditioned 30 m DEM; ESRI power-of-two D8 encoding",
        description="Derived raster with documented D8 compass encoding; raw codes are not shown as a user-facing layer.",
        unit="D8 direction code",
        refresh_type="reproducible offline pipeline",
        artifacts=(DATA / "processed/hydrology/flow_direction.tif",),
    ),
    DataSource(
        id="flow_accumulation",
        name="D8 flow accumulation",
        data_type=DataType.DERIVED,
        provider="OpenTopography SRTM GL1; FloodGuard D8 pipeline",
        dataset="Unnormalized upstream contributing-cell count, including each cell",
        description="Raw integer upstream cell counts retained in a GeoTIFF; no normalization is applied to the analysis output.",
        unit="upstream cells",
        refresh_type="reproducible offline pipeline",
        artifacts=(
            DATA / "processed/hydrology/flow_accumulation.tif",
            DATA / "processed/hydrology/flow_accumulation_preview.png",
        ),
    ),
    DataSource(
        id="drainage_network",
        name="D8-derived drainage network",
        data_type=DataType.DERIVED,
        provider="OpenTopography SRTM GL1; FloodGuard D8 pipeline",
        dataset="Generalized GeoJSON linework extracted above configurable contributing-area threshold",
        description="Terrain-derived drainage representation, not an authoritative mapped hydrography dataset.",
        unit="line length: km",
        refresh_type="reproducible offline pipeline",
        artifacts=(
            DATA / "processed/hydrology/drainage_network.geojson",
            DATA / "processed/hydrology/drainage_network_preview.png",
        ),
    ),
    DataSource(
        id="village_hydrology_features",
        name="Village hydrology summaries",
        data_type=DataType.DERIVED,
        provider="OpenTopography SRTM GL1; FloodGuard exact-LGD zonal aggregation",
        dataset="D8 accumulation, contributing-area and thresholded drainage metrics",
        description="Metrics joined by canonical village_lgd_code to exact village boundary polygons; uncovered villages remain unavailable.",
        unit="drainage density: km/km2; contributing area: km2",
        refresh_type="reproducible offline pipeline",
        artifacts=(DATA / "processed/hydrology/village_hydrology_features.csv",),
    ),
    DataSource(
        id="soil_moisture",
        name="ESP32 soil-moisture observations",
        data_type=DataType.OBSERVED,
        provider="Local FloodGuard sensor network",
        dataset="Locally registered ESP32 soil-moisture sensors and received readings",
        description="Observed sensor readings are stored locally; a registered sensor is required before this source is available. Test readings are explicitly labelled.",
        unit="%",
        refresh_type="device telemetry",
    ),
)

DATA_SOURCE_BY_ID = {source.id: source for source in DATA_SOURCES}


def source_status(source: DataSource) -> Availability:
    if source.planned:
        return Availability.PLANNED
    if source.id == "soil_moisture":
        from backend.app.sensor_store import has_registered_sensors

        return (
            Availability.AVAILABLE
            if has_registered_sensors()
            else Availability.PLANNED
        )
    if source.artifacts and all(path.is_file() for path in source.artifacts):
        return Availability.AVAILABLE
    return Availability.UNAVAILABLE


def source_provenance(
    source_id: str,
    *,
    unit: str | None = None,
    observed_at: str | None = None,
    generated_at: str | None = None,
    status: str | None = None,
    sensor_id: str | None = None,
    recorded_at: str | None = None,
    received_at: str | None = None,
    is_test: bool | None = None,
) -> dict[str, object]:
    source = DATA_SOURCE_BY_ID[source_id]
    result: dict[str, object] = {
        "source_name": source.name,
        "source_type": source.data_type.value,
        "data_type": source.data_type.value,
        "provider": source.provider,
        "dataset": source.dataset,
        "unit": unit if unit is not None else source.unit,
        "observed_at": observed_at,
        "generated_at": generated_at,
        "last_updated": None,
        "status": status if status is not None else source_status(source).value,
    }
    if sensor_id is not None:
        result["sensor_id"] = sensor_id
    if recorded_at is not None:
        result["recorded_at"] = recorded_at
    if received_at is not None:
        result["received_at"] = received_at
    if is_test is not None:
        result["is_test"] = is_test
    return result


def list_data_sources() -> list[dict[str, object]]:
    return [
        {
            "id": source.id,
            "name": source.name,
            "type": source.data_type.value,
            "data_type": source.data_type.value,
            "provider": source.provider,
            "dataset": source.dataset,
            "description": source.description,
            "unit": source.unit,
            "refresh_type": source.refresh_type,
            "status": source_status(source).value,
            "artifacts": [
                path.relative_to(ROOT).as_posix() for path in source.artifacts
            ],
        }
        for source in DATA_SOURCES
    ]
