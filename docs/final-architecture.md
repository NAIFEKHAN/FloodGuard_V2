# FloodGuard final architecture

## Runtime flow

```text
Source data and provenance
  -> validation and identifier reconciliation
  -> offline GIS / feature pipelines
  -> exact-LGD village feature and evidence tables
  -> spatially validated model artifacts
  -> FastAPI APIs and optional subsystem routers
  -> Leaflet dashboard and evidence/status views
```

Nilgiris District, Tamil Nadu is the current demonstration region. The
architecture separates source ingestion, offline processing, model artifacts,
API services, and the browser client; the current data coverage is not
district-wide for every feature.

## Repository responsibilities

- `data/raw/` retains supplied inputs and source-specific records.
- `data/processed/` contains validated administrative, event, terrain,
  rainfall, model, and hydrology artifacts.
- `pipeline/` provides reproducible ingestion, validation, spatial processing,
  feature engineering, model training, and hydrology operations.
- `model/artifacts/` stores the trained XGBoost model and spatial validation
  metadata.
- `backend/app/` serves the FastAPI application and focused API modules for
  provenance, village context, warning rules, sensors, shelters, and routing.
- `frontend/` contains the Leaflet dashboard, optional overlays, and
  Evidence & Audits interface.
- `tests/` verifies APIs, data joins, model semantics, warnings, sensors,
  shelter eligibility, routing, and system readiness.
- `docs/` records data, model, hydrology, sensor, warning, shelter, API, and
  system limitations.

## Identity, coverage, and model boundaries

`village_lgd_code` is the canonical key. The current Village Master has 102
administrative records. Exact model, terrain, rainfall-feature, and hydrology
coverage is 40 villages; uncovered records are not assigned inferred values.
System coverage endpoints calculate current counts from artifacts and stores.

The baseline model is the existing PU-weighted XGBoost proxy, using terrain,
DEM-derived hydrology, and historical rainfall features. Strict linked
historical event evidence defines positive/unlabeled training status but is
not a predictor. Spatial validation uses six Leave-One-Taluk-Out folds.
Metrics remain proxy metrics because the dataset has no verified negative
labels. See [spatial-model.md](./spatial-model.md).

Baseline modeled susceptibility, rainfall-scenario-adjusted modeled risk,
contextual weather, current rainfall, and warning-stage outputs are distinct
concepts:

- Baseline susceptibility uses the saved feature table and model artifact.
- A Rainfall Scenario modifies selected rainfall inputs in memory; it is a
  simulation, not current rainfall or a forecast.
- Browser-fetched Open-Meteo weather is contextual and is not a model input.
- No validated current rainfall feed is configured.
- Warning stages are rule-based decision support, not official alerts.

## Optional services and safe readiness states

The sensor router stores observations in local SQLite. No sensors are
currently registered; physical ESP32 validation is pending. Sensor
observations are supplementary and do not change the trained model.

Shelter and routing routers are mounted, but the source-verified shelter
inventory currently contains no route-eligible records. Routing remains
unavailable until facilities have authoritative provenance, verified
coordinates, and operational status. The API does not convert DEMO records or
straight-line distances into evacuation routes.

`GET /api/system/status` and `GET /api/system/coverage` expose backend and
subsystem readiness separately. Missing optional data is represented as
unavailable/degraded without masking the availability of the FastAPI process.
The endpoints do not expose sensor credentials. See [api-catalog.md](./api-catalog.md)
for the route catalog.

## Map and external providers

The Leaflet client uses Esri World Imagery for the default satellite basemap
and OpenStreetMap tiles for the street alternative, with provider attribution.
Basemap preference is stored locally; repeated imagery tile failures fall
back to the street basemap. Historical event, shelter, hydrology, and route
layers remain separate from basemap tiles. Weather and road routing depend on
their respective external services and are not represented as healthy merely
because the dashboard itself is reachable.

## Local execution

```powershell
python -m uvicorn backend.app.main:app --reload
```

The dashboard is served at `/`; API documentation is available at `/docs`.
See [api-catalog.md](./api-catalog.md), [data-architecture.md](./data-architecture.md),
and [limitations.md](./limitations.md) for operator-facing details.
