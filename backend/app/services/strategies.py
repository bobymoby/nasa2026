"""Crop rotation strategies.

Hard-coded for now: a handful of multi-year plans written for a Dobrudzha-like
climate (cold winters, dry hot summers, chernozem soils). The router ranks them
by how many of the requested crops they use; the area isn't used yet.
"""

from dataclasses import dataclass, field
from typing import Literal

Role = Literal["cash", "cover", "forage"]
Trait = Literal["nitrogen_fixing", "deep_rooting", "drought_tolerant", "cover"]
Level = Literal["low", "medium", "high"]


@dataclass(frozen=True)
class Planting:
    crop_id: str
    crop_name: str
    role: Role
    # rotation year (1-based) and calendar month (1-12), both inclusive
    start: tuple[int, int]
    end: tuple[int, int]
    # total over the whole planting, not per year
    water_use_mm: int
    traits: tuple[Trait, ...] = ()
    note: str = ""


@dataclass(frozen=True)
class Rating:
    score: int  # 1-5, higher is better
    summary: str


@dataclass(frozen=True)
class Assessment:
    level: Level
    summary: str


@dataclass(frozen=True)
class CropRole:
    importance: Level
    summary: str


@dataclass(frozen=True)
class Strategy:
    id: str
    name: str
    summary: str
    plantings: tuple[Planting, ...]
    nutrient_cycling: Rating
    organic_matter: Rating
    pest_disruption: Rating
    forage_potential: Rating
    market: Rating
    soil_improvement: Rating
    erosion_risk: Assessment
    water_use: Assessment
    nitrogen_fixing: CropRole
    cover_crops: CropRole
    deep_rooting: CropRole
    drought_tolerant: CropRole
    market_considerations: tuple[str, ...] = field(default=())

    @property
    def duration_years(self) -> int:
        return max(p.end[0] for p in self.plantings)

    def water_by_year(self) -> list[int]:
        """Each planting's water use spread evenly over its months, summed per year."""
        totals = [0.0] * self.duration_years
        for p in self.plantings:
            months = _month_index(p.end) - _month_index(p.start) + 1
            per_month = p.water_use_mm / months
            for m in range(_month_index(p.start), _month_index(p.end) + 1):
                totals[m // 12] += per_month
        return [round(t) for t in totals]


def _month_index(at: tuple[int, int]) -> int:
    year, month = at
    return (year - 1) * 12 + (month - 1)


STRATEGIES: tuple[Strategy, ...] = (
    Strategy(
        id="wheat-sunflower-legume",
        name="Wheat–sunflower with a legume break",
        summary=(
            "A four-year cereal–oilseed rotation that slips a vetch–oat cover "
            "between wheat and sunflower to feed nitrogen into the next crops "
            "and keep the soil covered over winter."
        ),
        plantings=(
            Planting("wheat", "Winter wheat", "cash", (1, 10), (2, 7), 450,
                     note="Sown after the first autumn rains."),
            Planting("vetch_oats", "Vetch + oats", "cover", (2, 8), (3, 3), 120,
                     ("nitrogen_fixing", "cover"),
                     "Terminated in March, 4–6 weeks before sunflower."),
            Planting("sunflower", "Sunflower", "cash", (3, 4), (3, 9), 480,
                     ("deep_rooting", "drought_tolerant")),
            Planting("barley", "Winter barley", "cash", (3, 10), (4, 6), 380,
                     note="Early harvest leaves time for a summer cover."),
        ),
        nutrient_cycling=Rating(4, "Vetch fixes ~60–90 kg N/ha; sunflower roots recover nitrate leached below the wheat root zone."),
        organic_matter=Rating(3, "Cereal straw plus one cover crop; moderate residue return."),
        pest_disruption=Rating(4, "Never two cereals back to back; the broadleaf break interrupts take-all and cereal aphids."),
        forage_potential=Rating(3, "The vetch–oat mix can be grazed or cut as hay if spring is wet."),
        market=Rating(4, "All three cash crops trade in bulk at Varna and Constanța ports."),
        soil_improvement=Rating(4, "Winter cover and deep sunflower roots improve structure and water infiltration."),
        erosion_risk=Assessment("low", "Ground is covered for all but ~5 weeks a year."),
        water_use=Assessment("medium", "Sunflower draws deep reserves; the cover is killed early to protect them."),
        nitrogen_fixing=CropRole("high", "The vetch break is the main nitrogen source; cuts mineral N for sunflower by ~40 kg/ha."),
        cover_crops=CropRole("high", "Keeps the bare post-wheat window from August to March protected."),
        deep_rooting=CropRole("medium", "Sunflower reaches 2 m and recycles leached nutrients."),
        drought_tolerant=CropRole("medium", "Sunflower handles July–August dry spells better than maize."),
        market_considerations=(
            "Sunflower oil demand is stable; prices track the Black Sea export market.",
            "Malting barley earns a premium if protein stays under 11.5%.",
            "Cover seed adds ~€60/ha in costs, partly offset by lower N fertiliser.",
        ),
    ),
    Strategy(
        id="drought-resilient",
        name="Drought-resilient pulse rotation",
        summary=(
            "Built around dry summers: sunflower and chickpea do the heavy "
            "lifting, and a short phacelia cover protects the soil without "
            "spending much water."
        ),
        plantings=(
            Planting("sunflower", "Sunflower", "cash", (1, 4), (1, 9), 480,
                     ("deep_rooting", "drought_tolerant")),
            Planting("wheat", "Winter wheat", "cash", (1, 10), (2, 7), 450),
            Planting("phacelia", "Phacelia", "cover", (2, 8), (2, 11), 90,
                     ("cover",), "Frost-killed; no herbicide needed."),
            Planting("chickpea", "Chickpea", "cash", (3, 3), (3, 7), 300,
                     ("nitrogen_fixing", "drought_tolerant")),
            Planting("barley", "Winter barley", "cash", (3, 10), (4, 6), 380),
        ),
        nutrient_cycling=Rating(4, "Chickpea leaves 30–50 kg N/ha for the following barley."),
        organic_matter=Rating(2, "Low-residue pulses and a light cover add little carbon."),
        pest_disruption=Rating(4, "Three plant families; phacelia also attracts aphid predators."),
        forage_potential=Rating(1, "No forage component."),
        market=Rating(3, "Chickpea prices are good but the local buyer base is thin."),
        soil_improvement=Rating(3, "Gains mostly from the legume; little added structure."),
        erosion_risk=Assessment("medium", "Bare from November to March before chickpea."),
        water_use=Assessment("low", "Lowest water draw of the set; suits <450 mm rainfall years."),
        nitrogen_fixing=CropRole("medium", "Chickpea fixes less than vetch or soybean but needs no irrigation."),
        cover_crops=CropRole("medium", "One short cover; longer covers would cost too much stored water."),
        deep_rooting=CropRole("medium", "Sunflower opens deep channels for the wheat that follows."),
        drought_tolerant=CropRole("high", "The point of this plan: two of four cash crops tolerate drought."),
        market_considerations=(
            "Chickpea needs a contract buyer; spot markets in Bulgaria are small.",
            "Lower input costs keep margins steady in dry years.",
        ),
    ),
    Strategy(
        id="alfalfa-ley",
        name="Alfalfa ley with rapeseed and wheat",
        summary=(
            "Two and a half years of alfalfa rebuild soil and feed livestock, "
            "followed by high-value rapeseed and wheat that use the stored "
            "nitrogen."
        ),
        plantings=(
            Planting("alfalfa", "Alfalfa", "forage", (1, 4), (3, 8), 1500,
                     ("nitrogen_fixing", "deep_rooting", "drought_tolerant"),
                     "3–4 cuts a year once established."),
            Planting("rapeseed", "Winter rapeseed", "cash", (3, 9), (4, 7), 500,
                     ("deep_rooting",)),
            Planting("wheat", "Winter wheat", "cash", (4, 10), (5, 7), 450),
            Planting("mustard", "White mustard", "cover", (5, 8), (5, 11), 80,
                     ("cover",), "Biofumigant against soil pests."),
        ),
        nutrient_cycling=Rating(5, "Alfalfa fixes 150+ kg N/ha a year; rapeseed and wheat need little added N."),
        organic_matter=Rating(5, "Perennial roots and no tillage for 2+ years add the most carbon."),
        pest_disruption=Rating(5, "A long perennial break and mustard biofumigation reset weed and pest cycles."),
        forage_potential=Rating(5, "8–12 t/ha of hay a year during the ley."),
        market=Rating(3, "Hay needs a nearby livestock buyer; rapeseed is a strong cash crop."),
        soil_improvement=Rating(5, "Best structure and infiltration of all plans."),
        erosion_risk=Assessment("low", "Permanent cover for the first 2.5 years."),
        water_use=Assessment("high", "Alfalfa uses the most water; the deep roots draw on subsoil reserves."),
        nitrogen_fixing=CropRole("high", "Alfalfa supplies most of the rotation's nitrogen."),
        cover_crops=CropRole("medium", "The ley itself is the cover; mustard covers the gap after wheat."),
        deep_rooting=CropRole("high", "Alfalfa roots reach 3–4 m and break up compacted layers."),
        drought_tolerant=CropRole("medium", "Established alfalfa survives drought but yields drop sharply."),
        market_considerations=(
            "Only viable with a livestock farm or hay buyer within ~50 km.",
            "Rapeseed has the highest gross margin of the cash crops here.",
            "No grain income in years 1–2; cash flow is uneven.",
        ),
    ),
    Strategy(
        id="maize-soy-wheat",
        name="Maize–soybean–wheat with winter covers",
        summary=(
            "A higher-yield, higher-input plan for wetter years or irrigated "
            "land, with a cover after every cash crop."
        ),
        plantings=(
            Planting("maize", "Maize", "cash", (1, 4), (1, 9), 600),
            Planting("rye", "Cereal rye", "cover", (1, 10), (2, 4), 100,
                     ("cover",), "Rolled before soybean is planted."),
            Planting("soybean", "Soybean", "cash", (2, 5), (2, 9), 500,
                     ("nitrogen_fixing",)),
            Planting("wheat", "Winter wheat", "cash", (2, 10), (3, 7), 450),
            Planting("clover_radish", "Crimson clover + radish", "cover", (3, 8), (3, 11), 90,
                     ("nitrogen_fixing", "deep_rooting", "cover")),
        ),
        nutrient_cycling=Rating(4, "Soybean and clover add N; rye catches what maize leaves behind."),
        organic_matter=Rating(4, "Maize stover plus two covers return lots of residue."),
        pest_disruption=Rating(3, "Corn rootworm is broken by soybean, but the plan is only three years."),
        forage_potential=Rating(2, "Rye can be grazed in early spring."),
        market=Rating(5, "Maize, soybean and wheat have the deepest markets."),
        soil_improvement=Rating(4, "Radish tap roots relieve compaction; covers protect structure."),
        erosion_risk=Assessment("low", "Covered almost year-round."),
        water_use=Assessment("high", "Maize and soybean are thirsty; rainfed yields swing a lot."),
        nitrogen_fixing=CropRole("high", "Soybean and clover cover the wheat's nitrogen needs."),
        cover_crops=CropRole("high", "Two covers keep roots in the soil through both winters."),
        deep_rooting=CropRole("low", "Only the short radish cover roots deeply."),
        drought_tolerant=CropRole("low", "No drought-tolerant crop; risky without irrigation."),
        market_considerations=(
            "Non-GM soybean earns a premium in EU feed markets.",
            "Maize drying costs eat into margins in wet autumns.",
        ),
    ),
    Strategy(
        id="short-wheat-sunflower",
        name="Two-year wheat–sunflower",
        summary=(
            "The simplest plan: alternate sunflower and wheat, with a pea–oat "
            "cover to fill the summer gap."
        ),
        plantings=(
            Planting("sunflower", "Sunflower", "cash", (1, 4), (1, 9), 480,
                     ("deep_rooting", "drought_tolerant")),
            Planting("wheat", "Winter wheat", "cash", (1, 10), (2, 7), 450),
            Planting("pea_oats", "Field pea + oats", "cover", (2, 8), (2, 11), 100,
                     ("nitrogen_fixing", "cover")),
        ),
        nutrient_cycling=Rating(3, "The pea cover adds some N; otherwise relies on fertiliser."),
        organic_matter=Rating(2, "Little residue beyond wheat straw."),
        pest_disruption=Rating(2, "Sunflower every other year raises broomrape and Sclerotinia risk."),
        forage_potential=Rating(2, "The pea–oat cover can be grazed in autumn."),
        market=Rating(4, "Two liquid, well-known commodities."),
        soil_improvement=Rating(2, "Maintains rather than improves soil."),
        erosion_risk=Assessment("medium", "Bare from November to April before sunflower."),
        water_use=Assessment("medium", "Typical rainfed water draw for the region."),
        nitrogen_fixing=CropRole("medium", "Only the short pea cover fixes nitrogen."),
        cover_crops=CropRole("medium", "One cover a cycle; extending it into spring would help erosion."),
        deep_rooting=CropRole("medium", "Sunflower every other year."),
        drought_tolerant=CropRole("medium", "Sunflower handles dry summers."),
        market_considerations=(
            "Easy to sell and to manage with existing machinery.",
            "Disease pressure may force a longer break within a few cycles.",
        ),
    ),
)


# HCAT codes are hierarchical; the last two digits are the season variant
# (00 any, 01 winter, 02 spring, 99 unspecified), so code // 100 is the crop
# itself. A requested crop matches a planting if their crop codes agree.
HCAT_CROPS = {
    "wheat": (33010101, 33010102),  # common soft and durum wheat
    "rye": (33010103,),
    "barley": (33010104,),
    "maize": (33010106,),  # grain maize
    "chickpea": (33010202,),
    "rapeseed": (33010604,),
    "sunflower": (33010605,),
    "alfalfa": (33010903,),
    "soybean": (33011600,),
}


def _uses(strategy: Strategy, crop: str, hcat3: int | None) -> bool:
    return any(
        p.crop_id == crop
        or (hcat3 is not None and hcat3 // 100 in HCAT_CROPS.get(p.crop_id, ()))
        for p in strategy.plantings
    )


def strategies_for(
    crops: list[str], hcat3_by_crop: dict[str, int] | None = None
) -> list[tuple[Strategy, list[str]]]:
    """All strategies with the requested crops each one uses, best match first.

    `crops` are crop_soil_scores keys (matched by HCAT code) or the plans'
    own ids like "wheat".
    """
    codes = hcat3_by_crop or {}
    wanted = set(crops)
    matched = [
        (s, [c for c in crops if _uses(s, c, codes.get(c))])
        for s in STRATEGIES
    ]
    # stable sort keeps the hand-picked order among equal matches
    return sorted(matched, key=lambda m: -len(set(m[1]) & wanted))
