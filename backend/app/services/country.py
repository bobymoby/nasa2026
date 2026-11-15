"""ISO 3166 alpha-2 country at a point, from bundled Natural Earth borders."""

import json
from functools import cache
from pathlib import Path

from shapely import STRtree
from shapely.geometry import Point, shape

BORDERS = Path(__file__).resolve().parents[1] / "data" / "countries.geojson"
COAST_TOLERANCE_DEG = 0.2


@cache
def _index() -> tuple[STRtree, list[str]]:
    features = json.loads(BORDERS.read_text())["features"]
    return STRtree([shape(f["geometry"]) for f in features]), [f["properties"]["code"] for f in features]


def country_at(lat: float, lon: float) -> str | None:
    tree, codes = _index()
    point = Point(lon, lat)
    inside = tree.query(point, predicate="intersects")
    if len(inside):
        return codes[int(inside[0])]
    nearest = tree.query_nearest(point, max_distance=COAST_TOLERANCE_DEG)
    return codes[int(nearest[0])] if len(nearest) else None
