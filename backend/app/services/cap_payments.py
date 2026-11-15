"""EU CAP payments per hectare in Bulgaria, for a generated rotation.

Rates are in `app/data/cap_payments.csv`: the unit amounts actually paid for
claim year 2024, from the Ministry of Agriculture's Annual Performance Report
for financial year 2025 (SFC2021, CAP strategic plan version 5.6). Planned
amounts for 2023-2027 are in the JRC CAP Strategic Plans master file.

Neither source states the eligibility rules, so where a rule is assumed the
row says so in its `assumption` column and the response repeats it.

A row's band (`band_from_ha` to `band_to_ha`) is the slice of the farm area
paid at its rate; `max_farm_ha` excludes larger farms (CRISS: the first 30 ha
of farms up to 600 ha). The selected area stands for the farm.
"""

import csv
import math
from dataclasses import dataclass
from functools import cache
from pathlib import Path

CAP_CSV = Path(__file__).resolve().parents[1] / "data" / "cap_payments.csv"
SOURCE = (
    "Ministry of Agriculture, Annual Performance Report FY2025 (claim year 2024), "
    "realised unit amounts"
)
MIN_DIVERSE_CROPS = 3


@dataclass(frozen=True)
class Rate:
    scheme: str
    name: str
    intervention: str
    eur_per_ha: float
    basis: str
    crops: frozenset[str]
    band_from_ha: float
    band_to_ha: float
    max_farm_ha: float
    claim_year: int
    assumption: str

    def share(self, area_ha: float) -> float:
        """Part of the farm area paid at this rate."""
        if area_ha <= 0 or area_ha > self.max_farm_ha:
            return 0.0
        return max(0.0, min(area_ha, self.band_to_ha) - self.band_from_ha) / area_ha


@dataclass(frozen=True)
class CapLine:
    scheme: str
    name: str
    eur_per_ha: float
    interventions: list[str]
    claim_year: int
    assumption: str


def _number(value: str, default: float) -> float:
    return float(value) if value else default


@cache
def rates() -> list[Rate]:
    with CAP_CSV.open(encoding="utf-8", newline="") as f:
        return [
            Rate(
                scheme=row["scheme"],
                name=row["name"],
                intervention=row["intervention"],
                eur_per_ha=float(row["eur_per_ha"]),
                basis=row["basis"],
                crops=frozenset(filter(None, row["crops"].split("|"))),
                band_from_ha=_number(row["band_from_ha"], 0.0),
                band_to_ha=_number(row["band_to_ha"], math.inf),
                max_farm_ha=_number(row["max_farm_ha"], math.inf),
                claim_year=int(row["claim_year"]),
                assumption=row["assumption"],
            )
            for row in csv.DictReader(f)
        ]


def _per_ha(rate: Rate, area_ha: float) -> float:
    return rate.eur_per_ha * rate.share(area_ha)


def crop_payment_eur_ha(crop_id: str, area_ha: float) -> float:
    """Coupled support for one season of a crop, per ha of the farm."""
    return sum(_per_ha(r, area_ha) for r in rates() if r.basis == "crop" and crop_id in r.crops)


def cover_payment_eur_ha(area_ha: float) -> float:
    return sum(_per_ha(r, area_ha) for r in rates() if r.basis == "cover_crop")


def diversification_eur_ha_year(main_crop_ids: list[str], area_ha: float) -> float:
    if len(set(main_crop_ids)) < MIN_DIVERSE_CROPS:
        return 0.0
    return sum(_per_ha(r, area_ha) for r in rates() if r.basis == "diversification")


def cap_lines(plantings: list[dict], years: int, area_ha: float) -> list[CapLine]:
    """Each payment one pass through the rotation earns, per ha of the farm."""
    main = [p["crop_id"] for p in plantings if p["role"] != "cover"]
    covers = sum(p["role"] == "cover" for p in plantings)
    diverse = len(set(main)) >= MIN_DIVERSE_CROPS
    totals: dict[str, float] = {}
    used: dict[str, list[Rate]] = {}
    for rate in rates():
        per_ha = _per_ha(rate, area_ha)
        if rate.basis == "area":
            amount = per_ha * years
        elif rate.basis == "crop":
            amount = per_ha * sum(crop in rate.crops for crop in main)
        elif rate.basis == "cover_crop":
            amount = per_ha * covers
        elif rate.basis == "diversification":
            amount = per_ha * years if diverse else 0.0
        else:
            raise ValueError(f"cap_payments.csv: unknown basis {rate.basis!r}")
        if amount > 0:
            totals[rate.scheme] = totals.get(rate.scheme, 0.0) + amount
            used.setdefault(rate.scheme, []).append(rate)
    return [
        CapLine(
            scheme=scheme,
            name=used[scheme][0].name,
            eur_per_ha=total,
            interventions=[r.intervention for r in used[scheme]],
            claim_year=used[scheme][0].claim_year,
            assumption=" ".join(dict.fromkeys(r.assumption for r in used[scheme] if r.assumption)),
        )
        for scheme, total in totals.items()
    ]
