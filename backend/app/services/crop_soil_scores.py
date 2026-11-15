"""How each crop affects the soil, from the crop_soil_scores table
(loaded by app.import_crop_csvs), looked up by HCAT3 code."""

from dataclasses import dataclass

from app.db import pool


@dataclass(frozen=True)
class SoilScore:
    crop: str
    # null for rows that aren't crops
    score: int | None
    nitrogen_added: int | None  # 0..3
    organic_matter: int | None  # 0..3
    soil_cover: float | None  # 0..3
    soil_disturbance: int | None  # -3..0
    break_crop: int | None  # 0..2
    removal_compaction: int | None  # -2..0
    family: str | None
    note: str | None


def all_soil_scores() -> list[SoilScore]:
    """Every scored crop; rows that aren't crops (no score) are left out."""
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
