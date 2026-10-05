# FloodGuard

FloodGuard is a village-level flood and landslide decision-support demonstration for hilly, high-risk regions. Nilgiris District, Tamil Nadu is the current demonstration region; data coverage and configuration are region-specific.

## Problem

Flash floods and landslides in hilly terrain can have very short warning times. Rainfall alone does not describe local risk: terrain and historical evidence also influence the vulnerability of a specific village or ward.

## Current system

FloodGuard provides an interactive Leaflet map, provenance-aware FastAPI endpoints, an existing spatially validated PU-weighted XGBoost susceptibility model, offline terrain and hydrology products, historical rainfall features, event evidence, rule-based warning decision support, and optional sensor and shelter/routing integrations. Rainfall scenario results are clearly presented as simulations, not live observations.

## Architecture

```text
Raw data
  -> validation and provenance
  -> GIS processing / feature engineering
  -> model-ready feature table
  -> spatial validation and ML model
  -> FastAPI
  -> frontend risk map
```

The repository keeps these concerns separate:

- `backend/` — FastAPI application
- `frontend/` — static HTML, CSS, and JavaScript
- `data/` — raw, processed, demo, and source/provenance materials
- `pipeline/` — rainfall, terrain, events, and spatial processing
- `model/` — trained model and spatial validation artifacts
- `tests/` and `docs/` — verification and supporting documentation

## Data sources and provenance

- **Rainfall:** historical IMD-derived features for the validated 40-village subset; no validated current rainfall feed is configured
- **Terrain and hydrology:** SRTM-derived offline elevation, slope, flow accumulation, and drainage features
- **Historical events:** standalone supplied GSI/NLFC evidence with conditional spatial linkage caveats
- **Boundaries:** supplied Village Master and SOI KMZ boundary sources

Source provenance, availability, and known gaps are surfaced in the data catalog and system coverage endpoints. Missing official data is reported as unavailable rather than replaced with fabricated values.

## Implemented capabilities

The repository includes reproducible data and model pipelines, hydrology
features, warning rules, optional ESP32 sensor storage, source-verified
shelter/routing interfaces, satellite and street basemaps, and system
readiness/coverage reporting. The status endpoints are informational and do
not certify operational readiness.

## Current dashboard

The FastAPI service serves the Leaflet dashboard at `/` and static assets under `/assets`. Existing endpoints provide village, terrain, historical rainfall, event evidence, model status, susceptibility, and rainfall-scenario data. The `/api/data-sources` registry, `/api/villages/{village_code}/context`, `/api/system/status`, and `/api/system/coverage` expose provenance, village context, live artifact coverage, and optional-subsystem status without replacing existing routes. Hydrology endpoints and overlays are described in [docs/hydrology.md](./docs/hydrology.md); regenerate offline products with `python -m pipeline.build_hydrology_features`. See [docs/data-architecture.md](./docs/data-architecture.md) for identifiers and provenance, [docs/api-catalog.md](./docs/api-catalog.md) for the route catalog, and [docs/final-architecture.md](./docs/final-architecture.md) for runtime boundaries. The health endpoint remains available:

The map defaults to Esri World Imagery satellite tiles and offers OpenStreetMap as the alternate street basemap through the Layers panel. Provider attribution remains visible. The selected basemap is saved in browser local storage (`floodguard_basemap`); repeated satellite tile failures switch the map to Street Map.

```text
GET /health -> {"status": "ok", "service": "FloodGuard API"}
```

The dashboard presents baseline modeled susceptibility using terrain, hydrology, and historical rainfall for the 40-village training coverage, alongside separate rainfall-scenario-adjusted modeled risk. Browser weather remains contextual and is not an input to the model. See [docs/spatial-model.md](./docs/spatial-model.md) for feature schema, validation, coverage, and positive/unlabeled caveats.

The scenario drawer also offers an optional Open-Meteo live forecast mode. It fetches village-coordinate hourly rain and precipitation, caches successful results, and falls back to Scenario Mode if neither a live forecast nor cache is available. The forecast-adjusted model output substitutes the next-24-hour accumulation into the existing historical daily-maximum rainfall feature; it is not retrained or recalibrated. See [docs/weather-forecast.md](./docs/weather-forecast.md) for source, coverage, methodology, and limitations.

Optional ESP32 soil-moisture observations can be registered, authenticated, and persisted locally in SQLite. They appear as supplementary village context and on the map when coordinates are supplied; they do not affect baseline model outputs or warning logic. See [docs/esp32-soil-moisture.md](./docs/esp32-soil-moisture.md) for local-network setup, calibration, API details, and a clearly labelled software-only test path.

## Run locally (PowerShell)

Requires Python 3.11 or newer.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn backend.app.main:app --reload
```

Then open `http://127.0.0.1:8000/`. Check service health at `http://127.0.0.1:8000/health`. Run tests with:

```powershell
python -m pytest
```

<<<<<<< Updated upstream
## Limitations and demo readiness
=======
Optional demo email alerts use `SMTP_USER` and a Gmail App Password in the ignored local `.env` file; copy the names and recipient list from `.env.example`. The dashboard never receives SMTP credentials. The test endpoint is `POST /api/alerts/test-email`. Automatic high-risk AUTO alerts are deduplicated per village for 30 minutes.

## Limitations
>>>>>>> Stashed changes

Current rainfall is unavailable, current susceptibility is a demonstration model output, and rainfall scenarios are simulations. Exact model/terrain/hydrology coverage is 40 of 102 village-master records. No real ESP32 sensors are registered and the shelter inventory has zero verified, operational facilities, so routing cannot provide evacuation recommendations. Weather is contextual rather than a model input. FloodGuard does not issue official warnings, evacuation orders, guaranteed-safe routes, or exact predictions; follow local authorities for emergency instructions. See [docs/limitations.md](./docs/limitations.md).
