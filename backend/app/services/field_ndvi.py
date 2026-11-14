"""Field greenness pipeline (Sentinel-2 NDVI).

    1. fetch    one Statistical API request per year, one value per acquisition,
                inside the field rectangle shrunk inwards by a buffer;
                only SCL vegetation (4) and not-vegetated/bare soil (5) pixels count
    2. clean    drop dates with < min_valid clear pixels, 3-point running median
                (spikes = missed clouds), daily interpolation, Savitzky-Golay
                smoothing, flag gaps longer than gap_days as uncertain
    3. label    hysteresis thresholds (bare < 0.20, crop > 0.35, held 10 days),
                winter dormancy, green cover after harvest, double cropping
    4. events   per season: emergence, peak, harvest (cliff vs gradual), and a
                list of periods: crop / dormant_crop / bare / green_cover / uncertain
"""

import math
from dataclasses import dataclass, replace
from datetime import date, timedelta

import numpy as np
from scipy.signal import savgol_filter
from shapely.geometry import Polygon, mapping
from shapely.validation import make_valid
from shapely.ops import transform

from app.cache import cached_json
from app.geo import Rect
from app.services.sentinel_hub import SentinelHub

S2_FIRST_YEAR = 2017  # Sentinel-2 L2A is complete over Europe from 2017
EVALSCRIPT_VERSION = "field-v1"  # bump when the evalscript changes (cache key)

EVAL_FIELD = """//VERSION=3
function setup() {
  return {
    input: [{ bands: ["B04", "B08", "B11", "SCL", "dataMask"] }],
    output: [
      { id: "ndvi", bands: 1, sampleType: "FLOAT32" },
      { id: "b11", bands: 1, sampleType: "FLOAT32" },
      { id: "dataMask", bands: 1 }
    ]
  };
}
function evaluatePixel(s) {
  // keep vegetation (4) and not-vegetated / bare soil (5); drop no-data,
  // saturated, dark, cloud shadow, water, unclassified, clouds, cirrus, snow
  const ok = s.dataMask == 1 && (s.SCL == 4 || s.SCL == 5);
  const ndvi = (s.B08 - s.B04) / (s.B08 + s.B04);
  return { ndvi: [ndvi], b11: [s.B11], dataMask: [ok ? 1 : 0] };
}"""

M_PER_DEG_LAT = 110_574.0
M_PER_DEG_LON_EQUATOR = 111_320.0


@dataclass(frozen=True)
class Params:
    min_valid: float = 0.7  # min share of field pixels that must be clear
    bare_ndvi: float = 0.20  # below: bare
    crop_ndvi: float = 0.35  # above: crop present
    hold_days: int = 10  # hysteresis: days a crossing must hold
    gap_days: int = 30  # longer gaps without clear images are uncertain
    crop_peak_min: float = 0.5  # a cash crop peaks at least this high
    harvest_days: int = 14  # high -> low within this many days = harvest cliff
    double_drop: float = 0.25  # NDVI drop that splits two crops in one season
    spike_drop: float = 0.15  # dip below both neighbours that marks a missed cloud
    sg_window: int = 21  # Savitzky-Golay window, days (odd)
    sg_order: int = 2
    auto_thresholds: bool = True  # adapt bare/crop lines to this field
    fill_gaps: bool = True  # long cloud gaps take the state of the period before
    detect_dormancy: bool = True  # low winter period of an autumn-sown crop = dormant


def field_thresholds(d: "Daily", p: Params) -> Params:
    """Adapt the lines to this field.

    Bare: just above the field's own low baseline (soil colour, margins and
    stubble differ between fields). Crop: 50% of the field's seasonal
    amplitude, the usual start/end-of-season rule in phenology tools.
    """
    if not p.auto_thresholds:
        return p
    baseline = float(np.percentile(d.smooth, 10))
    top = float(np.percentile(d.smooth, 95))
    bare = min(0.30, max(p.bare_ndvi, baseline + 0.05))
    crop = max(p.crop_ndvi, bare + 0.15, baseline + 0.5 * (top - baseline))
    return replace(p, bare_ndvi=round(bare, 3), crop_ndvi=round(crop, 3))


