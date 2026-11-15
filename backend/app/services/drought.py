"""Test 2: FAO-56 reference ET₀ and a daily soil-water bucket per crop season.

References
    FAO-56: Allen et al. (1998), Crop evapotranspiration, eqs. 7–39 (ET₀),
            Tables 11/12 (Kc), Table 22 (depletion fraction p), ch. 8 (Ks).
    FAO-33: Doorenbos & Kassam (1979), Yield response to water (Ky).

Simplifications (deliberate, for the pipeline test):
    * fixed soil water capacity (TAW, default 150 mm) instead of real soil data;
    * all rain is effective, anything above capacity drains away (no runoff);
    * fixed calendar dates for sowing, stages and harvest.
"""

import calendar
import math
from dataclasses import dataclass
from datetime import date, timedelta

from app.services.weather import DailySeries

# ---------------------------------------------------------------- FAO-56 ET₀

SIGMA = 4.903e-9  # Stefan-Boltzmann, MJ K⁻⁴ m⁻² day⁻¹


def _sat_vp(t: float) -> float:
    """Saturation vapour pressure e°(T), kPa (eq. 11)."""
    return 0.6108 * math.exp(17.27 * t / (t + 237.3))


def et0_fao56(
    day: date,
    tmax: float,
    tmin: float,
    rh_max: float,
    rh_min: float,
    wind10: float,
    rs: float,
    lat_deg: float,
    elevation: float,
) -> float:
    """Daily Penman-Monteith reference evapotranspiration, mm/day (eq. 6)."""
    t = (tmax + tmin) / 2
    pressure = 101.3 * ((293 - 0.0065 * elevation) / 293) ** 5.26  # eq. 7
    gamma = 0.000665 * pressure  # eq. 8
    delta = 4098 * _sat_vp(t) / (t + 237.3) ** 2  # eq. 13

    es = (_sat_vp(tmax) + _sat_vp(tmin)) / 2  # eq. 12
    ea = (_sat_vp(tmin) * rh_max / 100 + _sat_vp(tmax) * rh_min / 100) / 2  # eq. 17

    u2 = wind10 * 4.87 / math.log(67.8 * 10 - 5.42)  # eq. 47, 10 m -> 2 m

    # extraterrestrial and clear-sky radiation (eqs. 21–25, 37)
    j = day.timetuple().tm_yday
    phi = math.radians(lat_deg)
    dr = 1 + 0.033 * math.cos(2 * math.pi * j / 365)
    decl = 0.409 * math.sin(2 * math.pi * j / 365 - 1.39)
    ws = math.acos(max(-1.0, min(1.0, -math.tan(phi) * math.tan(decl))))
    ra = (24 * 60 / math.pi) * 0.0820 * dr * (
        ws * math.sin(phi) * math.sin(decl) + math.cos(phi) * math.cos(decl) * math.sin(ws)
    )
    rso = (0.75 + 2e-5 * elevation) * ra

    rns = 0.77 * rs  # eq. 38, albedo 0.23
    rel_sw = min(rs / rso, 1.0) if rso > 0 else 0.5
    rnl = (
        SIGMA
        * ((tmax + 273.16) ** 4 + (tmin + 273.16) ** 4)
        / 2
        * (0.34 - 0.14 * math.sqrt(max(ea, 0)))
        * (1.35 * rel_sw - 0.35)
    )  # eq. 39
    rn = rns - rnl  # soil heat flux G ≈ 0 for daily steps

    num = 0.408 * delta * rn + gamma * 900 / (t + 273) * u2 * (es - ea)
    return max(0.0, num / (delta + gamma * (1 + 0.34 * u2)))


def et0_series(s: DailySeries) -> list[float | None]:
    v = s.values
    out: list[float | None] = []
    for i, d in enumerate(s.dates):
        args = [v[k][i] for k in ("tmax", "tmin", "rh_max", "rh_min", "wind10", "rs")]
        out.append(
            None if any(a is None for a in args)
            else round(et0_fao56(d, *args, s.latitude, s.elevation), 3)
        )
    return out


# ---------------------------------------------------------------- crops

MD = tuple[int, int]  # (month, day)


@dataclass(frozen=True)
class Stage:
    name: str
    start: MD
    end: MD  # inclusive
    ky: float  # FAO-33 stage yield response factor


@dataclass(frozen=True)
class Crop:
    key: str
    label: str
    sow: MD
    harvest: MD
    kc_points: list[tuple[MD, float]]  # Kc is linearly interpolated between these
    stages: list[Stage]
    flowering: str  # name of the flowering stage
    p: float  # FAO-56 Table 22 depletion fraction before stress
    ky_season: float  # FAO-33 whole-season Ky

    @property
    def crosses_year(self) -> bool:
        return self.sow > self.harvest

    def on(self, md: MD, season: int) -> date:
        """Calendar date of (month, day) in the season that is harvested in `season`."""
        year = season - 1 if self.crosses_year and md >= self.sow else season
        return date(year, *md)

    def window(self, season: int) -> tuple[date, date]:
        return self.on(self.sow, season), self.on(self.harvest, season)


