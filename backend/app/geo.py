"""Rectangle input shared by every data endpoint.

All endpoints take the area of interest as two corners:
    top_left=<lat>,<lon>       north-west corner
    bottom_right=<lat>,<lon>   south-east corner
"""

import math
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Query


@dataclass(frozen=True)
class Rect:
    north: float
    west: float
    south: float
    east: float

    @property
    def bbox(self) -> list[float]:
        """[min_lon, min_lat, max_lon, max_lat], the order Sentinel Hub expects."""
        return [self.west, self.south, self.east, self.north]

    @property
    def center(self) -> tuple[float, float]:
        return ((self.north + self.south) / 2, (self.west + self.east) / 2)

    def grid(self, step_deg: float, max_points: int) -> list[tuple[float, float]]:
        """(lat, lon) sample points spaced ~step_deg apart covering the rectangle.

        Used for gridded point-based sources (ERA5 0.25°, POWER 0.5°): a
        rectangle smaller than one grid cell collapses to its centre.
        """
        n_lat = max(1, math.ceil((self.north - self.south) / step_deg))
        n_lon = max(1, math.ceil((self.east - self.west) / step_deg))
        while n_lat * n_lon > max_points:
            if n_lat >= n_lon:
                n_lat -= 1
            else:
                n_lon -= 1
        dlat = (self.north - self.south) / n_lat
        dlon = (self.east - self.west) / n_lon
        return [
            (round(self.south + (i + 0.5) * dlat, 4), round(self.west + (j + 0.5) * dlon, 4))
            for i in range(n_lat)
            for j in range(n_lon)
        ]

    def as_dict(self) -> dict:
        return {
            "top_left": [self.north, self.west],
            "bottom_right": [self.south, self.east],
        }


def _parse_corner(name: str, raw: str) -> tuple[float, float]:
    try:
        lat, lon = (float(v) for v in raw.split(","))
    except ValueError:
        raise HTTPException(422, f"{name} must be '<lat>,<lon>'")
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise HTTPException(422, f"{name} is out of range")
    return lat, lon


def rect_query(
    top_left: Annotated[
        str,
        Query(description="North-west corner as `lat,lon`", examples=["43.60,27.80"]),
    ],
    bottom_right: Annotated[
        str,
        Query(description="South-east corner as `lat,lon`", examples=["43.55,27.86"]),
    ],
) -> Rect:
    north, west = _parse_corner("top_left", top_left)
    south, east = _parse_corner("bottom_right", bottom_right)
    if north <= south:
        raise HTTPException(422, "top_left latitude must be north of bottom_right")
    if west >= east:
        raise HTTPException(422, "top_left longitude must be west of bottom_right")
    return Rect(north, west, south, east)


RectQuery = Annotated[Rect, Depends(rect_query)]