# ---------------------------------------------------------------- 1. fetch


def shrink(rect: Rect, buffer_m: float) -> Rect:
    """Move every edge inwards so roads, hedges and neighbours don't leak in."""
    lat = math.radians(rect.center[0])
    dlat = buffer_m / M_PER_DEG_LAT
    dlon = buffer_m / (M_PER_DEG_LON_EQUATOR * math.cos(lat))
    inner = Rect(rect.north - dlat, rect.west + dlon, rect.south + dlat, rect.east - dlon)
    if inner.north <= inner.south or inner.west >= inner.east:
        raise ValueError(f"rectangle is too small for a {buffer_m:g} m inward buffer")
    return inner


def pixel_size(bbox: list[float]) -> tuple[float, float]:
    """10 m pixels in degrees, coarsened if the area exceeds 2500 px a side."""
    west, south, east, north = bbox
    lat = math.radians((north + south) / 2)
    resx = 10 / (M_PER_DEG_LON_EQUATOR * math.cos(lat))
    resy = 10 / M_PER_DEG_LAT
    scale = max(1.0, (east - west) / resx / 2500, (north - south) / resy / 2500)
    return resx * scale, resy * scale


# -------- polygons (lon/lat shapely geometries)


def _metric(lat0: float):
    """Forward/back transforms between lon/lat and local metres around lat0."""
    kx = M_PER_DEG_LON_EQUATOR * math.cos(math.radians(lat0))
    to_m = lambda x, y: (x * kx, y * M_PER_DEG_LAT)  # noqa: E731
    to_deg = lambda x, y: (x / kx, y / M_PER_DEG_LAT)  # noqa: E731
    return to_m, to_deg


def plot_polygon(points: list[tuple[float, float]]) -> Polygon:
    """(lat, lon) outline -> lon/lat polygon.

    The ring closes itself (a repeated first point, as in GeoJSON, is fine).
    Slightly broken outlines (self-touching rings from reprojected parcels)
    are repaired, keeping the largest piece.
    """
    for lat, lon in points:
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError(f"point ({lat}, {lon}) is out of range")
    ring = [(lon, lat) for lat, lon in points]
    if len(ring) > 1 and ring[0] == ring[-1]:
        ring = ring[:-1]
    if len(set(ring)) < 3:
        raise ValueError("a plot needs at least 3 distinct points")
    poly = Polygon(ring)
    if not poly.is_valid:
        poly = _largest(make_valid(poly))
    if poly is None or poly.area == 0:
        raise ValueError("the outline encloses no area")
    return poly


def _largest(geom) -> Polygon | None:
    polys = [g for g in getattr(geom, "geoms", [geom]) if g.geom_type == "Polygon"]
    polys += [p for g in getattr(geom, "geoms", []) if g.geom_type == "MultiPolygon" for p in g.geoms]
    return max(polys, key=lambda g: g.area) if polys else None


def shrink_polygon(poly: Polygon, buffer_m: float) -> Polygon:
    """Move the outline inwards by buffer_m metres (keeps the largest piece).

    Narrow plots that would vanish get a smaller buffer, down to none, rather
    than failing: a strip field is still better than no answer.
    """
    to_m, to_deg = _metric(poly.centroid.y)
    metric = transform(to_m, poly)
    for b in (buffer_m, buffer_m / 2, buffer_m / 4, 0):
        inner = metric.buffer(-b, join_style="mitre") if b else metric
        inner = _largest(inner) if not inner.is_empty else None
        # keep at least a few 10 m pixels inside
        if inner is not None and inner.area >= 500:
            return transform(to_deg, inner)
    raise ValueError("plot is too small: under 500 m² (5 Sentinel-2 pixels)")


