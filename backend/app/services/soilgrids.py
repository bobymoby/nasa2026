"""Point properties from ISRIC SoilGrids."""

import math
from typing import Any

import httpx

from app.cache import cached_json
from shapely.geometry import Polygon

from app.geo import Rect
from app.services.soilgrids_store import stored_layers

SOILGRIDS_PROPERTIES_URL = "https://rest.isric.org/soilgrids/v2.0/properties/query"
SOILGRIDS_RESOLUTION_M = 250
MAX_RECTANGLE_SAMPLES = 5
TIMEOUT_STATUS = 504
TIMEOUT_RETRIES = 1
DEPTH_INTERVALS = (
    "0-5cm",
    "5-15cm",
    "15-30cm",
    "30-60cm",
    "60-100cm",
    "100-200cm",
)

CHEMISTRY_PROPERTIES = {
    "phh2o": ("pH_water", "pH"),
    "nitrogen": ("nitrogen", "g/kg"),
    "soc": ("organic_carbon", "g/kg"),
}
PHYSICAL_PROPERTIES = {
    "clay": ("clay", "%"),
    "sand": ("sand", "%"),
    "silt": ("silt", "%"),
    "bdod": ("bulk_density", "kg/dm3"),
}
SELECTION_PROPERTIES = {**CHEMISTRY_PROPERTIES, **PHYSICAL_PROPERTIES}


class SoilGridsError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def _property_values(
    layers: list[dict[str, Any]], property_name: str, output_unit: str
) -> dict[str, Any]:
    layer = next((item for item in layers if item.get("name") == property_name), None)
    if layer is None:
        raise SoilGridsError(f"SoilGrids response is missing {property_name}")

    try:
        factor = float(layer["unit_measure"]["d_factor"])
        depths = layer["depths"]
    except (KeyError, TypeError, ValueError) as exc:
        raise SoilGridsError(f"Malformed SoilGrids response for {property_name}") from exc
    if factor <= 0:
        raise SoilGridsError(f"Invalid SoilGrids scale factor for {property_name}")

    values_by_depth = {}
    for label in DEPTH_INTERVALS:
        depth = next((item for item in depths if item.get("label") == label), None)
        value = depth.get("values", {}).get("mean") if depth else None
        try:
            values_by_depth[label] = round(float(value) / factor, 3) if value is not None else None
        except (TypeError, ValueError) as exc:
            raise SoilGridsError(
                f"Malformed SoilGrids value for {property_name} at {label}"
            ) from exc

    return {"unit": output_unit, "values_by_depth": values_by_depth}


def _fetch_properties(lat: float, lng: float, property_names: tuple[str, ...]) -> dict:
    params = [
        ("lon", lng),
        ("lat", lat),
        *(("property", name) for name in property_names),
        *(("depth", depth) for depth in DEPTH_INTERVALS),
        ("value", "mean"),
    ]
    try:
        response = httpx.get(SOILGRIDS_PROPERTIES_URL, params=params, timeout=60)
    except httpx.TimeoutException as exc:
        raise SoilGridsError(f"SoilGrids request failed: {exc}", TIMEOUT_STATUS) from exc
    except httpx.HTTPError as exc:
        raise SoilGridsError(f"SoilGrids request failed: {exc}") from exc
    if response.status_code == 429:
        raise SoilGridsError("SoilGrids rate limit reached; try again later", 429)
    if response.status_code != 200:
        raise SoilGridsError(f"SoilGrids returned HTTP {response.status_code}", 502)
    try:
        data = response.json()
        layers = data["properties"]["layers"]
    except (ValueError, KeyError, TypeError) as exc:
        raise SoilGridsError("Malformed SoilGrids response") from exc
    if not isinstance(layers, list):
        raise SoilGridsError("Malformed SoilGrids response: layers must be a list")
    return {"layers": layers}


def _cached_properties(lat: float, lng: float, property_names: tuple[str, ...]) -> list:
    stored = stored_layers(lat, lng, property_names)
    if stored is not None:
        return stored
    key = {"lat": lat, "lng": lng, "properties": property_names}
    result = cached_json(
        "soilgrids_properties",
        key,
        lambda: _fetch_properties(lat, lng, property_names),
    )
    return result["layers"]