CROPS: dict[str, Crop] = {
    "wheat": Crop(
        key="wheat",
        label="Winter wheat",
        sow=(10, 15),
        harvest=(7, 5),
        # FAO-56 Table 12: Kc ini 0.7, mid 1.15, end 0.25
        kc_points=[((10, 15), 0.7), ((3, 1), 0.7), ((5, 1), 1.15), ((6, 10), 1.15), ((7, 5), 0.25)],
        stages=[
            Stage("vegetative", (10, 15), (5, 19), 0.2),
            Stage("flowering", (5, 20), (6, 15), 0.6),
            Stage("yield_formation", (6, 16), (7, 5), 0.5),
        ],
        flowering="flowering",
        p=0.55,
        ky_season=1.0,
    ),
    "maize": Crop(
        key="maize",
        label="Maize (grain)",
        sow=(4, 15),
        harvest=(9, 30),
        # FAO-56 Table 12: Kc ini 0.3, mid 1.20, end 0.35
        kc_points=[((4, 15), 0.3), ((5, 15), 0.3), ((7, 1), 1.2), ((8, 20), 1.2), ((9, 30), 0.35)],
        stages=[
            Stage("vegetative", (4, 15), (6, 30), 0.4),
            Stage("flowering", (7, 1), (7, 31), 1.5),
            Stage("yield_formation", (8, 1), (9, 10), 0.5),
            Stage("ripening", (9, 11), (9, 30), 0.2),
        ],
        flowering="flowering",
        p=0.55,
        ky_season=1.25,
    ),
}

SPINUP_DAYS = 90  # bucket starts full this many days before sowing
SPINUP_KC = 0.3  # bare soil / stubble evaporation before sowing


def _kc(crop: Crop, season: int, d: date) -> float:
    pts = [(crop.on(md, season), kc) for md, kc in crop.kc_points]
    if d <= pts[0][0]:
        return pts[0][1]
    for (d0, k0), (d1, k1) in zip(pts, pts[1:]):
        if d <= d1:
            return k0 + (k1 - k0) * (d - d0).days / max((d1 - d0).days, 1)
    return pts[-1][1]


def seasonal_etc_mm(crop: Crop, et0_monthly_mm: dict[int, float], season: int = 2023) -> float:
    """FAO-56 crop water demand over one season: daily Kc × the month's mean daily ET₀."""
    sow, harvest = crop.window(season)
    total, d = 0.0, sow
    while d <= harvest:
        days = calendar.monthrange(d.year, d.month)[1]
        total += _kc(crop, season, d) * et0_monthly_mm[d.month] / days
        d += timedelta(days=1)
    return total


# ---------------------------------------------------------------- bucket


def simulate_season(
    crop: Crop,
    season: int,
    dates: list[date],
    et0: list[float | None],
    precip: list[float | None],
    taw: float = 150.0,
) -> dict:
    """Run the FAO-56 single-layer water balance for one season.

    Returns per-day trace, per-stage ETc/ETa and the FAO-33 relative yield.
    """
    index = {d: i for i, d in enumerate(dates)}
    sow, harvest = crop.window(season)
    start = sow - timedelta(days=SPINUP_DAYS)
    if start not in index or harvest not in index:
        raise ValueError(f"weather data does not cover {crop.key} season {season}")

    raw = crop.p * taw
    depletion = 0.0  # mm below field capacity; starts full
    days = []
    d = start
    while d <= harvest:
        i = index[d]
        e0 = et0[i] or 0.0
        rain = precip[i] or 0.0
        in_season = d >= sow
        kc = _kc(crop, season, d) if in_season else SPINUP_KC
        ks = 1.0 if depletion <= raw else max(0.0, (taw - depletion) / (taw - raw))
        etc = kc * e0
        eta = ks * etc
        depletion = depletion - rain + eta
        drainage = max(0.0, -depletion)
        depletion = min(max(depletion, 0.0), taw)
        if in_season:
            days.append({
                "date": d.isoformat(),
                "et0": round(e0, 2),
                "kc": round(kc, 3),
                "etc": round(etc, 2),
                "eta": round(eta, 2),
                "ks": round(ks, 3),
                "rain": round(rain, 1),
                "soil_water_mm": round(taw - depletion, 1),
                "drainage": round(drainage, 1),
            })
        d += timedelta(days=1)

    stages = []
    rel_yield = 1.0
    for st in crop.stages:
        lo, hi = crop.on(st.start, season).isoformat(), crop.on(st.end, season).isoformat()
        sel = [x for x in days if lo <= x["date"] <= hi]
        etc = sum(x["etc"] for x in sel)
        eta = sum(x["eta"] for x in sel)
        ratio = eta / etc if etc > 0 else 1.0
        stage_factor = max(0.0, 1 - st.ky * (1 - ratio))
        rel_yield *= stage_factor  # FAO-33 multiplicative stage model
        stages.append({
            "stage": st.name,
            "from": lo,
            "to": hi,
            "ky": st.ky,
            "etc_mm": round(etc, 1),
            "eta_mm": round(eta, 1),
            "shortfall_mm": round(etc - eta, 1),
            "eta_etc": round(ratio, 3),
        })

    etc_tot = sum(x["etc"] for x in days)
    eta_tot = sum(x["eta"] for x in days)
    season_ratio = eta_tot / etc_tot if etc_tot > 0 else 1.0
    flowering = next(s for s in stages if s["stage"] == crop.flowering)
    return {
        "summary": {
            "season": season,
            "sow": sow.isoformat(),
            "harvest": harvest.isoformat(),
            "rain_mm": round(sum(x["rain"] for x in days), 1),
            "et0_mm": round(sum(x["et0"] for x in days), 1),
            "etc_mm": round(etc_tot, 1),
            "eta_mm": round(eta_tot, 1),
            "shortfall_mm": round(etc_tot - eta_tot, 1),
            "flowering_shortfall_mm": flowering["shortfall_mm"],
            "stress_days": sum(1 for x in days if x["ks"] < 1),
            # FAO-33 whole-season relative yield (headline); the stage-wise
            # multiplicative variant compounds losses and is much harsher
            "relative_yield": round(max(0.0, 1 - crop.ky_season * (1 - season_ratio)), 3),
            "relative_yield_stagewise": round(rel_yield, 3),
            "stages": stages,
        },
        "days": days,
    }