def area_m2(poly: Polygon) -> float:
    to_m, _ = _metric(poly.centroid.y)
    return transform(to_m, poly).area


def _stat(item: dict, output: str) -> dict:
    return item["outputs"][output]["bands"]["B0"]["stats"]


def fetch_observations(sh: SentinelHub, area: Rect | Polygon, years: list[int]) -> list[dict]:
    """Every acquisition in the years, unfiltered, with its clear-pixel share.

    A Rect is sent as a bbox; a polygon as its geometry, so only pixels inside
    the outline count.
    """
    if isinstance(area, Rect):
        bbox, geometry, expected_px = area.bbox, None, None
    else:
        bbox = list(area.bounds)
        geometry = mapping(area)
        res = pixel_size(bbox)
        to_m, _ = _metric(area.centroid.y)
        px_w, px_h = to_m(res[0], res[1])
        expected_px = area_m2(area) / (px_w * px_h)
    res = pixel_size(bbox)
    rows = []
    for year in years:
        def fetch(year=year):
            return sh.stats(
                bbox, f"{year}-01-01", f"{year}-12-31", EVAL_FIELD,
                interval="P1D", res_deg=res, geometry=geometry,
            )

        key = {"bbox": bbox, "year": year, "res": res, "v": EVALSCRIPT_VERSION}
        if geometry:
            key["geometry"] = [[round(x, 7), round(y, 7)] for x, y in area.exterior.coords]
        raw = cached_json("s2_field", key, fetch) if year < date.today().year else fetch()
        for item in raw.get("data", []):
            if "error" in item:
                continue
            st = _stat(item, "ndvi")
            total = st.get("sampleCount", 0)
            valid = total - st.get("noDataCount", 0)
            if total == 0:
                continue
            rows.append({
                "date": item["interval"]["from"][:10],
                "ndvi": _round(st.get("mean")) if valid else None,
                "ndvi_std": _round(st.get("stDev")) if valid else None,
                "b11": _round(_stat(item, "b11").get("mean")) if valid else None,
                "valid_px": valid,
                "total_px": total,
                "valid_fraction": round(valid / total, 3),
            })
    if expected_px and rows:
        # pixels outside the outline are reported as no-data: measure the clear
        # share against the pixels inside it (area estimate, or the clearest
        # acquisition when edge pixels make that slightly larger)
        inside = max(1, round(expected_px), max(r["valid_px"] for r in rows))
        for r in rows:
            r["total_px"] = min(r["total_px"], inside)
            r["valid_fraction"] = round(r["valid_px"] / r["total_px"], 3)
    rows.sort(key=lambda r: r["date"])
    return rows


def _round(v, nd=4):
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else round(float(v), nd)


# ---------------------------------------------------------------- 2. clean


@dataclass
class Daily:
    dates: list[date]
    interp: np.ndarray  # despiked, linearly interpolated to daily
    smooth: np.ndarray  # Savitzky-Golay smoothed
    in_gap: np.ndarray  # True inside gaps longer than gap_days
    obs_distance: np.ndarray  # days to the nearest kept observation

    def month(self, i: int) -> int:
        return self.dates[i].month


