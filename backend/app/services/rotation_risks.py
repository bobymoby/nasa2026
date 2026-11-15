"""Environmental issues a crop sequence makes worse.

Input is only the crops in planting order. Typical sowing and harvest months
(temperate Europe, e.g. Bulgaria) come from the trait table below, so the
time between two crops is inferred, not given.

Only medium and high risks are reported; an empty list means no medium or
high risk was found.

Trait values are coarse agronomic categories (1 = low, 2 = medium,
3 = high) from general references (FAO-56 rooting depths and crop water use,
RUSLE cover-factor ranges, typical regional crop calendars). They are meant
for warnings, not quantitative predictions.

Rules
-----
Single crop (every crop in the list):
  erosion       row crop (low cover): high if it also leaves little residue,
                else medium; bare fallow: high
  waterlogging  heavy late harvest (sugar beet, potato, silage maize)
                compacts the soil: medium; row crops crust and shed water
                in heavy rain: medium
Each step A -> B:
  drought       A uses a lot of water with deep/medium roots and B is sown
                soon after A's harvest, so the subsoil cannot refill:
                gap <= 2 months -> high (deep roots) / medium;
                gap 3-5 months and deep roots -> medium
  heat          high drought carry-over into a crop growing through summer:
                medium (dry soil makes heat damage worse)
  waterlogging  A's heavy harvest compacts the soil B grows in: medium,
                high if B is sown within a month (often on wet soil)
  erosion       soil left bare over winter between an autumn harvest and a
                spring crop: high with little residue, medium with some;
                two low-cover seasons in a row: medium, high if A also
                leaves little residue
  disease       same crop twice: high; same plant family: medium
Whole sequence (2+ crops):
  erosion       more than half of the crops are low-cover: medium, all: high
  drought       3+ crops, no perennial forage and mostly low-residue crops:
                soil organic matter declines and holds less water: medium
"""

from dataclasses import dataclass

MEDIUM, HIGH = "medium", "high"
SEVERITY_RANK = {MEDIUM: 1, HIGH: 2}


@dataclass(frozen=True)
class Crop:
    name: str
    family: str | None
    water_use: int  # seasonal water demand, 0-3
    root_depth: int  # 0-3
    sow_month: int  # typical sowing (or establishment) month
    harvest_month: int | None  # typical harvest / ploughing-out month
    season: str  # "winter", "spring", "perennial" or "fallow"
    residue: int  # residue left on the field, 1-3
    cover: int  # soil protection while growing: 1 low (row crop / bare), 2 dense, 3 perennial
    heavy_harvest: bool = False  # heavy machinery at a late, often wet harvest


CROPS: dict[str, Crop] = {
    c.name: c
    for c in (
        Crop("wheat", "Poaceae", 2, 2, 10, 7, "winter", 3, 2),
        Crop("barley", "Poaceae", 2, 2, 10, 6, "winter", 2, 2),
        Crop("rye", "Poaceae", 2, 2, 10, 7, "winter", 3, 2),
        Crop("oats", "Poaceae", 2, 2, 3, 7, "spring", 2, 2),
        Crop("maize", "Poaceae", 3, 2, 4, 10, "spring", 3, 1),
        Crop("maize_silage", "Poaceae", 3, 2, 4, 9, "spring", 1, 1, heavy_harvest=True),
        Crop("sorghum", "Poaceae", 2, 3, 5, 10, "spring", 2, 1),
        Crop("sunflower", "Asteraceae", 3, 3, 4, 9, "spring", 1, 1),
        Crop("rapeseed", "Brassicaceae", 2, 2, 9, 7, "winter", 2, 2),
        Crop("soybean", "Fabaceae", 2, 2, 5, 9, "spring", 1, 1),
        Crop("pea", "Fabaceae", 1, 1, 3, 7, "spring", 1, 2),
        Crop("bean", "Fabaceae", 2, 1, 5, 9, "spring", 1, 1),
        Crop("sugar_beet", "Amaranthaceae", 3, 3, 4, 10, "spring", 1, 1, heavy_harvest=True),
        Crop("potato", "Solanaceae", 2, 1, 4, 9, "spring", 1, 1, heavy_harvest=True),
        Crop("alfalfa", "Fabaceae", 3, 3, 4, 9, "perennial", 3, 3),
        Crop("clover", "Fabaceae", 2, 2, 4, 9, "perennial", 3, 3),
        Crop("fallow", None, 0, 0, 0, None, "fallow", 1, 1),
    )
}