def _rectangle_sample_points(rect: Rect) -> list[tuple[float, float]]:
    center_latitude = (rect.north + rect.south) / 2
    lat_step = SOILGRIDS_RESOLUTION_M / 111_000
    lon_step = lat_step / max(abs(math.cos(math.radians(center_latitude))), 0.01)
    lat_count = max(1, math.ceil((rect.north - rect.south) / lat_step))
    lon_count = max(1, math.ceil((rect.east - rect.west) / lon_step))
    aspect_ratio = lat_count / lon_count
    sample_dimensions = min(
        (
            (sample_lats, sample_lons)
            for sample_lats in range(1, min(lat_count, MAX_RECTANGLE_SAMPLES) + 1)
            for sample_lons in range(1, min(lon_count, MAX_RECTANGLE_SAMPLES) + 1)
            if sample_lats * sample_lons <= MAX_RECTANGLE_SAMPLES
        ),
        key=lambda dimensions: (
            abs(math.log((dimensions[0] / dimensions[1]) / aspect_ratio)),
            -(dimensions[0] * dimensions[1]),
        ),
    )
    lat_count, lon_count = sample_dimensions

    return [
        (
            round(rect.south + (i + 0.5) * (rect.north - rect.south) / lat_count, 6),
            round(rect.west + (j + 0.5) * (rect.east - rect.west) / lon_count, 6),
        )
        for i in range(lat_count)
        for j in range(lon_count)
    ]


def _aggregate_properties(
    samples: list[list[dict[str, Any]]],
    mapping: dict[str, tuple[str, str]],
) -> dict[str, Any]:
    result = {}
    for source_name, (public_name, unit) in mapping.items():
        sample_values = [
            _property_values(sample, source_name, unit)["values_by_depth"]
            for sample in samples
        ]
        values_by_depth = {}
        sample_counts_by_depth = {}
        for depth in DEPTH_INTERVALS:
            values = [values[depth] for values in sample_values if values[depth] is not None]
            values_by_depth[depth] = round(sum(values) / len(values), 3) if values else None
            sample_counts_by_depth[depth] = len(values)
        result[public_name] = {
            "unit": unit,
            "values_by_depth": values_by_depth,
            "sample_counts_by_depth": sample_counts_by_depth,
        }
    return result


def _rectangle_properties(
    rect: Rect, property_names: tuple[str, ...]
) -> tuple[list[tuple[float, float]], list[list[dict[str, Any]]]]:
    points = _rectangle_sample_points(rect)
    samples = [
        _cached_properties(lat, lng, property_names)
        for lat, lng in points
    ]
    return points, samples


def _rectangle_metadata(rect: Rect, points: list[tuple[float, float]]) -> dict[str, Any]:
    return {
        "source": "ISRIC SoilGrids",
        "spatial_resolution_m": SOILGRIDS_RESOLUTION_M,
        "location": {
            "top_left": [rect.north, rect.west],
            "bottom_right": [rect.south, rect.east],
        },
        "sampling": {
            "method": "evenly_spaced_grid_points",
            "sample_count": len(points),
            "max_sample_count": MAX_RECTANGLE_SAMPLES,
            "sampled_points": [{"lat": lat, "lng": lng} for lat, lng in points],
            "aggregation": "arithmetic mean of available point estimates by depth",
        },
    }


def get_soil_chemistry(rect: Rect) -> dict[str, Any]:
    names = tuple(CHEMISTRY_PROPERTIES)
    points, samples = _rectangle_properties(rect, names)
    return {
        **_rectangle_metadata(rect, points),
        "properties": _aggregate_properties(samples, CHEMISTRY_PROPERTIES),
    }


def get_soil_physical(rect: Rect) -> dict[str, Any]:
    names = tuple(PHYSICAL_PROPERTIES)
    points, samples = _rectangle_properties(rect, names)
    return {
        **_rectangle_metadata(rect, points),
        "depth_intervals_cm": list(DEPTH_INTERVALS),
        "properties": _aggregate_properties(samples, PHYSICAL_PROPERTIES),
        "note": (
            "Depth intervals describe the SoilGrids profile layers; they are not "
            "a measurement of effective soil depth."
        ),
    }


