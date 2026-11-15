# backend

FastAPI backend.

## Setup

```sh
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```sh
fastapi dev app/main.py
```

- API: http://localhost:8000
- Docs: http://localhost:8000/docs
- Health check: http://localhost:8000/api/health

## Data endpoints

Area endpoints take two corners, `top_left=<lat>,<lon>` (north-west) and
`bottom_right=<lat>,<lon>` (south-east). Point endpoints take `lat` and `lng`.
Full parameter docs: http://localhost:8000/docs

| Endpoint                                          | What                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| ------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `GET /api/climate/daily`                          | Daily weather, `source=era5\|power`, optional `with_et0=true`                                                                                                                                                                                                                                                                                                                                                                             |
| `GET /api/climate/heat-frost`                     | Test 1: July heat, wheat-flowering heat and April frost days per year, ERA5 vs POWER                                                                                                                                                                                                                                                                                                                                                      |
| `GET /api/drought`                                | Test 2: FAO-56 ET₀ + 150 mm bucket, seasonal shortfall and FAO-33 relative yield per crop/year                                                                                                                                                                                                                                                                                                                                            |
| `GET /api/drought/season`                         | Daily bucket trace (soil water, Ks, ETa) for one crop and harvest year                                                                                                                                                                                                                                                                                                                                                                    |
| `GET /api/fields/ndvi`                            | One rectangular plot: period timeline from Sentinel-2 NDVI (2017+) as `{periods: [{start, end, type, crop_type, confidence}]}`; type is barren / crop / dormant_crop / green_cover; `detail=true` returns the full analysis                                                                                                                                                                                                               |
| `POST /api/fields/ndvi`                           | Several plots. Either the frontend's picked fields, `{"fields": [{"id", "polygon": [{"lat", "lng"}, ...]}]}` (same shape as `/api/fields/suitability`), or plain outlines, `{"plots": [[[lat, lon], ...], ...]}`; plus `fromYear`/`toYear` and the GET options (snake_case or camelCase). Returns one `{id?, periods}` body per plot in input order, with `error` for a plot that failed                                                  |
| `POST /api/fields/ndvi/prefetch`                  | Same body; starts fetching the plots' Sentinel-2 data in the background and returns 202 at once. The frontend calls it when a field is picked                                                                                                                                                                                                                                                                                             |
| `POST /api/fields/suitability`                    | Same plots as `POST /ndvi`, plus `inputLevel` (high / low), `minClass` (default medium) and `groups` (cereal, oilseed, ...). Returns per plot `{id?, cell, crops: [{crop, code, group, suitability 0–100, class, attainable_yield_kg_ha, yield_basis}]}`, best first, from FAO GAEZ v4 (rainfed, 1981–2010 climate, ~9 km cells, cached in 1° tiles)                                                                                      |
| `POST /api/prices`                                | `{country: "BG", crops?, overrides?: {wheat: 210}, referenceYears?: 5}`. Per crop: reference price (mean of the latest years, € per tonne in today's money), price variability, confidence and the yearly history. An override replaces the reference price and shows as the farmer's                                                                                                                                                     |
| `GET /api/prices/{country}/table`                 | Every mapped crop and year for a country: € per tonne (today's money), source, confidence                                                                                                                                                                                                                                                                                                                                                 |
| `GET /api/sentinel/search` / `image` / `stats`    | Raw Sentinel Hub Catalog / Process / Statistical API; `image?type=ndvi-color` renders NDVI as a coloured PNG                                                                                                                                                                                                                                                                                                                              |
| `GET /api/crops/soil-scores`                      | How every scored crop affects the soil, from the `crop_soil_scores` table (loaded by `app/import_crop_csvs.py`), with the GAEZ crop codes each row stands for (`GAEZ_SOIL_CROPS` in `app/services/crop_soil_scores.py`)                                                                                                                                                                                                                   |
| `GET /api/soil/point?lat=&lng=`                   | Most probable WRB soil groups at a point from ISRIC SoilGrids (250 m), cached in Postgres (`json_cache`)                                                                                                                                                                                                                                                                                                                                  |
| `GET /api/soil/chemistry?top_left=&bottom_right=` | Sampled rectangle mean of SoilGrids pH in water, nitrogen and soil organic carbon by depth                                                                                                                                                                                                                                                                                                                                                |
| `GET /api/soil/physical?top_left=&bottom_right=`  | Sampled rectangle mean of SoilGrids clay, sand, silt and bulk density by depth                                                                                                                                                                                                                                                                                                                                                            |
| `POST /api/soil/selection`                       | Sampled ISRIC SoilGrids chemistry and physical properties for a rectangle or selected field polygons, by depth                                                                                                                                                                                                                                                                                                                         |
| `GET /api/sentinel/soil-characteristics`          | Sentinel Hub Statistical API spectral indicators and historical series                                                                                                                                                                                                                                                                                                                                                                    |
| `POST /api/strategies`                        | Multi-year crop rotation plans for `{"crops": [...], "bounds"\|"fields": ...}` (same area shapes as `/api/crops`), with five scoring profiles, timeline, ratings, and comparisons. The first four plans include every selected crop and use no unselected crops; the balanced plan includes every selected crop and may add another. Rotation length expands when needed. Uses available FAO GAEZ suitability/yield, mapped crop-agronomy data, ERA5-based FAO-56 water-demand estimates, and country reference prices, plus SoilGrids/Sentinel context. The response includes `dataMode` and `assumptions`; the page exposes those assumptions. |

Season compatibility, suitability, irrigation, and user crop selection are enforced
when generating strategy candidates. Repeating crops or closely related crop families
is allowed but lowers the modeled agronomic-feasibility score via a rotation-gap
penalty. If no season-compatible candidate remains, the endpoint returns a 422 with
guidance to select a compatible crop or enable irrigation.
When three or four crops are selected, the first four objective-focused plans can
extend by one year and repeat a selected crop, allowing profit-, soil-, risk-, and
water-focused plans to differ while still using every selected crop and no
unselected crops. The balanced plan may additionally introduce another crop.
The strategy page fetches `/api/fields/strategy-context` and passes its result to the
strategy endpoint. GAEZ suitability affects feasibility. Crop calendars, families,
nitrogen fixation, rooting depth, drought tolerance, and humus balance use mapped
entries from `app/data/crop_agronomy.csv` (FAO-56, FAO-33, and VDLUFA references are
documented in `app/services/agronomy.py`); the strategy endpoint no longer reads the
undocumented `crop_soil_scores` table. Seasonal crop-water demand uses FAO-56 crop
coefficients with ERA5 monthly reference-ET normals when available. Eurostat/FAOSTAT
reference prices and national yields are used where available; crops without
published market figures or cost estimates are left out and flagged. GAEZ yields
inform suitability, not the economics calculation. The reference-price coverage
rating is not a measure of local buyer access. Seed and operating costs, generalized
risk ratings, and unmapped crop profiles remain estimates. SoilGrids and Sentinel-2 seasonal
context are included in the response's market considerations; where a validated
crop-response model is not available, these remain context rather than being
converted to unsupported thresholds.

SoilGrids chemistry and physical endpoints use up to five evenly distributed points
inside the requested rectangle and return the arithmetic mean of available point
predictions at each depth. The sampled points and per-depth sample counts are included
in the response; this is a sampled estimate, not a full raster average. Values use
SoilGrids' supported depth layers from 0–5 cm through 100–200 cm, with published
scale factors applied.
SoilGrids does not provide phosphorus, potassium, or drainage. The available depth
intervals describe the model profile rather than measured effective soil depth.

Timing logs: every Copernicus request, cache hit/miss and pipeline step is logged with its
duration and the plot it belongs to (`app/timing.py`). Set `LOG_LEVEL=INFO` to keep only the
per-plot and per-request summaries.

NDVI years default to 2023 to the current year. Sentinel-2 tiles more than 45% cloudy are skipped.
Per-year field data is cached in Postgres (`ndvi_year_cache`, created on first use), keyed by field id
(or an outline hash) plus the inward buffer: finished years are kept forever, the current year for a day.
The years of one plot are fetched in parallel, with at most 10 Copernicus requests in flight at once.

Crop suitability comes from FAO GAEZ v4, read straight from FAO's cloud-optimised GeoTIFFs (no key).
It is stored in 1° tiles (12 x 12 cells of ~9 km): `gaez_crop`, `gaez_layer` (GAEZ layer objectid,
GeoTIFF url), `gaez_tile`, `gaez_tile_fetch` and `gaez_tile_value` (144 values per tile and layer).
A missing tile loads its whole 10° region at once (~20–30 s; all of Bulgaria is one region), so
any later plot there is answered from Postgres. `POST /api/fields/suitability/prefetch` warms the
tiles in the background; the frontend calls it when a field is clicked. GAEZ is static, so nothing expires.

Crop selling prices: Eurostat `apri_ap_crpouta` (ex-farm, national, per 100 kg) is the main source;
FAOSTAT producer prices (public bulk file, USD/t, converted at Eurostat's yearly USD/EUR rate) fill
missing crops and years and cover non-EU countries. Prices are deflated to the latest HICP year with the
country's HICP where Eurostat publishes its prices, otherwise the euro area's. Confidence: Eurostat =
high, FAOSTAT official (`A`) = medium, other FAOSTAT flags = low. Crop codes live in
`app/data/crop_price_codes.csv` (one row per crop, nothing per country). Raw series are stored in
`price_crop`, `eurostat_crop_price`, `fao_producer_price`, `eurostat_fx`, `eurostat_hicp`, with fetch times
in `price_fetch` (Eurostat refreshed weekly, FAOSTAT monthly).

Sources: ERA5 via the Open-Meteo archive API (`models=era5`, no key), NASA POWER (no key),
Sentinel-2 via Sentinel Hub (`SH_CLIENT_ID`, `SH_CLIENT_SECRET`, `SH_DEPLOYMENT` in the
repo-root `.env` or `backend/.env`). Upstream responses (ERA5, NASA POWER, SoilGrids) are cached in Postgres
(`json_cache`, created on first use; `DATABASE_URL`); the first 44-year ERA5 request for an
area takes ~20–60 s. If the database is down, requests still work, uncached. To carry over an
old `backend/.cache/` folder: `.venv/bin/python -m app.import_file_cache`, then delete the folder.