LABELS = {
    "maize_silage": "silage maize",
    "sugar_beet": "sugar beet",
}


def _label(crop: Crop) -> str:
    return LABELS.get(crop.name, crop.name)


def _gap_months(a: Crop, b: Crop) -> int | None:
    """Months between A's harvest and B's sowing (0 = same month)."""
    if a.harvest_month is None or b.season == "fallow":
        return None
    return (b.sow_month - a.harvest_month) % 12


def _gap_covers_winter(a: Crop, b: Crop) -> bool:
    """True if the months strictly between A's harvest and B's sowing include January."""
    gap = _gap_months(a, b)
    if gap is None or gap < 2:
        return False
    between = {(a.harvest_month + k - 1) % 12 + 1 for k in range(1, gap)}
    return 1 in between


class _Collector:
    def __init__(self):
        self.causes: dict[str, list[dict]] = {}

    def add(self, issue: str, severity: str, crops: list[Crop], reason: str, mitigation: str):
        self.causes.setdefault(issue, []).append(
            {
                "severity": severity,
                "crops": [c.name for c in crops],
                "reason": reason,
                "mitigation": mitigation,
            }
        )


def _single_crop_rules(crop: Crop, out: _Collector) -> None:
    name = _label(crop)
    if crop.season == "fallow":
        out.add(
            "erosion", HIGH, [crop],
            "Bare fallow leaves the soil without any cover, the worst case for erosion.",
            "Keep stubble or a cover crop on the field instead of bare fallow.",
        )
        return

    if crop.cover == 1:
        if crop.residue == 1:
            out.add(
                "erosion", HIGH, [crop],
                f"{name.capitalize()} is a row crop that leaves the soil open for months "
                "and little residue after harvest.",
                "Avoid steep slopes, plant across the slope, follow with a cover crop.",
            )
        else:
            out.add(
                "erosion", MEDIUM, [crop],
                f"{name.capitalize()} is a row crop: soil between the rows stays bare until "
                "the canopy closes.",
                "Plant across the slope, leave the residue on the field.",
            )
        out.add(
            "waterlogging", MEDIUM, [crop],
            f"Open soil under {name} crusts in heavy rain, so less water soaks in and "
            "more runs off or ponds.",
            "Reduce seedbed tillage, keep residue on the surface.",
        )

    if crop.heavy_harvest:
        out.add(
            "waterlogging", MEDIUM, [crop],
            f"{name.capitalize()} is harvested late with heavy machinery, often on wet "
            "soil, which compacts it; compacted soil drains poorly.",
            "Harvest only when the soil carries the load, use controlled traffic lanes.",
        )


