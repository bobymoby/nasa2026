import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import strategies
from app.services.strategies import STRATEGIES

app = FastAPI()
app.include_router(strategies.router, prefix="/api")
client = TestClient(app)

BOUNDS = {
    "topLeft": {"lat": 43.5, "lng": 27.1},
    "bottomRight": {"lat": 43.4, "lng": 27.3},
}


def month_index(at):
    year, month = at
    return (year - 1) * 12 + month


class StrategiesEndpointTests(unittest.TestCase):
    def test_ranks_strategies_by_requested_crops(self):
        res = client.post("/api/strategies", json={"crops": ["maize"], "bounds": BOUNDS})

        self.assertEqual(res.status_code, 200)
        body = res.json()["strategies"]
        self.assertEqual(len(body), len(STRATEGIES))
        self.assertEqual(body[0]["matchedCrops"], ["maize"])
        self.assertTrue(all(s["durationYears"] >= 2 for s in body))

    def test_needs_exactly_one_area(self):
        fields = [{"id": "a", "polygon": [{"lat": 1, "lng": 1}] * 3}]
        self.assertEqual(client.post("/api/strategies", json={"crops": ["maize"]}).status_code, 422)
        both = {"crops": ["maize"], "bounds": BOUNDS, "fields": fields}
        self.assertEqual(client.post("/api/strategies", json=both).status_code, 422)


class StrategyDataTests(unittest.TestCase):
    def test_water_by_year_covers_every_year(self):
        for s in STRATEGIES:
            by_year = s.water_by_year()
            self.assertEqual(len(by_year), s.duration_years, s.id)
            total = sum(p.water_use_mm for p in s.plantings)
            self.assertLessEqual(abs(sum(by_year) - total), len(by_year), s.id)

    def test_plantings_do_not_overlap(self):
        # the frontend draws each plan as a single lane
        for s in STRATEGIES:
            for a, b in zip(s.plantings, s.plantings[1:]):
                self.assertLess(month_index(a.end), month_index(b.start), s.id)


if __name__ == "__main__":
    unittest.main()
