"""Site data the rotation strategies are scored with, for the selected area.

- FAO GAEZ v4 rainfed suitability (1981-2010, high input), averaged over the
  ~9 km cells the area touches.
- ERA5 1995-2024 normals (`site_context.climate_normals`): frost dates, which
  shift the sowing calendar, and monthly reference evapotranspiration, which
  drives FAO-56 crop water demand.

A source that fails is left out (None) and reported in `warnings`; nothing is
filled in by hand.
"""

import logging
import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from app.geo import Rect
from app.services import agronomy, gaez
from app.services.site_context import climate_normals

log = logging.getLogger(__name__)

# GAEZ crop code -> strategy crop id
GAEZ_CROPS = {
    "whe": "wheat", "brl": "barley", "mze": "maize", "sfl": "sunflower",
    "rsd": "rapeseed", "chk": "chickpea", "soy": "soybean", "pea": "field_pea",
    "oat": "oat", "flx": "flax", "bck": "buckwheat", "rye": "rye",
    "srg": "sorghum", "car": "carrot",
}
GAEZ_INPUT_LEVEL = "high"
MAX_CELLS = 50


@dataclass(frozen=True)
class SiteInputs:
    # strategy crop id -> 0-100; empty when GAEZ is unavailable
    suitability: dict[str, float]
    frost: agronomy.Frost | None
    # calendar month -> mm; None when ERA5 is unavailable
    et0_monthly_mm: dict[int, float] | None
    warnings: list[str]


def cells_in_rect(rect: Rect) -> set[gaez.Cell]:
    north_west, south_east = gaez.Cell.at(rect.north, rect.west), gaez.Cell.at(rect.south, rect.east)
    cells = [
        gaez.Cell(row, col)
        for row in range(north_west.row, south_east.row + 1)
        for col in range(north_west.col, south_east.col + 1)
    ]
    step = max(1, math.ceil(len(cells) / MAX_CELLS))
    return set(cells[::step])


def cells_at(points: list[tuple[float, float]]) -> set[gaez.Cell]:
    cells = sorted({gaez.Cell.at(lat, lon) for lat, lon in points}, key=lambda c: (c.row, c.col))
    step = max(1, math.ceil(len(cells) / MAX_CELLS))
    return set(cells[::step])


def _suitability(cells: set[gaez.Cell]) -> dict[str, float]:
    values = gaez.cells_values(GAEZ_INPUT_LEVEL, cells)
    per_cell = []
    for got in values.values():
        if isinstance(got, gaez.GaezError) or not got:
            continue
        per_cell.append({
            GAEZ_CROPS[c["code"]]: c["suitability"]
            for c in gaez.crop_list(GAEZ_INPUT_LEVEL, got, "not_suitable", None)
            if c["code"] in GAEZ_CROPS
        })
    if not per_cell:
        return {}
    # a crop missing from a cell has no suitability there
    return {
        crop_id: round(sum(cell.get(crop_id, 0.0) for cell in per_cell) / len(per_cell), 1)
        for crop_id in GAEZ_CROPS.values()
    }


def _climate(rect: Rect) -> tuple[agronomy.Frost | None, dict[int, float] | None]:
    normals = climate_normals(rect)
    frost = None
    if normals["frost"]:
        spring, autumn = (
            tuple(int(part) for part in normals["frost"][key].split("-"))
            for key in ("last_spring", "first_autumn")
        )
        frost = agronomy.Frost(spring, autumn)
    et0 = {m["month"]: m["et0_mm"] for m in normals["months"] if m["et0_mm"] is not None}
    return frost, et0 if len(et0) == 12 else None


def site_inputs(cells: set[gaez.Cell], rect: Rect) -> SiteInputs:
    warnings: list[str] = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        suitability_job = pool.submit(_suitability, cells)
        climate_job = pool.submit(_climate, rect)
        try:
            suitability = suitability_job.result()
        except Exception as e:
            log.warning("strategy inputs: GAEZ failed: %s", e)
            suitability = {}
        try:
            frost, et0 = climate_job.result()
        except Exception as e:
            log.warning("strategy inputs: ERA5 failed: %s", e)
            frost, et0 = None, None
    if not suitability:
        warnings.append("FAO GAEZ suitability is unavailable for this area; crops are not filtered by suitability.")
    if frost is None:
        warnings.append("ERA5 frost dates are unavailable; sowing dates follow the standard Bulgarian calendar.")
    if et0 is None:
        warnings.append("ERA5 evapotranspiration is unavailable; crop water demand is not estimated.")
    return SiteInputs(suitability, frost, et0, warnings)
