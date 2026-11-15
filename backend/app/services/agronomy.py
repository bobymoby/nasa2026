"""Fixed agronomy table per crop: Bulgarian calendar, FAO-56 water parameters,
nitrogen, humus balance, drought tolerance and forage use.

Sources
    FAO-56: Allen et al. (1998), Tables 11/12 (stage lengths, Kc), Table 22 (p, rooting depth).
    FAO-33: Doorenbos & Kassam (1979), Ky.
    VDLUFA (2014) humus balance, lower values, humus equivalents per ha and year.
"""

import csv
import re
from datetime import date, timedelta
from functools import cache
from pathlib import Path
from typing import Literal, NamedTuple

from pydantic import BaseModel, ConfigDict, Field, ValidationError, ValidationInfo, field_validator

from app.services import drought
from app.services.drought import MD

AGRONOMY_CSV = Path(__file__).resolve().parents[1] / "data" / "crop_agronomy.csv"
REFERENCE_YEAR = 2022
WINTER_SOW_BEFORE_FROST_DAYS = 21
STAGE_NAMES = ("initial", "development", "mid", "late")
MM_DD = re.compile(r"^(\d{2})-(\d{2})$")
FAMILY = re.compile(r"^[A-Z][a-z]+aceae$")


class Frost(NamedTuple):
    last_spring: MD
    first_autumn: MD


def _ref(md: MD, year: int = REFERENCE_YEAR + 1) -> date:
    return date(year, *md)


def _md(d: date) -> MD:
    return d.month, d.day