def clean(obs: list[dict], p: Params) -> tuple[list[dict], Daily | None]:
    kept = [dict(o) for o in obs if o["ndvi"] is not None and o["valid_fraction"] >= p.min_valid]

    # a value well below both neighbours is a missed cloud: drop it before the
    # running median, which fails when two such spikes sit close together
    raw = [o["ndvi"] for o in kept]
    for i in range(1, len(kept) - 1):
        kept[i]["spike"] = raw[i] < min(raw[i - 1], raw[i + 1]) - p.spike_drop
    kept = [o for o in kept if not o.pop("spike", False)]
    if len(kept) < 5:
        return kept, None

    vals = np.array([o["ndvi"] for o in kept])
    despiked = vals.copy()
    for i in range(1, len(vals) - 1):
        despiked[i] = np.median(vals[i - 1: i + 2])
    for o, v in zip(kept, despiked):
        o["ndvi_despiked"] = round(float(v), 4)

    ords = np.array([date.fromisoformat(o["date"]).toordinal() for o in kept])
    days = np.arange(ords[0], ords[-1] + 1)
    interp = np.interp(days, ords, despiked)

    window = p.sg_window if p.sg_window % 2 else p.sg_window + 1
    smooth = (
        savgol_filter(interp, window, p.sg_order, mode="interp")
        if len(days) > window else interp.copy()
    )
    smooth = np.clip(smooth, -1, 1)

    in_gap = np.zeros(len(days), dtype=bool)
    for a, b in zip(ords, ords[1:]):
        if b - a > p.gap_days:
            in_gap[(days > a) & (days < b)] = True

    idx = np.clip(np.searchsorted(ords, days), 1, len(ords) - 1)
    obs_distance = np.minimum(np.abs(days - ords[idx - 1]), np.abs(ords[idx] - days))

    dates = [date.fromordinal(int(d)) for d in days]
    return kept, Daily(dates, interp, smooth, in_gap, obs_distance)


# ---------------------------------------------------------------- 3. label


@dataclass
class Segment:
    state: str  # "bare" | "crop"
    start: int  # inclusive day index
    end: int  # inclusive day index
    peak: int = 0
    end_type: str = ""  # harvest | gradual | drop_in_data_gap | ongoing
    harvest: int | None = None
    dormant: tuple[int, int] | None = None
    kind: str = "crop"  # crop | green_cover


def hysteresis(ndvi: np.ndarray, lo: float, hi: float, hold: int) -> list[str]:
    """Bare/crop per day; a switch needs `hold` consecutive days past the threshold."""
    state = "crop" if ndvi[0] > hi else "bare"
    states = [""] * len(ndvi)
    run = None
    for i, v in enumerate(ndvi):
        leaving = v > hi if state == "bare" else v < lo
        if leaving:
            run = i if run is None else run
            if i - run + 1 >= hold:
                state = "crop" if state == "bare" else "bare"
                states[run: i + 1] = [state] * (i + 1 - run)
                run = None
                continue
        else:
            run = None
        states[i] = state
    return states


def _segments(states: list[str]) -> list[Segment]:
    segs: list[Segment] = []
    for i, s in enumerate(states):
        if segs and segs[-1].state == s:
            segs[-1].end = i
        else:
            segs.append(Segment(s, i, i))
    return segs


def _split_double(seg: Segment, ndvi: np.ndarray, p: Params) -> list[Segment]:
    """Split a crop segment with two peaks and a sharp drop between them."""
    best = None
    for m in range(seg.start + p.harvest_days, seg.end - p.harvest_days):
        v = ndvi[m]
        if v >= p.crop_ndvi or not (ndvi[m - 1] >= v <= ndvi[m + 1]):
            continue
        if ndvi[seg.start: m].max() < p.crop_peak_min or ndvi[m + 1: seg.end + 1].max() < p.crop_peak_min:
            continue
        if ndvi[m - p.harvest_days: m].max() - v < p.double_drop:
            continue
        if best is None or v < ndvi[best]:
            best = m
    if best is None:
        return [seg]
    left = Segment("crop", seg.start, best)
    right = Segment("crop", best + 1, seg.end)
    return _split_double(left, ndvi, p) + _split_double(right, ndvi, p)


