import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import strategies as router
from app.services import strategy_economics as econ
from app.services.prices.pipeline import CropMarket, Market
from app.services.strategies import CROP_BY_ID
from app.services.strategy_inputs import SiteInputs


def priced(price, yield_t_ha):
    return CropMarket(price, (2020, 2024), "eurostat", "high", yield_t_ha, (2021, 2025), "eurostat")


MARKET = Market("BG", 2025, {
    "wheat": priced(250.0, 6.0),
    "dry_pea": priced(300.0, 2.0),
    "sunflower": CropMarket(yield_t_ha=2.0, yield_years=(2021, 2025), yield_source="eurostat"),
})


def planting(crop_id, role="cash", start=1, end=1):
    return {
        "crop_id": crop_id,
        "crop_name": crop_id.title(),
        "role": role,
        "start": {"year": start, "month": 10},
        "end": {"year": end, "month": 7},
    }


class EconomicsTests(unittest.TestCase):
    def test_revenue_is_yield_times_price(self):
        e = econ.strategy_economics([planting("wheat")], 1, MARKET)
        wheat = e.plantings[0]
        self.assertEqual((wheat.yield_t_ha, wheat.price_eur_t, wheat.revenue), (6.0, 250.0, 1500.0))
        self.assertEqual(wheat.cost, econ.SEED_COSTS_EUR_HA["wheat"] + econ.OPERATING_COSTS_EUR_HA["wheat"])
        self.assertEqual(wheat.source, "price eurostat 2020–2024, yield eurostat 2021–2025")
        self.assertEqual((e.country, e.price_year, e.complete), ("BG", 2025, True))

    def test_field_pea_uses_dry_pea_market(self):
        e = econ.strategy_economics([planting("field_pea")], 1, MARKET)
        self.assertEqual(e.plantings[0].revenue, 600.0)

    def test_missing_price_or_yield_earns_nothing_and_is_flagged(self):
        e = econ.strategy_economics([planting("wheat", start=1, end=2), planting("sunflower", start=3, end=3),
                                     planting("oat", start=4, end=4)], 4, MARKET)
        sunflower, oat = e.plantings[1], e.plantings[2]
        self.assertEqual((sunflower.revenue, sunflower.yield_t_ha, sunflower.price_eur_t), (0.0, 2.0, None))
        self.assertEqual((oat.source, oat.revenue, oat.cost), (econ.NO_MARKET_DATA, 0.0, 0.0))
        self.assertFalse(e.complete)
        self.assertEqual(e.missing_data, ["Sunflower", "Oat"])
        self.assertEqual(e.total_cost, e.plantings[0].cost)
        self.assertEqual([round(r) for r, _ in e.by_year()], [0, 1500, 0, 0])

    def test_crop_without_cost_estimate_is_left_out(self):
        market = Market("BG", 2025, {"lentil": priced(600.0, 1.3)})
        e = econ.strategy_economics([planting("lentil")], 1, market)
        self.assertEqual((e.plantings[0].source, e.revenue, e.total_cost), (econ.NO_COST_DATA, 0.0, 0.0))
        self.assertEqual(econ.crop_profit_eur_ha("lentil", market), 0.0)
        self.assertFalse(e.complete)

    def test_every_strategy_crop_has_a_cost_estimate(self):
        for crop_id in CROP_BY_ID:
            self.assertIn(crop_id, econ.SEED_COSTS_EUR_HA, crop_id)
            self.assertIn(crop_id, econ.OPERATING_COSTS_EUR_HA, crop_id)

    def test_cover_crops_cost_without_market(self):
        e = econ.strategy_economics([planting("phacelia", role="cover")], 1, Market("BG", None, {}))
        self.assertEqual((e.plantings[0].source, e.revenue, e.complete), ("no harvest", 0.0, True))

    def test_ranking_profit_matches_shown_profit(self):
        for crop_id in ("wheat", "sunflower", "oat"):
            shown = econ.strategy_economics([planting(crop_id)], 1, MARKET).profit
            self.assertAlmostEqual(econ.crop_profit_eur_ha(crop_id, MARKET), shown)


app = FastAPI()
app.include_router(router.router, prefix="/api")
client = TestClient(app)

BODY = {
    "crops": ["wheat", "sunflower"],
    "bounds": {"topLeft": {"lat": 43.5, "lng": 27.1}, "bottomRight": {"lat": 43.4, "lng": 27.3}},
}


@patch.object(router, "site_inputs", lambda cells, rect: SiteInputs({}, None, None, []))
class StrategiesMoneyTests(unittest.TestCase):
    def test_economics_use_market_and_ranking_follows_real_profit(self):
        market = Market("BG", 2025, {
            c: priced(900.0 if c == "sunflower" else 150.0, 3.0) for c in [*CROP_BY_ID, "dry_pea"]
        })
        seen = []

        def fake(country, crop_ids):
            seen.append(country)
            return market

        with patch.object(router, "market_reference", fake):
            res = client.post("/api/strategies", json={**BODY, "crops": ["wheat", "sunflower", "barley"]})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(seen, ["BG"])
        plans = res.json()["strategies"]
        profit_first = next(p for p in plans if p["strategyType"] == "profit_first")
        money = profit_first["economics"]
        self.assertEqual((money["country"], money["priceYear"], money["complete"]), ("BG", 2025, True))
        for row in money["plantings"]:
            self.assertEqual(row["revenue"], round(row["yieldTHa"] * row["priceEurT"]))
        self.assertTrue(all("mock" not in r["source"] for p in plans for r in p["economics"]["plantings"]))
        self.assertEqual(
            profit_first["evaluation"]["profitEurHa"],
            round(sum(econ.crop_profit_eur_ha(c, market) for c in profit_first["rotation"]), 2),
        )

    def test_market_failure_still_answers_with_flagged_economics(self):
        def down(country, crop_ids):
            raise RuntimeError("database and sources down")

        with patch.object(router, "market_reference", down):
            res = client.post("/api/strategies", json=BODY)
        self.assertEqual(res.status_code, 200, res.text)
        money = res.json()["strategies"][0]["economics"]
        self.assertFalse(money["complete"])
        self.assertIsNone(money["priceYear"])
        self.assertTrue(all(r["source"] == econ.NO_MARKET_DATA for r in money["plantings"]))
        self.assertEqual(money["perHa"]["profit"], 0)


if __name__ == "__main__":
    unittest.main()