class AgronomyCrop(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    crop_id: str = Field(pattern=r"^[a-z][a-z_]*$")
    name: str = Field(min_length=1)
    role: Literal["cash", "cover", "forage"]
    family: tuple[str, ...]
    min_return_years: int = Field(ge=0, le=10)
    sow_start: MD
    sow_end: MD
    harvest_start: MD
    harvest_end: MD
    kc_ini: float = Field(ge=0.1, le=1.3)
    kc_mid: float = Field(ge=0.3, le=1.3)
    kc_end: float = Field(ge=0.1, le=1.3)
    l_ini: int = Field(ge=1, le=300)
    l_dev: int = Field(ge=1, le=300)
    l_mid: int = Field(ge=1, le=300)
    l_late: int = Field(ge=1, le=300)
    p: float = Field(ge=0.1, le=0.9)
    ky: float = Field(ge=0.2, le=1.5)
    root_depth_m: float = Field(ge=0.2, le=3.0)
    n_fix_kg_ha: float = Field(ge=0, le=400)
    n_demand_kg_t: float = Field(ge=0, le=100)
    humus_ceq: int = Field(ge=-2000, le=1000)
    drought_tolerance: Literal["low", "medium", "high"]
    forage: bool

    @field_validator("family", mode="before")
    @classmethod
    def _split_family(cls, v):
        if isinstance(v, str):
            v = tuple(v.split("+"))
        for f in v:
            if not FAMILY.match(f):
                raise ValueError(f"{f!r} is not a botanical family (…aceae)")
        return v

    @field_validator("sow_start", "sow_end", "harvest_start", "harvest_end", mode="before")
    @classmethod
    def _parse_md(cls, v):
        if not isinstance(v, str):
            return v
        m = MM_DD.match(v)
        if not m:
            raise ValueError(f"{v!r} is not MM-DD")
        md = (int(m[1]), int(m[2]))
        try:
            _ref(md)
        except ValueError:
            raise ValueError(f"{v!r} is not a calendar day") from None
        return md

    @field_validator("sow_end", "harvest_end")
    @classmethod
    def _window_order(cls, v: MD, info: ValidationInfo) -> MD:
        start = info.data.get(info.field_name.replace("_end", "_start"))
        if start is not None and v < start:
            raise ValueError("window ends before it starts")
        return v

    @field_validator("l_late")
    @classmethod
    def _season_fits(cls, v: int, info: ValidationInfo) -> int:
        d = info.data
        keys = ("sow_start", "sow_end", "harvest_start", "harvest_end", "l_ini", "l_dev", "l_mid")
        if any(k not in d for k in keys):
            return v
        total = d["l_ini"] + d["l_dev"] + d["l_mid"] + v
        if total >= 365:
            raise ValueError(f"stage lengths add up to {total} days, a crop season must be shorter than a year")
        harvest = _md(_sow_day(d["sow_start"], d["sow_end"]) + timedelta(days=total))
        lo, hi = d["harvest_start"], d["harvest_end"]
        if not lo <= harvest <= hi:
            raise ValueError(
                f"sowing mid-window plus {total} days ends on {harvest[0]:02d}-{harvest[1]:02d}, "
                "outside the harvest window"
            )
        return v

    @property
    def stage_lengths(self) -> tuple[int, int, int, int]:
        return self.l_ini, self.l_dev, self.l_mid, self.l_late

    @property
    def season_days(self) -> int:
        return sum(self.stage_lengths)

    @property
    def winter(self) -> bool:
        return _md(self.sow_day + timedelta(days=self.season_days)) < self.sow_start

    @property
    def sow_day(self) -> date:
        return _sow_day(self.sow_start, self.sow_end)


def _sow_day(start: MD, end: MD) -> date:
    a, b = _ref(start, REFERENCE_YEAR), _ref(end, REFERENCE_YEAR)
    return a + (b - a) // 2


def load(path: Path) -> dict[str, AgronomyCrop]:
    out: dict[str, AgronomyCrop] = {}
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        for line, row in enumerate(reader, start=2):
            where = f"{path.name} row {line} ({row.get('crop_id') or '?'})"
            try:
                crop = AgronomyCrop.model_validate(row)
            except ValidationError as e:
                problems = "; ".join(
                    f"column {'.'.join(str(x) for x in err['loc']) or '?'}: {err['msg']}" for err in e.errors()
                )
                raise ValueError(f"{where}, {problems}") from None
            if crop.crop_id in out:
                raise ValueError(f"{where}, column crop_id: duplicate id")
            out[crop.crop_id] = crop
    return out


@cache
def crops() -> dict[str, AgronomyCrop]:
    return load(AGRONOMY_CSV)


def sowing_shift_days(crop: AgronomyCrop, frost: Frost | None) -> int:
    if frost is None:
        return 0
    if crop.winter:
        target = _ref(frost.first_autumn) - timedelta(days=WINTER_SOW_BEFORE_FROST_DAYS)
        return (target - _ref(crop.sow_end)).days
    if crop.sow_start < (7, 1):
        return (_ref(frost.last_spring) - _ref(crop.sow_start)).days
    return 0


def water_crop(crop: AgronomyCrop | str, frost: Frost | None = None) -> drought.Crop:
    if isinstance(crop, str):
        crop = crops()[crop]
    sow = crop.sow_day + timedelta(days=sowing_shift_days(crop, frost))

    bounds = [sow]
    for length in crop.stage_lengths:
        bounds.append(bounds[-1] + timedelta(days=length))
    kcs = (crop.kc_ini, crop.kc_ini, crop.kc_mid, crop.kc_mid, crop.kc_end)
    stages = [
        drought.Stage(name, _md(bounds[i]), _md(bounds[i + 1] - timedelta(days=1 if i < 3 else 0)), crop.ky)
        for i, name in enumerate(STAGE_NAMES)
    ]
    return drought.Crop(
        key=crop.crop_id,
        label=crop.name,
        sow=_md(sow),
        harvest=_md(bounds[-1]),
        kc_points=[(_md(d), kc) for d, kc in zip(bounds, kcs)],
        stages=stages,
        flowering="mid",
        p=crop.p,
        ky_season=crop.ky,
    )