def _end_info(seg: Segment, d: Daily, p: Params) -> None:
    """Peak, and whether the season ended in a harvest cliff or a gradual fall."""
    ndvi, n = d.smooth, len(d.smooth)
    seg.peak = seg.start + int(np.argmax(ndvi[seg.start: seg.end + 1]))
    if seg.end >= n - 1:
        seg.end_type, seg.harvest = "ongoing", None
        return
    peak_v = ndvi[seg.peak]
    amp = max(peak_v - p.bare_ndvi, 1e-6)
    high, low = p.bare_ndvi + 0.5 * amp, p.bare_ndvi + 0.15 * amp
    search_end = min(n - 1, seg.end + 2 * p.hold_days)
    highs = [i for i in range(seg.peak, seg.end + 1) if ndvi[i] >= high]
    t_high = highs[-1] if highs else seg.peak
    t_low = next((i for i in range(t_high + 1, search_end + 1) if ndvi[i] <= low), None)
    if t_low is None or peak_v < p.crop_peak_min:
        # no clear drop, or nothing tall enough to harvest (e.g. autumn
        # emergence fading into winter)
        seg.end_type, seg.harvest = "gradual", None
        _trim_end(seg, ndvi, p)
        return
    steepest = t_high + int(np.argmin(np.diff(ndvi[t_high: t_low + 1])))
    seg.harvest = steepest
    if d.in_gap[t_high: t_low + 1].any():
        seg.end_type = "drop_in_data_gap"
    elif t_low - t_high <= p.harvest_days:
        seg.end_type = "harvest"
    else:
        seg.end_type = "gradual"

    _trim_end(seg, ndvi, p)


def _trim_end(seg: Segment, ndvi: np.ndarray, p: Params) -> None:
    """End the crop period where NDVI falls back below the crop line.

    Hysteresis only returns to bare below the bare line, but after harvest
    NDVI often plateaus between the lines (stubble, residue) for weeks; that
    plateau is not crop. Mirrors the start, which is the rise above the line.
    """
    above = np.nonzero(ndvi[seg.peak: seg.end + 1] >= p.crop_ndvi)[0]
    if len(above):
        seg.end = seg.peak + int(above[-1])


def _autumn_emergence(bare: Segment, nxt: Segment, d: Daily, p: Params) -> int | None:
    """Autumn-sown crop that stayed below the crop threshold before winter."""
    since = date(d.dates[nxt.start].year - 1, 9, 1)
    ndvi = d.smooth
    for i in range(bare.start, bare.end + 1):
        if d.dates[i] < since or d.dates[i].month < 9 or ndvi[i] < p.bare_ndvi:
            continue  # only an emergence in Sep-Dec counts as autumn sowing
        rest = slice(i, bare.end + 1)
        alive = (ndvi[rest] >= p.bare_ndvi) | d.in_gap[rest]
        if alive.mean() >= 0.7:
            return i
    return None


def _merge_dormancy(segs: list[Segment], d: Daily, p: Params) -> list[Segment]:
    out: list[Segment] = []
    i = 0
    while i < len(segs):
        s = segs[i]
        prev = out[-1] if out else None
        nxt = segs[i + 1] if i + 1 < len(segs) else None
        spring_rise = (
            s.state == "bare" and nxt is not None and nxt.state == "crop"
            and d.month(nxt.start) in (1, 2, 3, 4)
        )
        if (
            spring_rise and prev is not None and prev.state == "crop"
            and prev.end_type != "harvest" and d.month(s.start) in (10, 11, 12, 1, 2)
        ):
            # low winter period with no harvest before it and a spring rise after:
            # the autumn crop was dormant, not gone
            merged = replace(prev, end=nxt.end, dormant=(prev.end + 1, s.end))
            merged.peak = merged.start + int(np.argmax(d.smooth[merged.start: merged.end + 1]))
            merged.end_type, merged.harvest = nxt.end_type, nxt.harvest
            out[-1] = merged
            i += 2
            continue
        if spring_rise and d.month(nxt.peak) <= 6:
            start = _autumn_emergence(s, nxt, d, p)
            if start is not None:
                if start > s.start:
                    out.append(replace(s, end=start - 1))
                out.append(replace(nxt, start=start, dormant=(start, s.end)))
                i += 2
                continue
        out.append(s)
        i += 1
    return out