def _field_sample_points(polygons: list[Polygon]) -> list[tuple[float, float]]:
    points = [
        (point.y, point.x)
        for point in (polygon.representative_point() for polygon in polygons)
    ]
    if len(points) <= MAX_RECTANGLE_SAMPLES:
        return points
    indexes = [
        round(i * (len(points) - 1) / (MAX_RECTANGLE_SAMPLES - 1))
        for i in range(MAX_RECTANGLE_SAMPLES)
    ]
    return [points[index] for index in indexes]


def _sample_points(
    points: list[tuple[float, float]], property_names: tuple[str, ...]
) -> tuple[list[list[dict[str, Any]]], list[tuple[float, float]]]:
    """Samples for the points SoilGrids answered, plus the points it didn't.

    A timed-out point is retried once; the area is described from the points
    that answered, and only fails when none did."""
    samples, failed, first_error = [], [], None
    for lat, lng in points:
        for attempt in range(1 + TIMEOUT_RETRIES):
            try:
                samples.append(_cached_properties(lat, lng, property_names))
                break
            except SoilGridsError as exc:
                if exc.status_code == TIMEOUT_STATUS and attempt < TIMEOUT_RETRIES:
                    continue
                first_error = first_error or exc
                failed.append((lat, lng))
                break
    if not samples and first_error is not None:
        raise first_error
    return samples, failed


def get_soil_selection(
    rect: Rect | None = None,
    polygons: list[Polygon] | None = None,
) -> dict[str, Any]:
    """Sample combined SoilGrids chemistry and physical properties for an area."""
    if rect is not None:
        points = _rectangle_sample_points(rect)
        method = "evenly_spaced_grid_points"
        location = {
            "top_left": [rect.north, rect.west],
            "bottom_right": [rect.south, rect.east],
        }
        selected_field_count = None
    elif polygons:
        points = _field_sample_points(polygons)
        method = "field_representative_points"
        location = None
        selected_field_count = len(polygons)
    else:
        raise ValueError("provide a rectangle or at least one field polygon")

    samples, failed = _sample_points(points, tuple(SELECTION_PROPERTIES))
    sampling: dict[str, Any] = {
        "method": method,
        "sample_count": len(samples),
        "failed_sample_count": len(failed),
        "max_sample_count": MAX_RECTANGLE_SAMPLES,
        "sampled_points": [{"lat": lat, "lng": lng} for lat, lng in points],
        "aggregation": "arithmetic mean of available point estimates by depth",
    }
    if selected_field_count is not None:
        sampling["selected_field_count"] = selected_field_count

    result = {
        "source": "ISRIC SoilGrids",
        "spatial_resolution_m": SOILGRIDS_RESOLUTION_M,
        "sampling": sampling,
        "depth_intervals_cm": list(DEPTH_INTERVALS),
        "properties": _aggregate_properties(samples, SELECTION_PROPERTIES),
        "note": (
            "SoilGrids values are modeled estimates at approximately 250 m "
            "resolution, not measurements from the selected fields."
            + (f" {len(failed)} of {len(points)} sample points could not be read."
               if failed else "")
        ),
    }
    if location is not None:
        result["location"] = location
    return result


TOPSOIL_DEPTHS = {"0-5cm": 5, "5-15cm": 10, "15-30cm": 15}
TOPSOIL_PROPERTIES = {"sand": "%", "clay": "%", "soc": "g/kg"}


def topsoil(lat: float, lng: float) -> dict[str, float] | None:
    """Depth-weighted 0-30 cm sand %, clay % and organic carbon g/kg at a point; None where SoilGrids has no soil."""
    layers = _cached_properties(round(lat, 4), round(lng, 4), tuple(TOPSOIL_PROPERTIES))
    out = {}
    for name, unit in TOPSOIL_PROPERTIES.items():
        by_depth = _property_values(layers, name, unit)["values_by_depth"]
        pairs = [(by_depth[d], w) for d, w in TOPSOIL_DEPTHS.items() if by_depth.get(d) is not None]
        if not pairs:
            return None
        out[name] = round(sum(v * w for v, w in pairs) / sum(w for _, w in pairs), 2)
    return out
