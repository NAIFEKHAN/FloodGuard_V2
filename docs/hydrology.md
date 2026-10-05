# DEM-derived hydrology pipeline

FloodGuard's hydrology products are offline derivatives of the existing SRTM DEM. They are model inputs for baseline susceptibility only for the exact-ID villages with complete Phase 3 summaries, documented in [spatial-model.md](./spatial-model.md). They do not represent a forecast or observed water level and do not change warning thresholds.

## Inputs and analysis extent

- DEM: `data/processed/dem_nilgiris_mosaic.tif`, the existing four-tile SRTM GL1 mosaic (`N10E076`, `N10E077`, `N11E076`, `N11E077`). The source mosaic and raw tiles are never changed.
- Boundaries: `data/raw/admin/vb_soi_tn.kmz`, read using the current exact-LGD matching convention.
- Village master: `data/processed/nilgiris_villages.csv`.
- Boundary extent: union of the 58 valid Nilgiris polygons in the supplied KMZ, buffered outward by 5,000 m to preserve nearby upstream terrain. The raster is clipped to this buffered envelope; pixels outside the buffered polygon are NoData.
- Processing CRS and grid: UTM zone 43N (`EPSG:32643`), 30 m square cells. Elevation is bilinearly reprojected from the mosaic's `EPSG:4326` grid. The supplied DEM covers the buffered analysis area.
- Village summaries are emitted only for exact `village_lgd_code` matches between a valid KMZ polygon and the 102-row Village Master. Other master villages stay unavailable; no geometry or values are inferred.

## Methods

1. **Depression conditioning:** deterministic Priority-Flood fills depressions to their lowest spill elevation without editing the input mosaic. Valid cells at the processing edge or beside NoData are outlets. A flood-parent direction resolves flat cells without introducing flow cycles. The conditioned elevation surface is saved separately.
2. **Flow direction:** D8 chooses the steepest lower neighboring cell using projected 30 m distances. Flats use the deterministic Priority-Flood parent. The GeoTIFF encodes ESRI power-of-two directions: `1` east, `2` southeast, `4` south, `8` southwest, `16` west, `32` northwest, `64` north, `128` northeast; `0` is an outlet and `255` is NoData. Direction codes are available for analysis, not shown directly to dashboard users.
3. **Flow accumulation:** an acyclic D8 graph is traversed topologically. Each cell's stored `uint32` value is the unnormalized count of upstream cells including itself. No normalization is applied to the analysis raster.
4. **Drainage representation:** cells at or above the configurable accumulation threshold form a terrain-derived channel network. The GeoJSON is generalized linework in WGS 84; transparent PNG previews are provided for efficient Leaflet overlays. The flow-accumulation preview uses log-scaled opacity. The PNG bounds are recorded in `hydrology_metadata.json`.
5. **Village aggregation:** exact village polygons are rasterized against the projected grid. Metrics include mean and maximum raw accumulation, maximum contributing area (cell count × 900 m²), fraction of valid village cells meeting the stream threshold, thresholded D8 drainage length, and drainage density (drainage length / valid rasterized village area). Stream length is assigned by the originating raster cell centre; it is an approximation at polygon edges.

### Drainage threshold

The default is **1,000 upstream cells**, approximately **0.90 km²** at 30 m resolution. This is an explicit initial channel-initiation area for district-scale headwater linework, not an official or locally calibrated hydrography threshold. Threshold effects should be reviewed against authoritative hydrography before operational interpretation. Set `HYDROLOGY_STREAM_THRESHOLD_CELLS` or pass `--stream-threshold-cells` to change it; the selected cell count and equivalent area are stored with the outputs.

### Deliberately omitted wetness classification

No Topographic Wetness Index, “water accumulation potential” tier, or flood probability is emitted. SRTM contributing area by itself does not establish soil transmissivity, rainfall, water level, or a defensible slope floor for flat-cell TWI. The API returns these classifications as `not_derived` rather than presenting unsupported categories.