def label(d: Daily, p: Params) -> tuple[list[Segment], list[str]]:
    states = hysteresis(d.smooth, p.bare_ndvi, p.crop_ndvi, p.hold_days)
    segs: list[Segment] = []
    for s in _segments(states):
        segs += _split_double(s, d.smooth, p) if s.state == "crop" else [s]
    for s in segs:
        if s.state == "crop":
            _end_info(s, d, p)
    if p.detect_dormancy:
        segs = _merge_dormancy(segs, d, p)

    for s in segs:
        if (
            s.state == "crop" and s.dormant is None
            and d.smooth[s.peak] < p.crop_peak_min and d.month(s.start) in (7, 8, 9, 10, 11)
        ):
            s.kind = "green_cover"  # weeds / volunteers / cover crop after harvest
    segs = _green_cover_in_bare(segs, d, p)

    labels = ["bare"] * len(d.dates)
    for s in segs:
        if s.state != "crop":
            continue
        tag = "green_cover" if s.kind == "green_cover" else "crop"
        labels[s.start: s.end + 1] = [tag] * (s.end + 1 - s.start)
        if s.dormant:
            a, b = s.dormant
            labels[a: b + 1] = ["dormant_crop"] * (b + 1 - a)
    for a, b in _runs(d.in_gap):
        if p.fill_gaps and a > 0:
            # long cloud gap: assume the field stayed as it was before the gap
            labels[a: b + 1] = [labels[a - 1]] * (b + 1 - a)
        else:
            for i in range(a, b + 1):
                if labels[i] == "bare":
                    labels[i] = "uncertain"  # no clear image for > gap_days
    return segs, labels


def _runs(mask) -> list[tuple[int, int]]:
    """(start, end) inclusive index ranges where mask is True."""
    out, start = [], None
    for i, m in enumerate(mask):
        if m and start is None:
            start = i
        elif not m and start is not None:
            out.append((start, i - 1))
            start = None
    if start is not None:
        out.append((start, len(mask) - 1))
    return out


def _green_cover_in_bare(segs: list[Segment], d: Daily, p: Params) -> list[Segment]:
    """Weeds, volunteers or a cover crop after harvest: a summer/autumn rise
    inside a bare period that stays below the crop line."""
    cover_line = (p.bare_ndvi + p.crop_ndvi) / 2
    out: list[Segment] = []
    for s in segs:
        if s.state != "bare":
            out.append(s)
            continue
        mask = d.smooth[s.start: s.end + 1] > cover_line
        cursor = s.start
        for a, b in _runs(mask):
            a, b = a + s.start, b + s.start
            if b - a + 1 < p.hold_days or d.month(a) not in (7, 8, 9, 10, 11):
                continue
            if b == s.end:
                continue  # still rising into the next crop: its emergence, not cover
            if a > cursor:
                out.append(replace(s, start=cursor, end=a - 1))
            peak = a + int(np.argmax(d.smooth[a: b + 1]))
            out.append(Segment("crop", a, b, peak=peak, end_type="gradual", kind="green_cover"))
            cursor = b + 1
        if cursor <= s.end:
            out.append(replace(s, start=cursor))
    return out


# ---------------------------------------------------------------- 4. events


def _confidence(d: Daily, a: int, b: int) -> str:
    near = (d.obs_distance[a: b + 1] <= 7).mean()
    gap = d.in_gap[a: b + 1].mean()
    if gap > 0.5 or near < 0.4:
        return "low"
    if gap > 0 or near < 0.7:
        return "medium"
    return "high"


def periods(d: Daily, labels: list[str]) -> list[dict]:
    out = []
    start = 0
    for i in range(1, len(labels) + 1):
        if i == len(labels) or labels[i] != labels[start]:
            out.append({
                "start": d.dates[start].isoformat(),
                "end": d.dates[i - 1].isoformat(),
                "state": labels[start],
                "days": i - start,
                "gap_days": int(d.in_gap[start:i].sum()),  # days without a clear image nearby
                "confidence": _confidence(d, start, i - 1),
            })
            start = i
    return out