def _step_rules(a: Crop, b: Crop, out: _Collector) -> None:
    gap = _gap_months(a, b)
    na, nb = _label(a), _label(b)

    # drought: A dried the subsoil and B is sown before it can refill
    if gap is not None and a.water_use == 3 and a.root_depth >= 2:
        severity = None
        if gap <= 2:
            severity = HIGH if a.root_depth == 3 else MEDIUM
        elif gap <= 5 and a.root_depth == 3:
            severity = MEDIUM
        if severity:
            when = (
                "in the same month as its harvest" if gap == 0
                else "about 1 month after its harvest" if gap == 1
                else f"about {gap} months after its harvest"
            )
            out.add(
                "drought", severity, [a, b],
                f"{na.capitalize()} uses a lot of water and dries the soil deeply; {nb} is "
                f"sown {when}, before rain can refill the subsoil.",
                f"Put a less demanding crop or a fallow between {na} and {nb} in dry "
                "years, keep residue to reduce evaporation.",
            )
            if severity == HIGH and b.season != "fallow" and b.harvest_month in range(6, 11):
                out.add(
                    "heat", MEDIUM, [a, b],
                    f"{nb.capitalize()} grows through summer on soil {na} left dry; "
                    "water-stressed plants suffer more in heatwaves.",
                    f"Choose an earlier-maturing {nb} variety or break the {na} -> {nb} "
                    "sequence.",
                )

    # waterlogging: compaction from A's heavy harvest
    if a.heavy_harvest and gap is not None:
        out.add(
            "waterlogging", HIGH if gap <= 1 else MEDIUM, [a, b],
            f"The heavy {na} harvest compacts the soil {nb} is grown in"
            + (", and it is sown right after, often on wet soil." if gap <= 1 else "."),
            "Loosen compacted layers before sowing, avoid traffic on wet soil.",
        )

    # erosion: bare soil over winter between an autumn harvest and a spring crop
    if b.season in ("spring", "perennial") and _gap_covers_winter(a, b) and a.residue < 3:
        out.add(
            "erosion", HIGH if a.residue == 1 else MEDIUM, [a, b],
            f"After {na} the field stays bare over winter until {nb} is sown, with "
            + ("little" if a.residue == 1 else "only some") + " residue to protect it.",
            "Sow a winter cover crop after the harvest.",
        )

    # erosion: two low-cover seasons in a row
    if a.cover == 1 and b.cover == 1:
        out.add(
            "erosion", HIGH if a.residue == 1 else MEDIUM, [a, b],
            f"{na.capitalize()} followed by {nb}: two low-cover seasons in a row, so the "
            "soil stays exposed for a long time.",
            f"Put a cereal, forage or cover crop between {na} and {nb}.",
        )

    # disease buildup
    if a.family is not None and a.family == b.family:
        if a.name == b.name:
            out.add(
                "disease_buildup", HIGH, [a, b],
                f"{na.capitalize()} after {na}: soil-borne diseases and pests build up, "
                "and weakened plants cope worse with drought and waterlogging.",
                f"Grow a crop from another plant family between two {na} crops.",
            )
        else:
            out.add(
                "disease_buildup", MEDIUM, [a, b],
                f"{na.capitalize()} and {nb} are both {a.family}; they share many diseases "
                "and pests.",
                "Alternate between plant families.",
            )


def _sequence_rules(crops: list[Crop], out: _Collector) -> None:
    if len(crops) < 2:
        return
    low_cover = [c for c in crops if c.cover == 1]
    share = len(low_cover) / len(crops)
    if share > 0.5:
        out.add(
            "erosion", HIGH if share == 1 else MEDIUM, low_cover,
            f"{len(low_cover)} of {len(crops)} crops leave the soil poorly covered; over the "
            "years topsoil is lost and soil structure declines.",
            "Add winter cereals, forage legumes or cover crops to the sequence.",
        )

    if len(crops) >= 3 and not any(c.season == "perennial" for c in crops):
        low_residue = [c for c in crops if c.residue == 1]
        if len(low_residue) / len(crops) > 0.5:
            out.add(
                "drought", MEDIUM, low_residue,
                "Most crops in the sequence return little residue and none is a perennial "
                "forage, so soil organic matter declines and the soil holds less water.",
                "Leave residues on the field, add alfalfa/clover or cover crops.",
            )


def rotation_risks(crop_names: list[str]) -> list[dict]:
    """Medium and high environmental risks of planting these crops in this order.

    Args:
        crop_names: one or more crop ids from CROPS, in planting order.

    Returns:
        One entry per issue ("drought", "heat", "waterlogging", "erosion",
        "disease_buildup") with its worst severity and every cause, worst
        issues first. Empty list if no medium or high risk was found.

    Raises:
        ValueError: empty list or unknown crop id.
    """
    if not crop_names:
        raise ValueError("give at least one crop")
    unknown = [n for n in crop_names if n.strip().lower() not in CROPS]
    if unknown:
        raise ValueError(f"unknown crop(s) {unknown}; known: {sorted(CROPS)}")
    crops = [CROPS[n.strip().lower()] for n in crop_names]

    out = _Collector()
    for crop in dict.fromkeys(crops):  # each distinct crop once
        _single_crop_rules(crop, out)
    for a, b in zip(crops, crops[1:]):
        _step_rules(a, b, out)
    _sequence_rules(crops, out)

    issues = []
    for issue, causes in out.causes.items():
        causes.sort(key=lambda c: -SEVERITY_RANK[c["severity"]])
        issues.append(
            {
                "issue": issue,
                "severity": causes[0]["severity"],
                "causes": causes,
            }
        )
    issues.sort(key=lambda i: (-SEVERITY_RANK[i["severity"]], i["issue"]))
    return issues
