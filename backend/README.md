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

| Endpoint                                       | What                                                                                                                                                                                                                                                                                                                                                                               |
| ---------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `GET /api/climate/daily`                       | Daily weather, `source=era5\|power`, optional `with_et0=true`                                                                                                                                                                                                                                                                                                                      |
| `GET /api/climate/heat-frost`                  | Test 1: July heat, wheat-flowering heat and April frost days per year, ERA5 vs POWER                                                                                                                                                                                                                                                                                               |
| `GET /api/drought`                             | Test 2: FAO-56 ET₀ + 150 mm bucket, seasonal shortfall and FAO-33 relative yield per crop/year                                                                                                                                                                                                                                                                                     |
| `GET /api/drought/season`                      | Daily bucket trace (soil water, Ks, ETa) for one crop and harvest year                                                                                                                                                                                                                                                                                                             |
| `GET /api/fields/ndvi`                         | One rectangular plot: period timeline from Sentinel-2 NDVI (2017+) as `{periods: [{start, end, type, crop_type, confidence}]}`; type is barren / crop / dormant_crop / green_cover; `detail=true` returns the full analysis                                                                                                                                                        |
| `POST /api/fields/ndvi`                        | Several plots. Either the frontend's picked fields, `{"fields": [{"id", "polygon": [{"lat", "lng"}, ...]}]}` (same shape as `/api/crops/fields`), or plain outlines, `{"plots": [[[lat, lon], ...], ...]}`; plus `fromYear`/`toYear` and the GET options (snake_case or camelCase). Returns one `{id?, periods}` body per plot in input order, with `error` for a plot that failed |
| `POST /api/fields/ndvi/prefetch`               | Same body; starts fetching the plots' Sentinel-2 data in the background and returns 202 at once. The frontend calls it when a field is picked                                                                                                                                                                                                                                      |
| `GET /api/sentinel/search` / `image` / `stats` | Raw Sentinel Hub Catalog / Process / Statistical API; `image?type=ndvi-color` renders NDVI as a coloured PNG                                                                                                                                                                                                                                                                       |
| `GET /api/soil/point?lat=&lng=`                | Most probable WRB soil groups at a point from ISRIC SoilGrids (250 m), cached in Postgres (`json_cache`)                                                                                                                                                                                                                                                                           |
| `GET /api/soil/chemistry?top_left=&bottom_right=` | Sampled rectangle mean of SoilGrids pH in water, nitrogen and soil organic carbon by depth                                                                                                                                                                                                                                                                                     |
| `GET /api/soil/physical?top_left=&bottom_right=`  | Sampled rectangle mean of SoilGrids clay, sand, silt and bulk density by depth                                                                                                                                                                                                                                                                                                |
| `GET /api/sentinel/soil-characteristics`       | Sentinel Hub Statistical API spectral indicators and historical series                                                                                                                                                                                                                                                                                                             |
| `POST /api/strategies`                        | Multi-year crop rotation plans for `{"crops": [...], "bounds"\|"fields": ...}` (same area shapes as `/api/crops`), best match first, with timeline, ratings, erosion risk, water use per year, crop-role importance, market notes and estimated cost/profit per ha and for the area (2026 market yields, prices and seed costs plus estimated other costs, hard-coded in `app/services/strategy_economics.py`). Plans are **hard-coded for now** (`app/services/strategies.py`) |

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

Sources: ERA5 via the Open-Meteo archive API (`models=era5`, no key), NASA POWER (no key),
Sentinel-2 via Sentinel Hub (`SH_CLIENT_ID`, `SH_CLIENT_SECRET`, `SH_DEPLOYMENT` in the
repo-root `.env` or `backend/.env`). Upstream responses (ERA5, NASA POWER, SoilGrids) are cached in Postgres
(`json_cache`, created on first use; `DATABASE_URL`); the first 44-year ERA5 request for an
area takes ~20–60 s. If the database is down, requests still work, uncached. To carry over an
old `backend/.cache/` folder: `.venv/bin/python -m app.import_file_cache`, then delete the folder.
