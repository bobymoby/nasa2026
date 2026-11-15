"""One-off: load the crop CSVs from `luben data/` into Postgres.

    cd backend && ../.venv/bin/python -m app.import_crop_csvs [csv_dir]

- crop_soil_scores.csv -> crop_soil_scores (one row per crop)

Safe to re-run: each table is emptied and reloaded in one transaction. HCAT
codes are bigint, like bulgaria_data."EC_hcat_c", so the tables join on them.
"""

import sys
from pathlib import Path

from app.db import pool

DEFAULT_DIR = Path(__file__).resolve().parents[2] / "luben data"

TABLES = {
    "crop_soil_scores": (
        "crop_soil_scores.csv",
        """
        CREATE TABLE IF NOT EXISTS crop_soil_scores (
            crop        text PRIMARY KEY,
            hcat4_code  bigint,
            hcat3_code  bigint,
            -- null for rows that aren't crops (see note)
            score_0_100 smallint,
            points      numeric(4, 1),
            -- the CSV's N, R, C, D, B, X component scores, in that order
            nitrogen_added     smallint,
            organic_matter     smallint,
            soil_cover         numeric(3, 1),
            soil_disturbance   smallint,
            break_crop         smallint,
            removal_compaction smallint,
            family      text,
            note        text
        );
        CREATE INDEX IF NOT EXISTS crop_soil_scores_hcat3_idx ON crop_soil_scores (hcat3_code);
        COMMENT ON COLUMN crop_soil_scores.nitrogen_added IS
            'N: nitrogen the crop adds to the soil (legumes), 0..3';
        COMMENT ON COLUMN crop_soil_scores.organic_matter IS
            'R: roots and leftover plant material that build organic matter, 0..3';
        COMMENT ON COLUMN crop_soil_scores.soil_cover IS
            'C: how long the soil stays covered by living plants (erosion protection), 0..3';
        COMMENT ON COLUMN crop_soil_scores.soil_disturbance IS
            'D: soil disturbance from ploughing or digging at harvest, -3..0';
        COMMENT ON COLUMN crop_soil_scores.break_crop IS
            'B: break-crop value, interrupts disease cycles and feeds soil life, 0..2';
        COMMENT ON COLUMN crop_soil_scores.removal_compaction IS
            'X: whole-plant removal or soil compaction at harvest, -2..0';
        """,
        "crop, hcat4_code, hcat3_code, score_0_100, points, nitrogen_added, organic_matter,"
        " soil_cover, soil_disturbance, break_crop, removal_compaction, family, note",
    ),
}


def main() -> None:
    csv_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DIR
    pool.open(wait=True, timeout=10)
    try:
        with pool.connection() as conn:
            for table, (filename, ddl, columns) in TABLES.items():
                conn.execute(ddl)
                conn.execute(f"TRUNCATE {table}")
                # the CSVs' column order matches `columns`; empty fields become NULL
                with conn.cursor().copy(
                    f"COPY {table} ({columns}) FROM STDIN WITH (FORMAT csv, HEADER true)"
                ) as copy, open(csv_dir / filename, "rb") as f:
                    while chunk := f.read(1 << 16):
                        copy.write(chunk)
                count = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                print(f"{table}: {count} rows from {filename}")
    finally:
        pool.close()


if __name__ == "__main__":
    main()
