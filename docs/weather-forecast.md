# Open-Meteo village forecast input

FloodGuard's **AUTO Live Forecast Mode** uses Open-Meteo's public forecast API:

`https://api.open-meteo.com/v1/forecast`

No API key is required. Each unique coordinate in the current model-village coordinate table is requested with `hourly=rain,precipitation,precipitation_probability`, `current=precipitation`, and `timezone=Asia/Kolkata`. The API returns hourly timestamps and precipitation amounts in mm.

The repository currently stores village-linked coordinates in `data/processed/village_time_features.csv` as `imd_grid_latitude` and `imd_grid_longitude`. These are the existing historical IMD grid coordinates assigned to exact-ID polygon representative points, not exact village centroids. There are 40 model-supported villages and these assignments currently collapse to seven distinct coordinate pairs. FloodGuard fetches forecasts per unique coordinate, then maps each response back to each village LGD code; the 62 administrative villages without validated model coordinates are not assigned invented locations or forecasts.

For each coordinate, the service uses the current local forecast timestamp and selects the next 24 consecutive hourly values after it. It sums Open-Meteo's `precipitation` values (mm); if an hourly total is null but `rain` is numeric, it uses that returned rain amount for the interval. If the forecast does not contain all 24 valid hourly intervals, the request is treated as unavailable rather than filling values. Hourly rain, total precipitation, precipitation probability, and current precipitation (when supplied) are retained in the response. Probability percentages are not treated as rainfall amounts.

The 24-hour total enters the existing `rainfall_1d_max_mm` model feature through the existing scenario evaluator. That feature was trained as the historical maximum daily rainfall; substituting a 24-hour forecast is a same-period model-input substitution, not a retrained or calibrated live prediction. Terrain and other model features and feature ordering remain unchanged. Result records are labeled forecast-adjusted modeled risk, not official warnings.

Successful forecasts are cached at `data/cache/open_meteo_latest.json` for 60 minutes by default. Configure `WEATHER_CACHE_TTL_MINUTES` or `WEATHER_CACHE_PATH` in the server process environment. Freshly retrieved results report `status: live`; cache responses report `status: cached`. A live-fetch failure uses the most recent valid cache. If both the live request and cache are unavailable, the endpoint reports unavailable and the dashboard returns to the existing manual Scenario Mode.

Endpoints:

- `GET /api/weather/forecast` — forecast and model scores; accepts optional `village_lgd_code`.
- `GET /api/weather/status` — cache status without an upstream request.
- Backward-compatible aliases remain at `/api/imd/forecast` and `/api/imd/status`.

The available `/data/raw/admin/vb_soi_tn.kmz` route may return 404 when the raw source file is absent from a checkout. Forecast input uses the already-processed village-time coordinate assignments and does not need to fetch that KMZ at runtime.
