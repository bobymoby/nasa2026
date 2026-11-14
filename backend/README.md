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

## Sentinel Hub

Set `SH_CLIENT_ID`, `SH_CLIENT_SECRET` and optionally `SH_DEPLOYMENT` (`cdse` default, or `sh`)
in the repo-root `.env` or `backend/.env`. Endpoints (bbox is `min_lon,min_lat,max_lon,max_lat`):

- `GET /api/sentinel/search?bbox=23.2,42.6,23.4,42.75&from=2025-06-01&to=2025-06-30&max_cloud=20`
- `GET /api/sentinel/image?bbox=...&from=...&to=...&type=ndvi&format=tiff` (`type`: truecolor|ndvi, `format`: tiff|png|jpeg)
- `GET /api/sentinel/stats?bbox=...&from=2025-03-01&to=2025-10-31&interval=P10D&format=csv` (`format`: json|csv)
