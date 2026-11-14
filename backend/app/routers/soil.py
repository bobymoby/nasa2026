"""Soil type at a point from ISRIC SoilGrids (WRB reference soil groups, 250 m).

The map draws the soil layer straight from ISRIC's WMS; this endpoint only
answers "what is here" on click. The SoilGrids REST API is slow (~4 s) and
rate-limited (about 5 requests a minute), so answers are cached in Postgres.
"""

from typing import Annotated

import httpx
from fastapi import APIRouter, HTTPException, Query

from app.cache import cached_json

router = APIRouter(prefix="/soil", tags=["soil"])

SOILGRIDS_CLASSIFICATION_URL = "https://rest.isric.org/soilgrids/v2.0/classification/query"
# SoilGrids cells are 250 m, so ~100 m rounding still hits the same cell and
# lets nearby clicks share a cache entry
COORD_DECIMALS = 3


@router.get("/point", summary="Most probable WRB soil groups at a point (SoilGrids)")
def soil_at_point(
    lat: Annotated[float, Query(ge=-90, le=90)],
    lng: Annotated[float, Query(ge=-180, le=180)],
    classes: Annotated[int, Query(ge=1, le=10)] = 3,
) -> dict:
    lat, lng = round(lat, COORD_DECIMALS), round(lng, COORD_DECIMALS)

    def fetch() -> dict:
        try:
            r = httpx.get(
                SOILGRIDS_CLASSIFICATION_URL,
                params={"lat": lat, "lon": lng, "number_classes": classes},
                timeout=60,
            )
        except httpx.HTTPError as e:
            raise HTTPException(502, f"SoilGrids request failed: {e}")
        if r.status_code == 429:
            raise HTTPException(503, "SoilGrids rate limit reached, try again in a minute")
        if r.status_code != 200:
            raise HTTPException(502, f"SoilGrids returned {r.status_code}")
        data = r.json()
        return {
            "lat": lat,
            "lng": lng,
            # null over sea, lakes and built-up areas
            "soil": data.get("wrb_class_name"),
            "probabilities": [
                {"soil": name, "probability": p}
                for name, p in data.get("wrb_class_probability") or []
                # off-land points list placeholder classes with null probability
                if p is not None
            ],
        }

    return cached_json("soilgrids", {"lat": lat, "lng": lng, "classes": classes}, fetch)
