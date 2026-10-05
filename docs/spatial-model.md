# Baseline susceptibility model

## Output separation

FloodGuard distinguishes:

1. **Baseline modeled susceptibility** — a relative, 0–100 output from a model fit to terrain, DEM-derived hydrology, and historical rainfall summaries. The training features do not include validated current weather or current rainfall, so this is not a current-conditions estimate or real-time prediction.
2. **Current/environmental conditions** — browser-fetched Open-Meteo weather remains contextual only. It is not persisted or used as a model feature. No validated live rainfall feed is ingested by this model.
3. **Scenario-adjusted modeled risk** — an on-demand simulation that changes only the three historical rainfall percentile/maximum inputs (`rainfall_7d_p95_mm`, `rainfall_3d_p95_mm`, `rainfall_1d_max_mm`) in memory, then scores the modified feature rows. It does not change saved baseline data, the historical annual mean, warning logic, or thresholds. Scenario output is not an observation, forecast, official warning, or calibrated probability.

The existing tier interpretation is unchanged: High ≥60, Medium ≥30, Low <30. Existing warning logic is separate and was not modified in this phase.

## Model audit and training data

The training entry point is `pipeline/train_spatial_model.py`. `build_spatial_dataset()` aggregates five years of historical IMD rainfall data (`village_time_features.csv`), merges exact-ID SRTM village terrain summaries, event-linkage evidence, the Village Master taluk, and Phase 3 hydrology summaries. Joins use `village_lgd_code`, never village names. Every merge is validated one-to-one and required-source gaps raise an error instead of dropping villages.

The current training population is limited to **40 validated villages** with exact terrain and rainfall coverage and complete hydrology summary coverage. The last build matched **40/40** model villages to hydrology records and had no unmatched model rows; hydrology records outside the model population are also reported in the generated metrics. The remaining 62 of 102 Village Master records have no matching Phase 3 hydrology summary and are not silently added to or scored by this training population. No hydrology value is imputed. Missing feature values fail dataset construction/training with affected LGD codes.

### Feature groups

| Group | Model inputs |
| --- | --- |
| Terrain | `elevation_mean_m`, `elevation_range_m`, `slope_mean_deg`, `slope_max_deg` |
| Hydrology | `mean_flow_accumulation_cells`, `max_flow_accumulation_cells`, `high_flow_area_fraction`, `drainage_density_km_per_km2` |
| Historical rainfall | `rainfall_7d_p95_mm`, `rainfall_3d_p95_mm`, `rainfall_1d_max_mm`, `rainfall_annual_mean_mm` |
| Historical event evidence | No predictor columns. Strictly linked event count defines positive/unlabeled training status; it is not also supplied as a predictor. |
| Future sensor inputs | Soil moisture is declared as planned and excluded from training. No synthetic sensor value is created. |

The hydrology inputs are actual Phase 3 village output columns. No distance-to-drainage feature exists in the Phase 3 table, so none is invented. Event counts are conditional on the documented event-to-polygon linkage and source CRS caveats; absence of a linked record is not proof of no event.

## Algorithm, target, and preprocessing

The estimator is an XGBoost binary classifier with 35 trees, maximum depth 3, learning rate 0.08, random seed 42, log-loss evaluation, and one worker. The pipeline also compares weighted logistic regression and a weighted random forest.

`conditional_strict_linked_event_count > 0` defines the observed-positive indicator: 25 villages are `POSITIVE`, while 15 villages with no strict linked event are `UNLABELED`. No verified-negative examples exist. **Important implementation limitation:** the current weighted binary-estimator approach encodes unlabeled observations as class `0` and assigns them a lower sample weight (`n_positive / n_unlabeled`). It is a positive-versus-unlabeled proxy, not a formal PU risk estimator; unlabeled observations are not verified negatives. ROC-AUC, PR-AUC, and Brier score are therefore proxy metrics against this encoding and must not be interpreted as calibrated probability or verified-negative discrimination metrics.

`experimental_hazard_index_0_100` is excluded from the estimator because that descriptive index directly includes the linked-event count. The count itself is target evidence only. Neither is a model predictor.

Only the logistic-regression comparison standardizes inputs, with a `StandardScaler` fitted independently on each training fold. XGBoost and random forest use raw numeric feature values. Training rejects missing features; it does not apply an imputer.

## Spatial validation and artifacts

Validation uses six explicit Leave-One-Taluk-Out iterations. Each iteration holds out all rows from one of the six taluks, trains on the other five, and stores out-of-fold predictions for the held-out villages. This is a manual taluk loop; it is **not** a call to scikit-learn `GroupKFold`, despite previous validation-metadata wording.

Current hydrology-enabled positive-versus-unlabeled proxy metrics (rounded to four decimals):

| Model | LOTO ROC-AUC | LOTO PR-AUC | LOTO Brier |
| --- | ---: | ---: | ---: |
| Weighted logistic regression | 0.7173 | 0.8060 | 0.1935 |
| Weighted random forest | 0.7627 | 0.8209 | 0.1809 |
| Weighted XGBoost | 0.8573 | 0.9331 | 0.1780 |

An exploratory depth comparison on these same folds (not nested validation) selected maximum depth 3 to make it possible for the hydrology metrics to participate without changing the 35-tree count or learning rate. In each model, sample weights and explicit taluk holdouts are held constant. For context, the old eight-feature terrain/rainfall model at depth 3 scored ROC-AUC 0.8773, PR-AUC 0.9440, and Brier 0.1733, while the 12-feature hydrology-enabled model scored 0.8573, 0.9331, and 0.1780. Thus, adding hydrology has **not** improved these proxy metrics; selecting depth against these same folds adds model-selection optimism. A future independent or nested validation is needed before claiming model improvement.

The Phase 3 features are present in the 12-column estimator schema and in the scored-village table. The current final fit uses `mean_flow_accumulation_cells` (2.63%) and `high_flow_area_fraction` (1.04%) by split-based feature importance; `max_flow_accumulation_cells` and `drainage_density_km_per_km2` have zero split importance. At least one hydrology feature is used in five of the six LOTO fold models. The pipeline does not force feature use. These inputs make hydrology available to the baseline model, but current evidence does not establish a performance gain.

Artifacts:

- `data/processed/ml_spatial_dataset.csv` — 40-row exact-ID joined feature and evidence table.
- `data/processed/ml_village_susceptibility_scores.csv` — baseline scores, LOTO scores, PU status, evidence count, and used hydrology metrics.
- `model/artifacts/spatial_susceptibility_xgboost.json` — final XGBoost estimator with named feature schema.
- `model/artifacts/spatial_validation_metrics.json` — feature groups, join coverage, target/metric caveats, fold details, and feature importance.

Regenerate the dataset, validation report, scored rows, and model from the repository root after the Phase 3 hydrology outputs exist:

```powershell
.\backend\venv\Scripts\python.exe -m pipeline.train_spatial_model
```

FastAPI loads the XGBoost artifact lazily from `model/artifacts/spatial_susceptibility_xgboost.json`. `/api/ml-susceptibility` and `/api/status` preserve their existing response fields and add feature groups/coverage/interpretation. `/api/rainfall-scenario` continues to use the same model artifact and adds explicit baseline-versus-scenario classification. Tier and warning thresholds are unchanged.