## Regeneration

Install the repository dependencies, then run this from the repository root:

```powershell
python -m pip install -r requirements.txt
python -m pipeline.build_hydrology_features
```

On Windows, the project virtual environment can be used directly:

```powershell
.\backend\venv\Scripts\python.exe -m pipeline.build_hydrology_features
```

Optional configuration:

```powershell
$env:HYDROLOGY_STREAM_THRESHOLD_CELLS = "1000"
python -m pipeline.build_hydrology_features --buffer-m 5000 --resolution-m 30
```

`--dem`, `--villages`, `--kmz`, and `--output-dir` can override input/output paths. The script writes only under `data/processed/hydrology/` and replaces its derived outputs when rerun. In this Windows workspace the completed cached-kernel build took about 66 seconds; a first run also compiles the Numba kernels and may take longer.

## Outputs

| Output | Purpose |
| --- | --- |
| `filled_dem.tif` | Priority-Flood conditioned elevation raster |
| `flow_direction.tif` | D8 direction codes described above |
| `flow_accumulation.tif` | Raw, unnormalized `uint32` upstream-cell counts |
| `flow_accumulation_preview.png` | Transparent log-scaled map preview; not an analysis substitute |
| `drainage_network.geojson` | Generalized WGS 84 D8-derived drainage linework |
| `drainage_network_preview.png` | Transparent thresholded network preview |
| `village_hydrology_features.csv` | Exact-LGD village summaries |
| `hydrology_metadata.json` | Inputs, CRS, resolution, algorithms, threshold, bounds, coverage, limitations, and build time |

## API and dashboard

- `GET /api/hydrology` returns output availability, processing metadata, provenance, and URLs/bounds for the map previews and GeoJSON.
- `GET /api/villages/{village_code}/hydrology` returns the exact-LGD village metrics or an explicit unavailable response; unknown LGD codes return 404.
- `GET /api/villages/{village_code}/context` includes the same hydrology fields in its existing context response.
- `/api/hydrology` does not perform raster processing. If the offline outputs are absent, it returns `status: "unavailable"` and null data.
- The Layers panel offers separate, initially-off Flow Accumulation and Drainage Network overlays. They use static transparent PNG previews to avoid rendering tens of thousands of vector path objects in Leaflet. Raw rasters and drainage GeoJSON remain available under `/data/processed/hydrology/`.

## Current build validation

The generated workspace build used 4,458,326 valid 30 m cells in the buffered analysis mask; the remaining 2,686,747 cells in the rectangular output grid are intentionally outside that mask or inherited NoData. The conditioned surface raised 175,182 valid cells relative to a reprojection of the original DEM; the largest raise was about 36.9 m (mean raise among changed cells about 2.32 m). D8 accumulation is nonnegative and ranges from 1 to 1,685,885 contributing cells. The 1,000-cell threshold selects 88,439 cells; this output has 78,615 generalized line segments. Across 4,446,233 routed cell edges, downstream accumulation is monotonic with zero violations. The three analytical GeoTIFFs use `EPSG:32643` at 30 m and share the same valid-cell mask; the PNG previews use WGS84 bounds recorded in metadata.

Village metrics join 40 of 102 master records by exact LGD code; 62 are unavailable. In this build, drainage density ranges from 0.055 to 2.858 km/km², with a median of 0.743 km/km² across the 40 matched villages. These are DEM-derived, threshold-sensitive metrics, not verified operational drainage or flood exposure. The supplied boundary and DEM do not provide a reference hydrography layer for independent channel-position validation.

## Limitations

SRTM is a surface elevation model, not a surveyed hydrologic terrain model. There is no stream burning, hydro-enforcement, rainfall-runoff model, soil data, or validated waterbody mask. The 5 km buffer reduces but does not eliminate edge effects from upstream catchments beyond the processed envelope. The channel threshold is configurable and impacts drainage lengths/density. These products are not flood extent, flood probability, live water levels, nor risk-model inputs.