def _crop_type(s: Segment, d: Daily, emergence: int | None) -> str:
    peak = d.dates[s.peak]
    emerged = d.dates[emergence if emergence is not None else s.start]
    harvest = d.dates[s.harvest] if s.harvest is not None else None
    if s.dormant or (emerged.month >= 8 and peak.year > emerged.year and peak.month <= 7):
        return "winter crop"
    if s.end_type == "ongoing" and emerged.month >= 9:
        return "winter crop (sown, season continues)"
    # spring-sown, peaking in July+ (or a June peak but harvested from August)
    if 3 <= emerged.month <= 6 and (
        peak.month >= 7 or (peak.month == 6 and harvest is not None and harvest.month >= 8)
    ):
        return "summer crop"
    if 4 <= peak.month <= 6:
        return "winter or early spring crop"
    if 7 <= peak.month <= 9:
        return "summer crop"
    return "unclear"


def _emergence(s: Segment, d: Daily, p: Params) -> int | None:
    """Rise through the bare line before the crop threshold was passed."""
    i = s.start
    while i > 0 and d.smooth[i - 1] > p.bare_ndvi and s.start - i < 60:
        i -= 1
    return None if i == 0 else i  # None: emerged before the data starts


def seasons(segs: list[Segment], d: Daily, p: Params) -> list[dict]:
    out = []
    for s in segs:
        if s.state != "crop":
            continue
        iso = lambda i: d.dates[i].isoformat() if i is not None else None  # noqa: E731
        emergence = _emergence(s, d, p)
        out.append({
            "kind": s.kind,
            "season_year": d.dates[s.peak].year,
            "crop_type_guess": _crop_type(s, d, emergence) if s.kind == "crop" else None,
            "emergence": iso(emergence),
            "crop_threshold_date": iso(s.start),  # first day labelled crop
            "peak_date": iso(s.peak),
            "peak_ndvi": round(float(d.smooth[s.peak]), 3),
            "harvest_date": iso(s.harvest),
            "end_type": s.end_type,
            "dormant": {"from": iso(s.dormant[0]), "to": iso(s.dormant[1])} if s.dormant else None,
            "length_days": s.end - s.start + 1,
            "double_cropping": False,
            "confidence": _confidence(d, s.start, s.end),
        })
    # double cropping: two cash-crop harvests in the same calendar year
    by_year: dict[int, list[dict]] = {}
    for x in out:
        if x["kind"] == "crop" and x["harvest_date"]:
            by_year.setdefault(int(x["harvest_date"][:4]), []).append(x)
    for xs in by_year.values():
        if len(xs) > 1:
            for x in xs:
                x["double_cropping"] = True
    return out


def year_summary(
    years: list[int], kept: list[dict], d: Daily, labels: list[str], seas: list[dict]
) -> list[dict]:
    rows = []
    for y in years:
        idx = [i for i, dt in enumerate(d.dates) if dt.year == y]
        obs_months = {int(o["date"][5:7]) for o in kept if o["date"].startswith(str(y))}
        crops = [s for s in seas if s["kind"] == "crop" and s["season_year"] == y]
        top = max(crops, key=lambda s: s["peak_ndvi"]) if crops else None
        month_labels: dict[int, list[str]] = {}
        for i in idx:
            month_labels.setdefault(d.dates[i].month, []).append(labels[i])
        rows.append({
            "year": y,
            "observations": sum(1 for o in kept if o["date"].startswith(str(y))),
            "months_without_clear_image": [m for m in range(1, 13) if m not in obs_months],
            "peak_ndvi": top["peak_ndvi"] if top else None,
            "peak_month": int(top["peak_date"][5:7]) if top else None,
            "crop_type_guess": [s["crop_type_guess"] for s in crops],
            "double_cropping": any(s["double_cropping"] for s in crops),
            "bare_days": sum(1 for i in idx if labels[i] == "bare"),
            "uncertain_days": sum(1 for i in idx if labels[i] == "uncertain"),
            "bare_months": sorted(
                m for m, ls in month_labels.items() if ls.count("bare") > len(ls) / 2
            ),
        })
    peaks = [r["peak_ndvi"] for r in rows if r["peak_ndvi"] is not None]
    avg = sum(peaks) / len(peaks) if peaks else None
    for r in rows:
        if avg and r["peak_ndvi"] is not None:
            r["peak_vs_average"] = round(r["peak_ndvi"] - avg, 3)
            r["peak_ratio"] = round(r["peak_ndvi"] / avg, 3)
    return rows


