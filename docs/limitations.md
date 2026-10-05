# FloodGuard limitations and readiness

FloodGuard is a research/demo decision-support application for Nilgiris
District. It is not an operational warning or evacuation system. The
dashboard, APIs, model, or scenario output must not be used as a substitute
for official instructions from disaster-management authorities.

## Model and data coverage

- The Village Master contains 102 administrative records; exact model,
  terrain, village-time rainfall, and hydrology coverage is limited to 40.
  System coverage is available from `GET /api/system/coverage`.
- The classifier is a PU-weighted binary XGBoost proxy. It has 25
  event-linked positives, 15 unlabeled records, and zero verified negatives.
  Unlabeled examples are encoded as class 0 for fitting with lower sample
  weight; they are not verified negatives.
- ROC-AUC, PR-AUC, and Brier values compare positive and unlabeled examples
  under that proxy encoding. They are not calibrated probabilities,
  verified-negative discrimination, or evidence of operational warning
  performance.
- Six Leave-One-Taluk-Out folds provide spatial holdouts, but prior feature
  and depth choices were explored on the same folds. Independent or nested
  evaluation is still needed before claiming a model improvement.
- Historical event-to-village matches are conditional spatial evidence. The
  source release CRS/datum and positional accuracy are not certified; an
  unmatched or absent event record does not prove that no event occurred.
- DEM-derived terrain and hydrology are offline features. Flow accumulation,
  drainage density, elevation, and slope do not represent observed water
  level, current stream flow, or inundation.

## Current conditions and warnings

- No validated current rainfall feed is configured. `Current rainfall` is
  unavailable.
- Rainfall Scenario is an in-memory simulation over model features, not an
  observation, forecast, or official warning.
- Open-Meteo weather is fetched in the browser as context. It is not persisted
  or used as a model feature; system status reports the integration but does
  not probe external live health.
- Warning stages are configurable deterministic decision-support rules. They
  are not official alerts, calibrated event probabilities, guaranteed
  warnings, or evacuation orders.

## Sensors, shelters, and routing

- ESP32 soil-moisture storage and APIs are optional. The current system has
  zero registered real sensors; physical hardware, calibration, placement,
  and field reliability have not been validated. Test records are marked and
  remain distinct from real telemetry.
- Sensor observations do not enter baseline XGBoost predictions. A zero
  sensor count is a data-coverage gap, not a zero-moisture reading.
- The shelter CSV is a header-only ingestion template. There are zero
  source-verified, operational, route-eligible shelters. Legacy DEMO
  placeholders are retired and must not be used for routing.
- Evacuation routing needs verified facility records and a functioning
  external road-routing provider. The API will not present a straight-line
  fallback as a road route. A road route is not a safety guarantee or an
  official evacuation instruction.

## External dependencies and intended use

Satellite imagery, street tiles, weather, and road routing rely on external
providers and network availability. Attribution and provider terms must be
preserved. Availability of the local FastAPI service does not guarantee that
an external provider is reachable.

Use the dynamic `GET /api/system/status` and `GET /api/system/coverage`
responses to check artifact and optional-subsystem state before a demo. They
are not operational readiness certification. Consult [api-catalog.md](./api-catalog.md)
and [final-architecture.md](./final-architecture.md) for the current service
boundaries.
