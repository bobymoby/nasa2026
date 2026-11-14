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

Every endpoint takes the area as two corners, `top_left=<lat>,<lon>` (north-west) and
`bottom_right=<lat>,<lon>` (south-east). Full parameter docs: http://localhost:8000/docs

| Endpoint | What |
| --- | --- |
| `GET /api/climate/daily` | Daily weather, `source=era5\|power`, optional `with_et0=true` |
| `GET /api/climate/heat-frost` | Test 1: July heat, wheat-flowering heat and April frost days per year, ERA5 vs POWER |
| `GET /api/drought` | Test 2: FAO-56 ET₀ + 150 mm bucket, seasonal shortfall and FAO-33 relative yield per crop/year |
| `GET /api/drought/season` | Daily bucket trace (soil water, Ks, ETa) for one crop and harvest year |
| `GET /api/sentinel/field-ndvi` | Test 3: clear-sky field NDVI curves 2017+, peak month, bare months, crop guess |
| `GET /api/sentinel/search` / `image` / `stats` | Raw Sentinel Hub Catalog / Process / Statistical API |

Sources: ERA5 via the Open-Meteo archive API (`models=era5`, no key), NASA POWER (no key),
Sentinel-2 via Sentinel Hub (`SH_CLIENT_ID`, `SH_CLIENT_SECRET`, `SH_DEPLOYMENT` in the
repo-root `.env` or `backend/.env`). Upstream responses are cached in `backend/.cache/`;
the first 44-year ERA5 request for an area takes ~20–60 s.
