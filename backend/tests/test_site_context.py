import unittest
from datetime import date, timedelta
from unittest.mock import patch

import numpy as np
from fastapi import FastAPI
from fastapi.testclient import TestClient
from shapely.geometry import box

from app.routers import fields as fields_router
from app.services import site_context as sc
from app.services.country import country_at

FIELD = [(43.612, 27.931), (43.612, 27.935), (43.611, 27.933)]


class CountryTests(unittest.TestCase):
    def test_points_on_land_and_at_sea(self):
        self.assertEqual(country_at(42.70, 23.32), "BG")
        self.assertEqual(country_at(44.82, 20.46), "RS")
        self.assertIsNone(country_at(43.2, 29.6))


class FormulaTests(unittest.TestCase):
    def test_saxton_rawls_loam(self):
        self.assertAlmostEqual(sc.awc_saxton_rawls(40, 20, 2.5 / 1.724 * 10), 142.6, delta=0.5)

    def test_more_clay_and_organic_matter_hold_more_water(self):
        self.assertGreater(sc.awc_saxton_rawls(20, 30, 20), sc.awc_saxton_rawls(80, 5, 5))

    def test_epic_k_hand_computed_silt_loam(self):
        self.assertAlmostEqual(sc.k_factor_epic(20, 15, 15), 0.0251, delta=0.0005)
        self.assertLess(sc.k_factor_epic(85, 5, 5), sc.k_factor_epic(20, 15, 15))

    def test_slope_of_a_five_percent_plane(self):
        w, s, e, n = 27.0, 43.0, 27.01, 43.01
        dy = (n - s) / 40 * 110_540
        dem = np.tile(np.arange(40)[:, None] * dy * 0.05, (1, 40)).astype("float32")
        slope, ls = sc.slope_stats(dem, (w, s, e, n), box(w + 0.002, s + 0.002, e - 0.002, n - 0.002))
        self.assertAlmostEqual(slope, 5.0, delta=0.05)
        self.assertGreater(ls, 0.5)
        self.assertEqual(sc.slope_stats(np.zeros((10, 10), "float32"), (w, s, e, n), box(w, s, e, n))[0], 0.0)


class ClimateTests(unittest.TestCase):
    def test_frost_dates_take_the_80_percent_years(self):
        dates, tmin = [], []
        for i, year in enumerate(range(2000, 2005)):
            day = date(year, 1, 1)
            while day.year == year:
                spring_last = date(year, 4, 1 + i)
                autumn_first = date(year, 10, 20 + i)
                cold = (day <= spring_last) or (day >= autumn_first)
                dates.append(day)
                tmin.append(-2.0 if cold else 8.0)
                day += timedelta(days=1)
        frost = sc.frost_dates(dates, tmin)
        self.assertEqual(frost["last_spring"][:2], "04")
        self.assertGreaterEqual(frost["last_spring"], "04-04")
        self.assertLessEqual(frost["first_autumn"], "10-21")

    def test_monthly_normals_average_monthly_totals(self):
        dates = [date(2000, 1, 1) + timedelta(days=i) for i in range(366 * 2)]
        months = sc.monthly_normals(dates, [5.0] * len(dates), [1.0] * len(dates), [2.0] * len(dates))
        self.assertEqual(months[0], {"month": 1, "tmean_c": 5.0, "precip_mm": 31.0, "et0_mm": 62.0})


class HistoryTests(unittest.TestCase):
    TODAY = date(2026, 11, 15)

    def period(self, start, end, crop_type, kind="crop"):
        return {"start": start, "end": end, "type": kind, "crop_type": crop_type, "confidence": "high"}

    def test_latest_finished_crop_decides(self):
        periods = [self.period("2025-10-10", "2026-07-05", "winter"), self.period("2026-07-06", "2026-11-14", None, "barren")]
        self.assertEqual(sc.previous_season(periods, self.TODAY)["type"], "winter_crop")
        periods = [self.period("2026-04-20", "2026-09-25", "summer")]
        self.assertEqual(sc.previous_season(periods, self.TODAY)["type"], "summer_crop")

    def test_old_crop_means_fallow_and_none_means_unknown(self):
        self.assertEqual(sc.previous_season([self.period("2024-04-20", "2024-09-25", "summer")], self.TODAY)["type"], "fallow")
        self.assertEqual(sc.previous_season([], self.TODAY)["type"], "unknown")


SITE = sc.PlotSite(10.0, 40.0, 20.0, 150.0, 0.03, 2.0, 0.4)


class AssemblyTests(unittest.TestCase):
    def setUp(self):
        sc._memory.clear()

    @patch.object(sc, "_prices", lambda country: {"price_year": 2025, "crops": {}})
    @patch.object(sc, "_gaez", lambda polys: [{"wheat": {"suitability": 60.0, "yield_kg_ha": 5000}}])
    @patch.object(sc, "_history", lambda sh, pid, pts, today: {"type": "winter_crop", "end": "2026-07-05", "confidence": "high"})
    @patch.object(sc, "plot_site", lambda sh, key, poly: (SITE, []))
    def test_a_failing_part_is_none_with_an_error(self):
        def broken(rect):
            raise RuntimeError("ERA5 down")

        with patch.object(sc, "climate_normals", broken):
            ctx = sc.site_context(None, [("f1", FIELD)])
        self.assertEqual(ctx["country"], "BG")
        self.assertIsNone(ctx["climate"])
        self.assertEqual(ctx["errors"], ["climate: ERA5 down"])
        self.assertEqual(ctx["plots"][0]["site"]["awc_mm_per_m"], 150.0)
        self.assertEqual(ctx["plots"][0]["previous_season"]["type"], "winter_crop")
        self.assertEqual(ctx["plots"][0]["crops"]["wheat"]["yield_kg_ha"], 5000)

    def test_plot_inputs_are_fetched_once(self):
        calls = []

        def fetch(sh, poly, errors):
            calls.append(poly)
            return SITE

        with patch.object(sc, "_read_plot", lambda key: None), patch.object(sc, "_write_plot", lambda key, site: None), \
                patch.object(sc, "_fetch_plot", fetch):
            poly = sc.plot_polygon(FIELD)
            first, _ = sc.plot_site(None, "field:f1:b0", poly)
            second, _ = sc.plot_site(None, "field:f1:b0", poly)
        self.assertEqual((first, second, len(calls)), (SITE, SITE, 1))

    def test_failed_fetch_is_not_kept(self):
        def fetch(sh, poly, errors):
            errors.append("soil: rate limited")
            return sc.PlotSite(None, None, None, None, None, 2.0, 0.4)

        with patch.object(sc, "_read_plot", lambda key: None), patch.object(sc, "_write_plot", lambda key, site: None), \
                patch.object(sc, "_fetch_plot", fetch):
            _, errors = sc.plot_site(None, "field:f2:b0", sc.plot_polygon(FIELD))
        self.assertEqual(errors, ["soil: rate limited"])
        self.assertNotIn("field:f2:b0", sc._memory)


app = FastAPI()
app.include_router(fields_router.router, prefix="/api")
app.dependency_overrides[fields_router.Client] = lambda: None


class EndpointTests(unittest.TestCase):
    def test_prefetch_returns_at_once(self):
        with patch.object(sc, "prefetch", lambda sh, outlines: None):
            res = TestClient(app).post("/api/fields/strategy-context/prefetch", json={"plots": [FIELD]})
        self.assertEqual((res.status_code, res.json()), (202, {"queued": 1}))


if __name__ == "__main__":
    unittest.main()
