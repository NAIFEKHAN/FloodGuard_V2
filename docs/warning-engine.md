# Rule-based warning decision support

## Purpose and separation

The warning engine is a transparent decision-support workflow layered over existing FloodGuard outputs. It does not retrain or change the PU-XGBoost model, model features, susceptibility score, scenario model, or the separate risk-tier thresholds.

Keep these outputs distinct:

1. **Baseline modeled susceptibility**: the existing model score from terrain, hydrology, historical rainfall, and historical evidence.
2. **Current conditions**: fresh online ESP32 soil moisture may be used as supporting evidence; there is no validated current-rainfall feed. Current rainfall is therefore explicitly missing.
3. **Scenario-adjusted modeled risk**: the existing scenario evaluator’s output for a simulated rainfall multiplier. A rainfall scenario is never live rainfall.
4. **Warning stage**: GREEN/YELLOW/ORANGE/RED from the versioned rules below. It is not a calibrated event probability, guaranteed warning, or official operational alert.

The current rule version is `1.0.0`. All numeric boundaries are centralized in `backend/app/warning_config.py`; evaluation and reason construction are in `backend/app/warning_engine.py`. The default population is the existing 40-village model coverage, joined by `village_lgd_code`. Other villages return an explicit `unavailable` response rather than an inferred/fill value.

## Stage rules

Rules are checked from RED downward. `S` means baseline modeled susceptibility, `R` a simulated rainfall multiplier, and `M` a soil-moisture reading whose sensor freshness is `online`.

| Stage | Exact rule | Guidance |
|---|---|---|
| RED · Warning | `S ≥ 80` and (`R ≥ 2.0×` or (`R ≥ 1.5×` and `M ≥ 80%`)) | Seek official emergency information immediately. Be ready to move to designated safe areas and avoid flooded or unstable routes. |
| ORANGE · Prepare | `S ≥ 60` and (`R ≥ 1.5×` or `M ≥ 60%`) | Prepare essential items, identify designated safe locations, and monitor official instructions. |
| YELLOW · Watch | `S ≥ 60` or (`R ≥ 1.5×`) | Monitor weather and official local alerts. Avoid unnecessary exposure to known unstable slopes during heavy rain. |
| GREEN · Normal | No configured watch signal above | Continue normal monitoring and follow official local guidance. |

`R` is available only in **scenario** mode; it is simulated and is not a current observation. `M` is supplementary, not a baseline-model feature. Soil moisture alone does not raise the stage. Stale and offline readings are reported as context but are not accepted as current evidence. Test readings retain an explicit test-data label.

The baseline susceptibility score is used to trigger these rules, not the scenario-adjusted score. Scenario output remains available separately for inspection. Hydrology and historical-event linkage are returned as contextual factors but do not independently trigger a stage. DEM flow accumulation is not a measurement of current water level, flow, or inundation.

The thresholds are provisional, auditable decision-support boundaries. They are not calibrated or validated operational warning thresholds and should not be used as a substitute for local disaster-management authorities.

## Missing data and coverage

- The current model score must be present for an evaluated stage. If it is absent, the engine reports `status: unavailable`.
- Current mode always reports validated current rainfall as missing. It does not substitute historical rainfall or a scenario multiplier.
- Missing, stale, and offline soil readings remain explicit; they are not replaced with zero or treated as dry.
- Missing hydrology and historical linkage are listed as missing/context limitations; neither is numerically imputed by the warning engine.
- The current validated model population contains 40 LGD-coded villages. A village outside it is returned with `status: unavailable`; it does not silently receive GREEN.
- Warning history and all response payloads include the mode, rule version, factors, timestamps where available, missing inputs, and disclaimer.

## API

| Endpoint | Behavior |
|---|---|
| `GET /api/warnings?mode=scenario&scenario=baseline` | Evaluate the supported village population and return stage counts, thresholds, coverage, metadata, and village records. Preset scenarios include `moderate`, `baseline`, `heavy`, and `extreme`. |
| `GET /api/warnings?mode=scenario&multiplier=1.8` | Evaluate a custom simulated multiplier from `0.1` through `5.0`. |
| `GET /api/warnings?mode=current` | Evaluate using baseline susceptibility and currently online sensor context, with current rainfall explicitly unavailable. |
| `GET /api/villages/{village_lgd_code}/warning?...` | Return the same structured evaluation for one village; unknown LGD code is 404 and unsupported model coverage is returned as unavailable. |
| `GET /api/warnings/history?village_lgd_code=635099&limit=100` | Read the latest persisted material warning-stage changes, including rule and input-factor snapshots. `limit` is 1–500. |

GET warning evaluations are persisted to the existing local SQLite database (`data/sensors.sqlite3` by default, configurable with `SENSOR_DATABASE_PATH`). To avoid poll-driven duplicates, a history row is written only when a decision-relevant signature changes: stage, rule version/ID, mode and multiplier, sensor freshness, online soil-moisture band, or test-data status. History is a local audit of evaluations, not an external alert delivery log.

## Dashboard

The navigation status shows the highest non-GREEN stage and the number of villages in that stage. The optional **Warning Stage** map layer styles supported village polygons by evaluated stage; disabling it restores existing susceptibility colors. The selected-village drawer displays the stage, server-supplied reasons, missing inputs, mode, guidance, test-data status, rule version, and disclaimer. **Evidence & Audits → Scientific Governance** shows the active version, configured thresholds, latest evaluation, stage counts, and recent locally persisted material changes.

The dashboard does not send email, SMS, or WhatsApp alerts. It does not implement evacuation routing, sensor integration beyond the existing optional Phase 5 ingestion, live-rainfall ingestion, new warning thresholds outside this engine, or a revised model.
