import math
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from app.services import agronomy, drought
from app.services.agronomy import AGRONOMY_CSV, Frost, crops, load, water_crop
from app.services.weather import DailySeries

SEASON = 2024
PAGE_CROPS = {
    "wheat", "barley", "rye", "oat", "maize", "sorghum", "buckwheat", "foxtail_millet", "sunflower",
    "rapeseed", "soybean", "flax", "dry_pea", "chickpea", "phaseolus_bean", "sugarbeet", "white_potato",
    "tobacco", "alfalfa", "cotton", "cabbage", "carrot", "onion", "tomato",
}
COVER_CROPS = {"vetch_oats", "pea_oats", "clover_radish", "phacelia", "mustard", "cereal_rye", "crimson_clover"}


def synthetic_weather() -> DailySeries:
    start = date(SEASON - 1, 1, 1)
    dates = [start + timedelta(days=i) for i in range((date(SEASON, 12, 31) - start).days + 1)]
    season = [math.cos(2 * math.pi * (d.timetuple().tm_yday - 200) / 365) for d in dates]
    values = {
        "tmax": [17 + 13 * s for s in season],
        "tmin": [6 + 10 * s for s in season],
        "rh_max": [90.0] * len(dates),
        "rh_min": [65 - 15 * s for s in season],
        "wind10": [2.5] * len(dates),
        "rs": [13 + 9 * s for s in season],
        "precip": [1.6] * len(dates),
    }
    return DailySeries("synthetic", dates, values, [(43.5, 26.0)], 43.5, 150.0)


WEATHER = synthetic_weather()
ET0 = drought.et0_series(WEATHER)


def simulate(crop: drought.Crop) -> dict:
    return drought.simulate_season(crop, SEASON, WEATHER.dates, ET0, WEATHER.values["precip"])["summary"]


def csv_with(row_edit) -> Path:
    lines = AGRONOMY_CSV.read_text().splitlines()
    header = lines[0].split(",")
    row = dict(zip(header, lines[1].split(",")))
    row_edit(row)
    path = Path(tempfile.mkdtemp()) / "agronomy.csv"
    path.write_text("\n".join([lines[0], ",".join(row[h] for h in header), lines[1]]) + "\n")
    return path


class TableTests(unittest.TestCase):
    def test_csv_validates_with_expected_crops(self):
        table = crops()
        self.assertEqual(set(table), PAGE_CROPS | COVER_CROPS)
        self.assertTrue(all(table[c].role == "cover" for c in COVER_CROPS))
        self.assertEqual(table["alfalfa"].role, "forage")

    def test_errors_name_row_and_column(self):
        def bad_date(row):
            row["sow_end"] = "13-40"

        with self.assertRaisesRegex(ValueError, r"row 2 \(wheat\), column sow_end"):
            load(csv_with(bad_date))

        def bad_range(row):
            row["kc_mid"] = "3.0"

        with self.assertRaisesRegex(ValueError, r"row 2 \(wheat\), column kc_mid"):
            load(csv_with(bad_range))

        def short_season(row):
            row["l_ini"] = "60"

        with self.assertRaisesRegex(ValueError, r"row 2 \(wheat\), column l_late: .*harvest window"):
            load(csv_with(short_season))

    def test_duplicate_id_is_rejected(self):
        with self.assertRaisesRegex(ValueError, r"row 3 \(wheat\), column crop_id: duplicate"):
            load(csv_with(lambda row: None))


class WaterCropTests(unittest.TestCase):
    def test_every_crop_simulates_with_sane_water_use(self):
        for crop_id, crop in crops().items():
            with self.subTest(crop=crop_id):
                summary = simulate(water_crop(crop))
                self.assertGreater(summary["etc_mm"], 0)
                self.assertGreaterEqual(summary["eta_mm"], 0)
                if crop.role == "cover":
                    continue
                low, high = (300, 600) if crop.winter else (250, 750)
                self.assertTrue(low <= summary["etc_mm"] <= high, f"{crop_id}: {summary['etc_mm']} mm")

    def test_wheat_matches_drought_reference(self):
        reference = drought.CROPS["wheat"]
        table = water_crop("wheat")
        season = 2023
        sow, harvest = reference.window(season)
        self.assertEqual(table.window(season), (sow, harvest))
        d = sow
        while d <= harvest:
            self.assertAlmostEqual(
                drought._kc(table, season, d), drought._kc(reference, season, d), delta=0.05, msg=d.isoformat()
            )
            d += timedelta(days=1)

    def test_frost_shifts_winter_sowing_three_weeks_before_first_frost(self):
        wheat = crops()["wheat"]
        shifted = water_crop(wheat, Frost(last_spring=(4, 10), first_autumn=(10, 31)))
        self.assertEqual(agronomy.sowing_shift_days(wheat, Frost((4, 10), (10, 31))), -15)
        self.assertEqual(shifted.sow, (9, 30))
        self.assertEqual(shifted.harvest, (6, 20))

    def test_frost_shifts_summer_sowing_after_last_spring_frost(self):
        maize = crops()["maize"]
        shifted = water_crop(maize, Frost(last_spring=(4, 20), first_autumn=(10, 31)))
        self.assertEqual(agronomy.sowing_shift_days(maize, Frost((4, 20), (10, 31))), 10)
        self.assertEqual(shifted.sow, (5, 2))
        self.assertEqual(shifted.harvest, (10, 9))
        simulate(shifted)

    def test_frost_leaves_summer_sown_catch_crops_alone(self):
        self.assertEqual(agronomy.sowing_shift_days(crops()["phacelia"], Frost((4, 20), (10, 1))), 0)


if __name__ == "__main__":
    unittest.main()