def daily_rows(d: Daily, labels: list[str]) -> list[dict]:
    return [
        {
            "date": dt.isoformat(),
            "ndvi": round(float(d.interp[i]), 4),
            "ndvi_smooth": round(float(d.smooth[i]), 4),
            "label": labels[i],
            "in_gap": bool(d.in_gap[i]),
        }
        for i, dt in enumerate(d.dates)
    ]


PERIOD_TYPES = {
    "bare": "barren",
    "crop": "crop",
    "dormant_crop": "dormant_crop",  # sown crop not showing up yet (winter)
    "green_cover": "green_cover",  # weeds / cover crop, no cash crop
    "uncertain": "uncertain",  # only when fill_gaps is off
}

CROP_TYPES = {
    "winter crop": "winter",
    "winter crop (sown, season continues)": "winter",
    "summer crop": "summer",
    "winter or early spring crop": "winter_or_spring",
    "unclear": "unknown",
}


def plot_periods(segs: list[Segment], d: Daily, labels: list[str], p: Params) -> list[dict]:
    """Compact timeline: start, end, type, crop type and confidence per period."""
    crops = [s for s in segs if s.state == "crop" and s.kind == "crop"]
    crop_type = {id(s): CROP_TYPES[_crop_type(s, d, _emergence(s, d, p))] for s in crops}

    def season_of(i: int) -> Segment | None:
        # the crop season holding day i; a gap-filled tail belongs to the one before
        before = [s for s in crops if s.start <= i]
        inside = [s for s in before if i <= s.end]
        return (inside or before or [None])[-1]

    out = []
    for per in periods(d, labels):
        kind = PERIOD_TYPES[per["state"]]
        seg = season_of(d.dates.index(date.fromisoformat(per["start"]))) if kind in (
            "crop", "dormant_crop") else None
        out.append({
            "start": per["start"],
            "end": per["end"],
            "type": kind,
            "crop_type": crop_type[id(seg)] if seg else None,
            "confidence": per["confidence"],
        })
    return out


def analyse(obs: list[dict], years: list[int], p: Params) -> dict:
    kept, d = clean(obs, p)
    result = {
        "observations_total": len(obs),
        "observations_kept": len(kept),
        "observations": obs,
        "kept": kept,
    }
    if d is None:
        return {
            **result, "summary": [], "seasons": [], "periods": [], "bare_periods": [],
            "years": [], "daily": [],
        }
    p = field_thresholds(d, p)
    result["thresholds_used"] = {"bare_ndvi": p.bare_ndvi, "crop_ndvi": p.crop_ndvi}
    segs, labels = label(d, p)
    seas = seasons(segs, d, p)
    pers = periods(d, labels)
    avg_peak = [s["peak_ndvi"] for s in seas if s["kind"] == "crop"]
    return {
        **result,
        "summary": plot_periods(segs, d, labels, p),
        "average_crop_peak": round(sum(avg_peak) / len(avg_peak), 3) if avg_peak else None,
        "seasons": seas,
        "periods": pers,
        "bare_periods": [
            {"start": x["start"], "end": x["end"], "days": x["days"], "confidence": x["confidence"]}
            for x in pers if x["state"] == "bare"
        ],
        "years": year_summary(years, kept, d, labels, seas),
        "daily": daily_rows(d, labels),
    }
