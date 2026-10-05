# FloodGuard

FloodGuard is a planned village/ward-level flood and landslide risk assessment system for hilly, high-risk regions. Nilgiris District, Tamil Nadu is the current demonstration region; it is not a limitation of the system design.

## Problem

Flash floods and landslides in hilly terrain can have very short warning times. Rainfall alone does not describe local risk: terrain and historical evidence also influence the vulnerability of a specific village or ward.

## Proposed solution

FloodGuard will combine traceable IMD gridded rainfall, public DEM-derived terrain features, and historical flood/landslide information from appropriate ISRO/NRSC/Bhuvan (and, where appropriate, GSI) sources. The resulting feature dataset will later support spatially validated machine-learning risk scoring and an interactive map. Rainfall scenario results will be clearly presented as simulations, not live observations.

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
- `model/` — reproducible model artifacts (future phase)
- `tests/` and `docs/` — verification and supporting documentation

## Data sources (future pipeline)

- **Rainfall:** IMD gridded rainfall data
- **Terrain:** SRTM or another suitable public DEM, used for elevation and justified terrain features such as slope
- **Historical events:** ISRO/NRSC/Bhuvan flood and landslide information; GSI sources where appropriate
- **Boundaries:** reliable official or public administrative/geographical sources

No external datasets are downloaded or included in this foundation task. Each real dataset added later must include its provenance. When an official source cannot be ingested automatically, FloodGuard will document an ingestion interface instead of inventing data.

## Four development phases

1. **Foundation + data pipeline** — scaffold and documentation; reproducible rainfall, terrain, and event pipelines; a model-ready feature dataset. No ML model.
2. **ML + spatial validation** — target definition from real events, XGBoost training, spatial/group-based validation, metrics, and artifacts.
3. **Backend + dashboard** — model-backed APIs and a Leaflet-based dashboard using a public/OpenStreetMap-compatible basemap.
4. **Integration + hackathon ready** — end-to-end testing, UX improvements, provenance and validation checks, reproducible documentation, and demo preparation.

## Current dashboard

The FastAPI service serves the Leaflet dashboard at `/` and static assets under `/assets`. Existing API endpoints provide the validated village, terrain, rainfall, historical-event, model-status, susceptibility, and rainfall-scenario data used by the interface. The `/api/data-sources` registry and `/api/villages/{village_code}/context` endpoint add source metadata and per-village data aggregation without replacing existing routes. DEM-derived hydrology endpoints and optional Leaflet overlays are described in [docs/hydrology.md](./docs/hydrology.md); regenerate their offline products with `python -m pipeline.build_hydrology_features`. See [docs/data-architecture.md](./docs/data-architecture.md) for identifier, coverage, provenance, and availability details. The health endpoint remains available:

The map defaults to Esri World Imagery satellite tiles and offers OpenStreetMap as the alternate street basemap through the Layers panel. Provider attribution remains visible. The selected basemap is saved in browser local storage (`floodguard_basemap`); repeated satellite tile failures switch the map to Street Map.

```text
GET /health -> {"status": "ok", "service": "FloodGuard API"}
```

The dashboard presents baseline modeled susceptibility using terrain, hydrology, and historical rainfall for the 40-village training coverage, alongside separate rainfall-scenario-adjusted modeled risk. Browser weather remains contextual and is not an input to the model. See [docs/spatial-model.md](./docs/spatial-model.md) for the feature schema, validation, coverage, and positive/unlabeled caveats. This remains a decision-support demonstration, not an operational warning service.

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

## Limitations

FloodGuard is not an operational warning system. Current susceptibility scores are a demonstration spatial model output, rainfall scenarios are simulations, and weather observations are contextual rather than model inputs. Shelter records are demonstration representations. The interface does not issue official warnings, evacuation orders, guaranteed-safe routes, or exact predictions; follow local authorities for emergency instructions.
