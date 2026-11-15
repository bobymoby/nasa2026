# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Code comments

Do not leave comments except where EXTREMELY necessary: a non-obvious constraint, a workaround, or a "why" the code cannot say itself. When reworking code, remove any unnecessary comments in the parts you touch.

## Project

Field planner for Bulgarian farmland (NASA challenge). Users pick fields or draw a rectangle on a map, then see crop suitability, Sentinel-2 NDVI periods, soil and climate data. `backend/` is FastAPI (Python 3.12), `frontend/` is Create React App (React 19, TypeScript, Mapbox GL, SCSS). The VS Code task `dev: all` (`.vscode/tasks.json`) starts both.

## Commands

Backend (run from `backend/`, virtualenv at `backend/.venv`):

```sh
.venv/bin/pip install -r requirements.txt
.venv/bin/fastapi dev app/main.py                      # http://localhost:8000, docs at /docs
.venv/bin/python -m unittest discover -s tests -p "test_*.py"
.venv/bin/python -m unittest tests.test_soilgrids.SoilGridsEndpointTests   # single test class
```

Frontend (run from `frontend/`):

```sh
npm ci            # needed after pulls that change package.json (e.g. sass)
npm start         # http://localhost:3000
npx tsc --noEmit -p .
npx prettier --write --no-semi --tab-width 4 <files>   # no config file; this matches the existing style
```

Database: `docker-compose.yml` runs PostGIS locally. On Apple Silicon, do not edit the compose platform; override it instead:

```sh
DOCKER_DEFAULT_PLATFORM=linux/amd64 docker compose up -d db
```

## Configuration

- Settings live in `backend/app/config.py` (pydantic-settings). It reads the repo-root `.env`, then `backend/.env`; the later file wins.
- `backend/.env` is git-ignored and holds `DATABASE_URL` (the shared team database).
- Never drop tables or schemas from app code or scripts. When a table becomes obsolete, leave it in place and stop using it; deleting is a decision for the team.
- The root `.env` holds Sentinel Hub credentials and is tracked in git. Never commit changes to it.
- Sentinel Hub credentials are read from `DANNY_SH_CLIENT_ID` / `DANNY_SH_CLIENT_SECRET` through `validation_alias`. This is temporary.
- The frontend reads `REACT_APP_API_URL` (default `http://localhost:8000`). Backend CORS allows only `http://localhost:3000` unless `CORS_ORIGINS` is set.

## Backend architecture

- **Layers.** `app/main.py` mounts every router under `/api`. Routers in `app/routers/` validate input and shape responses; the work lives in `app/services/`.
- **Route order in `fields.py`.** `GET /fields/{field_id}` is registered last on purpose. It would otherwise capture `/fields/ndvi`, `/fields/suitability` and similar. New `/fields/...` routes go above it.
- **Shared plot input.** `PlotsInput` in `routers/fields.py` takes `plots` (lists of `[lat, lon]` or `{lat, lng}`) and/or `fields` (`{id, polygon}`, the shape the frontend sends). Its `outlines()` method merges both into one list. `POST /fields/ndvi`, `/fields/ndvi/prefetch` and `/fields/suitability` all extend it, and each returns one body per plot in input order, with `error` set on any plot that failed.
- **camelCase.** Request models use `alias_generator=to_camel, populate_by_name=True`, so both snake_case and camelCase keys are accepted.
- **Geometry.** Rectangles are `RectQuery` / `Rect` in `app/geo.py` (`top_left`, `bottom_right` as `lat,lon`). Outlines become shapely polygons in lon/lat via `plot_polygon` in `services/field_ndvi.py`.
- **Upstream sources:**
  - Sentinel Hub / Copernicus: `services/sentinel_hub.py`. A semaphore allows at most 10 requests in flight.
  - ERA5 via Open-Meteo, and NASA POWER: `services/weather.py`.
  - ISRIC SoilGrids: `services/soilgrids.py` (REST, soil properties) and `services/wrb.py` (WRB soil groups for `/soil/point`, read from the rasters on files.isric.org instead of the rate-limited REST API).
  - FAO GAEZ v4 ImageServer: `services/gaez.py`.
  - FAO ECOCROP: `app/data/ecocrop.csv`, used by `services/crop_suitability.py`.
  - Crop selling prices: `services/prices/` (`sources.py` fetch/parse, `store.py` tables, `pipeline.py` merge, deflate, summarize). Eurostat `apri_ap_crpouta` first, FAOSTAT bulk file as fallback. Crop codes are in `app/data/crop_price_codes.csv`, keyed by the same crop ids as the crops page (`wheat`, `dry_pea`, …); adding a country needs no code change.
- **Caching. Everything is cached in Postgres; there are no file caches:**
  - `app/cache.py` provides `cached_json(namespace, key, fetch)`. It stores generic upstream responses in the `json_cache` table.
  - `services/ndvi_cache.py` stores per-year NDVI observations in `ndvi_year_cache`, keyed by field id or outline hash plus the inward buffer. Finished years are kept forever; the current year expires after 1 day. Concurrent fetches of the same plot and year are deduplicated.
  - `services/gaez.py` reads FAO's GeoTIFFs with rasterio and stores 1° tiles (`gaez_tile`, `gaez_tile_fetch`, `gaez_tile_value`, plus `gaez_crop`, `gaez_layer`). A missing tile loads its whole 10° region. Nothing is cached per plot.
  - `services/site_context.py` stores static per-plot inputs (topsoil, available water, RUSLE K, slope) in `plot_site`, keyed like the NDVI cache; everything else in the context is derived from the caches above. Country comes from `services/country.py` (bundled Natural Earth borders).
  - `services/wrb.py` stores the top 3 WRB groups per cell in 256-cell tiles (`soilgrids_wrb_tile`). Bulgaria is preloaded; a missing tile is read from ISRIC on first use. `/soil/legend` counts the map legend from these tiles, clipped to Bulgaria; only the soil map layer itself still comes from the ISRIC WMS.
  - Tables are created lazily with `ensure_table` in `app/db.py`.
  - Every cache must degrade gracefully: if the database is down, the request still succeeds, uncached.
- **Field parcels.** Field outlines come from the PostGIS table `bulgaria_data` (EPSG:3035). The map loads them as vector tiles (`/fields/tiles/...`) and fetches one full field on click.
- **Timing logs.** `app/timing.py` provides `timed()` / `emit()`, which tag each log line with the current plot via a `ContextVar`. Worker threads need `contextvars.copy_context().run` to keep the tag. `LOG_LEVEL` defaults to DEBUG.

## Frontend architecture

- **App state.** `src/Router.tsx` holds the app state: the drawn `bounds`, `selectedFields` and `selectedCrops`. It turns these into an `AreaSelection` (`src/data/point.ts`), which is either a rectangle or a set of fields, never both. That selection is passed to `/crops` (`CropsPage`).
- **API clients.** Each endpoint has a client function in `src/api/`. For example, `fetchCrops.ts` calls `/api/fields/suitability`: it splits a rectangle along the ~9 km GAEZ grid and averages each crop over the area.
- **Map.** `components/Map/Map.tsx` owns Mapbox, the field tile layer, the soil WMS layer and the right-click soil popup. A clicked field is selected at once from its vector-tile feature. The full outline (`GET /fields/{id}`), the NDVI prefetch and the suitability prefetch run in the background; the UI never waits on them.
- **Styles.** SCSS files import shared tokens and mixins from `src/styles/` with `@use "../../styles/tokens" as *;`.
