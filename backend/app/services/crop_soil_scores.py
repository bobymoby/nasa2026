"""How each crop affects the soil, from the crop_soil_scores table
(loaded by app.import_crop_csvs)."""

from dataclasses import dataclass

from app.db import pool

# The crop_soil_scores row for each field crop FAO GAEZ rates (gaez.CROP_GROUPS),
# so the crops page can show both. GAEZ has no season, so the form usually
# grown in Bulgaria is used. Napier grass, reed canary grass, cowpea, gram and
# pigeonpea have no row.
GAEZ_SOIL_CROPS = {
    "whe": "winter_common_soft_wheat",
    "brl": "barley",
    "mze": "grain_maize_corn_popcorn",
    "rye": "rye",
    "oat": "oats",
    "srg": "sorghum",
    "bck": "buckwheat",
    "fml": "millet",
    "pml": "millet",
    "rcd": "rice",
    "rcw": "rice",
    "sfl": "sunflower",
    "rsd": "winter_rapeseed_rape",
    "soy": "soy_soybeans",
    "grd": "peanuts",
    "flx": "flax_linseed",
    "pea": "peas",
    "chk": "chickpeas",
    "phb": "beans",
    "sub": "sugar_beet",
    "wpo": "potatoes",
    "spo": "sweet_potatoes",
    "cab": "white_cabbage",
    "car": "carrots_daucus",
    "oni": "onions",
    "tom": "tomato",
    "alf": "alfalfa_lucerne",
    "cot": "cotton",
    "tob": "tobacco",
}


@dataclass(frozen=True)
class SoilScore:
    crop: str
    score: int | None
    nitrogen_added: int | None
    organic_matter: int | None
    soil_cover: float | None
    soil_disturbance: int | None
    break_crop: int | None
    removal_compaction: int | None
    family: str | None
    note: str | None


def all_soil_scores() -> list[SoilScore]:
    """Rows without a score aren't crops and are left out."""
    with pool.connection(timeout=5) as conn:
        rows = conn.execute(
            """
            SELECT crop, score_0_100, nitrogen_added, organic_matter,
                   soil_cover, soil_disturbance, break_crop, removal_compaction,
                   family, note
            FROM crop_soil_scores
            WHERE score_0_100 IS NOT NULL
            ORDER BY crop
            """
        ).fetchall()
    return [
        SoilScore(
            crop=crop,
            score=score,
            nitrogen_added=n,
            organic_matter=r,
            soil_cover=float(c) if c is not None else None,
            soil_disturbance=d,
            break_crop=b,
            removal_compaction=x,
            family=family,
            note=note,
        )
        for crop, score, n, r, c, d, b, x, family, note in rows
    ]


def hcat3_codes(crops: list[str]) -> dict[str, int]:
    """HCAT3 code of each crop key that has one."""
    with pool.connection(timeout=5) as conn:
        rows = conn.execute(
            "SELECT crop, hcat3_code FROM crop_soil_scores"
            " WHERE crop = ANY(%s) AND hcat3_code IS NOT NULL",
            (crops,),
        ).fetchall()
    return dict(rows)
