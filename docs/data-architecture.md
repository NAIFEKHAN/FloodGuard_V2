# FloodGuard data architecture

FloodGuard uses a small, read-only provenance catalog and village-context service. Existing API responses remain available; the new endpoints supplement them.

## Shared data classifications

The API uses these `data_type` values for data and provenance:

| Type | Meaning in FloodGuard |
| --- | --- |
| `static` | Administrative identifiers, names, and reference boundaries |
| `observed` | Direct sensor observations, including explicitly labelled ESP32 test records |
| `external_current` | External current observations such as browser-fetched Open-Meteo weather |
| `derived` | Terrain statistics calculated from SRTM or future derived features |
| `historical` | Historical rainfall and supplied landslide inventory evidence |
| `modeled` | Existing PU Spatial XGBoost susceptibility outputs |
| `simulated` | Rainfall-scenario parameters and scenario calculations |

Source availability is reported as `available`, `unavailable`, `stale`, or `planned`. Sensor freshness is computed by the backend using its centralized 10-minute online and 30-minute stale limits. The soil-moisture source remains `planned` until at least one non-test sensor is registered; configured-source availability does not imply a recent reading. The Open-Meteo entry indicates a browser integration exists, not that an external request is currently healthy.

Provenance objects contain `source_name`, `source_type`, `data_type`, `provider`, `dataset`, `unit`, `observed_at`, `generated_at`, `last_updated`, and `status`. Unrecorded metadata and times are `null`. API response-generation time is not misrepresented as the creation date of an existing artifact.

## Identifiers and current coverage

`village_lgd_code` is the canonical spatial/model identifier and is also exposed as `village.code` and `village.village_code` in the context response. The source workbook's distinct, shorter `village_code` is returned as `source_village_code`; it is not used to join model, terrain, rainfall, or event-linkage tables.

The supplied Village Master contains 102 administrative records. The model, exact-ID village terrain, and village-time rainfall datasets contain the same 40-village subset; names and LGD identifiers reconcile exactly for those 40. The remaining 62 records are still returned by the existing village endpoint, but do not receive inferred model, terrain, or village-time rainfall values. Geometry is also incomplete for the district.

The 772-row landslide inventory is standalone evidence. The separate linkage artifact has 349 strict point-in-polygon matches across 25 villages. These matches are conditional on a WGS 84 interpretation: the PDF release's CRS/datum and positional accuracy are not certified. A missing link or zero linked count is not evidence of no events or a model-negative label.

## Current source distinctions

- Village-level historical rainfall comes from `village_time_features.csv` for 40 validated LGD codes, associated with the nearest IMD grid cell to each validated polygon's interior representative point. It contains daily data for 2017, 2019, and 2022–2024. It is not current weather.
- `historical_rainfall_features.csv` and `terrain_features.csv` are separate historical/terrain feature tables for two DEMO settlement points. Their values must not be joined onto village records.
- Village terrain elevation and slope in `terrain_features_villages.csv` are derived from the documented SRTM GL1 tiles and cover only the exact-ID subset.
- The Open-Meteo browser feature fetches contextual external current weather directly. The backend does not persist that observation, so the village-context response reports weather data as unavailable there while identifying the existing source integration.
- ESP32 soil-moisture observations are stored in local SQLite and joined using `village_lgd_code`. Readings retain server `received_at`, observation `recorded_at`, sensor ID, raw ADC value, and a test-data flag. Stale/offline values are returned as last-known observations, not current readings; no reading is inferred when no telemetry exists. They remain contextual and are not included in the baseline model or warning calculations. See [esp32-soil-moisture.md](./esp32-soil-moisture.md).
- Baseline susceptibility is classified `modeled` and is built from terrain, Phase 3 hydrology, and historical rainfall for 40 exact-LGD villages. Historical event linkage defines positive/unlabeled training status but is not also a predictor. The six-fold manual LOTO validation, unlabeled-as-class-0 proxy metric caveat, feature groups, and coverage are documented in [spatial-model.md](./spatial-model.md). Rainfall-scenario outputs are classified `simulated` and their score is scenario-adjusted modeled risk; current browser weather is contextual only and is not a model input.
- Hydrology sources are registered as `derived`; their status depends on all required offline outputs being present. ESP32 soil moisture is a local observed-data source; it remains `planned` until a non-test sensor is registered, and villages without readings return unavailable/null data.

## Endpoints

### `GET /api/data-sources`

Returns the catalog records with an ID, display name, data type, provider/dataset when known, unit, refresh style, availability status, artifacts, and coverage notes. DEM-derived hydrology status reflects precomputed artifact availability; ESP32 soil moisture becomes configured/available after a non-test sensor is registered.

### `GET /api/villages/{village_code}/context`

Accepts the canonical village LGD code. Optional `scenario` values are `moderate`, `baseline`, `heavy`, `extreme`, or `custom`; a custom scenario also requires `multiplier` from 0.1 to 5.0. Scenario parameters are metadata only in this endpoint; scenario risk scores continue to come from the existing `/api/rainfall-scenario` endpoint.

The response aggregates administrative metadata, stored modeled risk, exact-ID terrain, the latest historical village-time rainfall row, conditionally linked event records, model validation metadata, and per-source provenance. It includes exact-LGD DEM-derived hydrology when the offline outputs exist and latest registered soil-moisture context when readings are stored. Soil moisture does not enter baseline model calculations. Optional source files are loaded independently: a missing optional artifact is logged and reported as unavailable without dropping other available context. Unknown LGD codes return HTTP 404.

Example request:

```text
GET /api/villages/635099/context?scenario=baseline
```

Existing `/api/villages`, `/api/status`, `/api/ml-susceptibility`, `/api/rainfall`, `/api/terrain`, `/api/events`, and `/api/rainfall-scenario` endpoints remain in place. Their additive metadata distinguishes historical, derived, modeled, and simulated values.

Hydrology derivation, coverage, algorithms, outputs, and caveats are documented in [hydrology.md](./hydrology.md).
