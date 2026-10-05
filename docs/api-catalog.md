# FloodGuard API catalog

Run the service from the repository root with:

```powershell
python -m uvicorn backend.app.main:app --reload
```

The interactive OpenAPI document is available at `/docs`. Identifiers for
village-specific endpoints are canonical `village_lgd_code` values.

## Service and system readiness

| Method and path | Purpose |
| --- | --- |
| `GET /health` | Liveness check for the FastAPI process. |
| `GET /api/system/status` | Current backend/component readiness, coverage summary, and feature audit. Optional subsystem gaps do not make the backend itself unavailable. |
| `GET /api/system/coverage` | Artifact- and store-derived village, model, terrain, hydrology, rainfall, evidence, warning, sensor, and shelter coverage. |
| `GET /api/data-sources` | Provenance and configured artifact availability registry. |
| `GET /api/status` | Existing model metadata, validation, feature, and provenance response. |

The system status endpoints report `current_rainfall` as unavailable until a
validated current rainfall feed exists. Weather-provider integration is
configured, but its live health is not probed by these endpoints. Scenario
rainfall is explicitly marked simulated. Sensor storage availability does not
imply that any real sensors are registered or online. Warning-engine
readiness is derived from its local model, hydrology, and sensor-store
dependencies; status checks do not run a warning evaluation or write warning
history.

## Village, evidence, and model

| Method and path | Purpose |
| --- | --- |
| `GET /api/villages?limit=102` | Village Master administrative records; `limit` is 1–102. |
| `GET /api/villages/{village_code}/context` | Aggregated administrative, modeled, terrain, hydrology, rainfall, event, warning, weather-source, and sensor context. Optional query: `scenario`, `multiplier`, `warning_mode`. |
| `GET /api/ml-susceptibility` | Existing baseline modeled susceptibility records and model coverage. |
| `GET /api/experimental-hazard-index` | Fixed descriptive index, explicitly not ML or a prediction. |
| `GET /api/events` | Standalone historical GSI/NLFC event evidence and record classifications. |
| `GET /api/ddmp` | Official documentary evidence tables and manifest. |

## Rainfall, terrain, and hydrology

| Method and path | Purpose |
| --- | --- |
| `GET /api/rainfall-scenario` | On-demand simulated rainfall-feature scaling and scenario-adjusted model output. Supports preset scenarios or the existing bounded custom parameters. |
| `GET /api/rainfall/summary` | Summary of the historical rainfall feature source. |
| `GET /api/rainfall?year={year}` | Historical rainfall records for a supported year (2017–2024). |
| `GET /api/terrain` | Derived terrain feature records. |
| `GET /api/hydrology` | Precomputed hydrology metadata and overlay URLs. |
| `GET /api/villages/{village_code}/hydrology` | Exact-LGD hydrology summary, or an explicit unavailable response. |

Rainfall-scenario results are simulations, not observations or forecasts.
Terrain and hydrology are offline-derived products; they are not current
measurements of slope failure, stream flow, or inundation.

## Warning engine

| Method and path | Purpose |
| --- | --- |
| `GET /api/warnings` | Warning-engine decision-support results. Supports `mode`, `scenario`, and bounded `multiplier` parameters. |
| `GET /api/villages/{village_code}/warning` | One village's warning-engine result with the same mode/scenario parameters. |
| `GET /api/warnings/history` | Locally persisted material warning-stage changes. Supports optional `village_lgd_code` and `limit` (1–500). |

Warning stages and thresholds are configurable decision-support rules, not
official alerts or calibrated event probabilities. A scenario-mode result is
identified as simulated; current validated rainfall is not available.

## Sensors

| Method and path | Purpose |
| --- | --- |
| `POST /api/sensors` | Register an ESP32 soil-moisture sensor. |
| `GET /api/sensors` | List registered sensors and freshness state. |
| `GET /api/sensors/{sensor_id}` | Read one sensor registration. |
| `POST /api/sensors/readings` | Submit a bounded sensor reading. |
| `GET /api/sensors/{sensor_id}/readings` | Read stored observations for a sensor. |
| `GET /api/villages/{village_code}/soil-moisture` | Read latest village-associated soil-moisture context. |

Registration and reading writes require the configured `SENSOR_API_KEY`, sent
as `X-Sensor-Key`. Never place the key in browser code or status responses.
Test sensors/readings remain explicitly marked and are not real hardware
validation. Soil moisture is not an input to the susceptibility model.

## Shelters and evacuation routing

| Method and path | Purpose |
| --- | --- |
| `GET /api/shelters` | Shelter inventory, provenance, verification coverage, and route-eligible count. An empty inventory returns an explicit unavailable state. |
| `GET /api/shelters/{shelter_id}` | Read a shelter record; unknown IDs return HTTP 404. |
| `GET /api/villages/{village_code}/shelters` | Route-eligible shelter candidates for a known village; it does not claim straight-line distance is a road route. |
| `GET /api/evacuation-route` | Request a road route to a verified shelter using `shelter_id` and either a village code or paired origin coordinates. Supports warning `mode`, `scenario`, and `multiplier` context. |

The current shelter inventory is an empty ingestion template. DEMO or
unverified records are not routable; the API does not return a straight-line
fallback as an evacuation route. A road-route response also does not establish
that a route is safe or officially approved.

## Static resources

The dashboard is served at `/`; frontend assets are under `/assets`, and
repository data resources exposed to the dashboard are under `/data`.
