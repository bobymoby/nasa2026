import unittest
from dataclasses import replace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import strategies
from app.services import agronomy, drought
from app.services.prices.pipeline import CropMarket, Market
from app.services.strategies import (
    CROP_BY_ID,
    STRATEGY_PROFILES,
    _add_cover_crops,
    _extra_crops,
    _schedule,
    _similarity,
    crop_risk,
    evaluate_baseline,
    generate_valid_rotations,
    profiles,
    rank_rotation_strategies,
)
from app.services.strategy_inputs import SiteInputs

app = FastAPI()
app.include_router(strategies.router, prefix="/api")
client = TestClient(app)

FAKE_MARKET = Market("BG", 2025, {
    crop_id: CropMarket(250.0, (2020, 2024), "eurostat", "high", 4.0, (2021, 2025), "eurostat")
    for crop_id in [*CROP_BY_ID, "dry_pea"]
})
# monthly reference evapotranspiration in mm, roughly Dobrudzha's
FAKE_ET0 = dict(zip(range(1, 13), [15, 25, 50, 80, 115, 140, 160, 140, 90, 50, 25, 15]))
FAKE_SITE = SiteInputs(
    {crop_id: 70.0 for crop_id in CROP_BY_ID} | {"carrot": 10.0},
    agronomy.Frost((4, 10), (10, 25)),
    FAKE_ET0,
    [],
)
_patches = [
    patch.object(strategies, "market_reference", lambda country, crop_ids: FAKE_MARKET),
    patch.object(strategies, "site_inputs", lambda cells, rect: FAKE_SITE),
]


def setUpModule():
    for p in _patches:
        p.start()


def tearDownModule():
    for p in _patches:
        p.stop()

BOUNDS = {
    "topLeft": {"lat": 43.5, "lng": 27.1},
    "bottomRight": {"lat": 43.4, "lng": 27.3},
}


def post(selected: list[str], **extra) -> list[dict]:
    response = client.post("/api/strategies", json={"bounds": BOUNDS, "crops": selected, **extra})
    assert response.status_code == 200, response.text
    return response.json()["strategies"]


class StrategiesEndpointTests(unittest.TestCase):
    def test_five_selected_crops_fill_the_rotation_with_distinct_orders(self):
        selected = ["wheat", "barley", "sunflower", "chickpea", "field_pea"]
        response = client.post(
            "/api/strategies",
            json={
                "crops": selected,
                "bounds": BOUNDS,
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        plans = body["strategies"]
        self.assertEqual(body["dataMode"], "sourced_with_estimated_costs")
        self.assertGreater(body["rotationsEvaluated"], 0)
        self.assertEqual(len(plans), 5)
        self.assertEqual(len({tuple(plan["rotation"]) for plan in plans}), 5)
        self.assertEqual([plan["ranking"] for plan in plans], [1, 2, 3, 4, 5])
        self.assertEqual(
            {plan["strategyType"] for plan in plans},
            {"profit_first", "soil_builder", "risk_balanced", "water_efficient", "balanced"},
        )
        for plan in plans:
            self.assertEqual(set(plan["rotation"]), set(selected))
            self.assertEqual(plan["addedCrops"], [])
            self.assertTrue(plan["evaluation"]["feasible"])
            self.assertIn("profit_difference_eur_ha", plan["comparison"])
            self.assertIn("water_demand_difference", plan["comparison"])
            self.assertEqual(
                set(plan["evaluation"]["scoringWeights"]),
                {"profit", "soilBenefit", "riskControl", "waterEfficiency", "feasibility"},
            )
            self.assertTrue(plan["explanations"])

    def test_needs_exactly_one_area(self):
        fields = [{"id": "a", "polygon": [{"lat": 1, "lng": 1}] * 3}]
        self.assertEqual(client.post("/api/strategies", json={}).status_code, 422)
        both = {"bounds": BOUNDS, "fields": fields}
        self.assertEqual(client.post("/api/strategies", json=both).status_code, 422)
        no_crops = {"bounds": BOUNDS}
        self.assertEqual(client.post("/api/strategies", json=no_crops).status_code, 422)

    def test_every_strategy_adds_its_own_crops_to_the_selection(self):
        selected = ["wheat", "sunflower"]
        plans = post(selected)
        self.assertEqual(len(plans), 5)
        for plan in plans:
            self.assertTrue(set(selected).issubset(plan["rotation"]))
            self.assertEqual(len(plan["rotation"]), 3)
            self.assertTrue(plan["addedCrops"])
            self.assertEqual(
                plan["addedCrops"],
                [c for c in dict.fromkeys(plan["rotation"]) if c not in selected],
            )
            self.assertTrue(any(e.startswith("Adds ") for e in plan["explanations"]))
        self.assertGreater(len({frozenset(plan["addedCrops"]) for plan in plans}), 2)

    def test_added_crops_respect_exclusions_and_suitability(self):
        plans = post(["wheat", "sunflower"], excludedCrops=["chickpea", "field_pea"])
        for plan in plans:
            self.assertFalse({"chickpea", "field_pea", "carrot"} & set(plan["rotation"]))

    def test_site_data_drives_calendar_water_and_suitability(self):
        plan = post(["wheat", "sunflower"])[0]
        self.assertNotEqual(plan["waterUse"]["level"], "unknown")
        self.assertTrue(all(p["waterUseMm"] > 0 for p in plan["plantings"]))
        self.assertTrue(all(score == 70.0 for score in plan["evaluation"]["suitabilityScores"]))
        self.assertTrue(any("ERA5" in note for note in plan["marketConsiderations"]))

    def test_missing_climate_leaves_water_unknown_and_warns(self):
        site = SiteInputs({}, None, None, ["ERA5 evapotranspiration is unavailable; crop water demand is not estimated."])
        with patch.object(strategies, "site_inputs", lambda cells, rect: site):
            response = client.post("/api/strategies", json={"bounds": BOUNDS, "crops": ["wheat"]})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        for plan in body["strategies"]:
            self.assertEqual(plan["waterUse"]["level"], "unknown")
            self.assertEqual(plan["waterUse"]["byYearMm"], [])
            self.assertIsNone(plan["evaluation"]["waterDemandMm"])
            self.assertTrue(any(n.startswith("Data warning") for n in plan["marketConsiderations"]))
        self.assertTrue(any("ERA5" in a for a in body["assumptions"]))

    def test_unsuitable_selected_crop_is_allowed_with_irrigation(self):
        response = client.post("/api/strategies", json={"bounds": BOUNDS, "crops": ["carrot"]})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(len(post(["carrot"], irrigationAvailable=True)), 5)

    def test_cover_crops_follow_the_strategy_goal(self):
        plans = {plan["strategyType"]: plan for plan in post(["wheat", "sunflower"])}
        covers = {
            name: [p for p in plan["plantings"] if p["role"] == "cover"]
            for name, plan in plans.items()
        }
        self.assertEqual(covers["profit_first"], [])
        self.assertEqual(covers["water_efficient"], [])
        with_covers = [name for name, found in covers.items() if found]
        self.assertTrue(with_covers)
        self.assertTrue(set(with_covers) <= {"soil_builder", "risk_balanced", "balanced"})
        for name in with_covers:
            self.assertTrue(all(p["cropId"] not in plans[name]["rotation"] for p in covers[name]))
            cover_names = {p["cropName"] for p in covers[name]}
            self.assertTrue(any(c in e for e in plans[name]["explanations"] for c in cover_names))
            self.assertEqual(
                {p["source"] for p in plans[name]["economics"]["plantings"] if p["role"] == "cover"},
                {"no harvest"},
            )
            self.assertIn(
                "Eco-scheme 03: soil potential (green manure)",
                {line["name"] for line in plans[name]["economics"]["capPayments"]},
            )
        self.assertIn("No cover crops", " ".join(plans["profit_first"]["explanations"]))

    def test_supports_oat_flax_and_buckwheat_from_crop_suitability(self):
        selected = ["oat", "flax", "buckwheat"]
        plans = post(selected)
        self.assertEqual(len(plans), 5)
        self.assertTrue(all(set(selected).issubset(plan["rotation"]) for plan in plans))

    def test_supports_rye_and_sorghum_from_crop_suitability(self):
        selected = ["rye", "sorghum"]
        plans = post(selected)
        self.assertEqual(len(plans), 5)
        self.assertTrue(all(set(selected).issubset(plan["rotation"]) for plan in plans))

    def test_reed_canary_grass_is_no_longer_offered(self):
        response = client.post("/api/strategies", json={"bounds": BOUNDS, "crops": ["reed_canary_grass"]})
        self.assertEqual(response.status_code, 422)

    def test_rejects_bad_suitability_and_conflicting_preferences(self):
        body = {
            "bounds": BOUNDS,
            "crops": ["wheat"],
            "cropSuitability": {"wheat": 110},
        }
        self.assertEqual(client.post("/api/strategies", json=body).status_code, 422)
        conflict = {
            "bounds": BOUNDS,
            "crops": ["wheat"],
            "preferredCrops": ["wheat"],
            "excludedCrops": ["wheat"],
        }
        self.assertEqual(client.post("/api/strategies", json=conflict).status_code, 422)

    def test_planned_rotation_comes_first_and_is_the_baseline(self):
        response = client.post(
            "/api/strategies",
            json={
                "bounds": BOUNDS,
                "crops": ["wheat", "sunflower", "chickpea"],
                "plannedCrops": ["wheat", "sunflower"],
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        planned = body["strategies"][0]
        self.assertEqual(planned["strategyType"], "planned")
        self.assertEqual(planned["name"], "Planned rotation")
        self.assertEqual(planned["rotation"], ["wheat", "sunflower"])
        self.assertEqual(planned["ranking"], 1)
        self.assertEqual(
            body["baselineRotation"], ["wheat", "sunflower", "wheat", "sunflower"]
        )
        self.assertEqual(body["baseline"]["crop_ids"], body["baselineRotation"])
        self.assertEqual(planned["comparison"]["profit_difference_eur_ha"], 0)
        self.assertTrue(any("planned rotation" in a for a in body["assumptions"]))

    def test_explicit_baseline_wins_over_planned_crops(self):
        response = client.post(
            "/api/strategies",
            json={
                "bounds": BOUNDS,
                "crops": ["wheat", "sunflower", "chickpea"],
                "plannedCrops": ["wheat", "sunflower"],
                "baselineRotation": ["barley", "rapeseed", "barley"],
                "years": 3,
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["baselineRotation"][:2], ["barley", "rapeseed"])

    def test_without_planned_crops_the_regional_baseline_is_used(self):
        response = client.post(
            "/api/strategies", json={"bounds": BOUNDS, "crops": ["wheat", "barley"]}
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertNotIn("planned", {s["strategyType"] for s in body["strategies"]})
        self.assertTrue(any("typical regional rotation" in a for a in body["assumptions"]))

    def test_accepts_custom_baseline_and_weight_profile(self):
        response = client.post(
            "/api/strategies",
            json={
                "bounds": BOUNDS,
                "crops": ["wheat", "sunflower", "field_pea", "chickpea", "barley"],
                "baselineRotation": ["sunflower", "wheat", "field_pea"],
                "strategyWeights": {
                    "soil_builder": {"soilBenefit": 0.8, "profit": 0.2},
                },
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(
            body["baselineRotation"],
            ["sunflower", "wheat", "field_pea", "sunflower", "wheat"],
        )
        soil_builder = next(
            strategy for strategy in body["strategies"]
            if strategy["strategyType"] == "soil_builder"
        )
        self.assertEqual(
            soil_builder["evaluation"]["scoringWeights"]["soilBenefit"],
            0.8,
        )


class RotationGenerationTests(unittest.TestCase):
    def test_enumerates_only_nonexcluded_and_suitable_crops(self):
        territory = {
            "excluded_crops": ["sunflower"],
            "crop_suitability": {"maize": 10.0},
        }
        rotations = generate_valid_rotations(territory, list(CROP_BY_ID), years=3)

        self.assertTrue(rotations)
        for rotation in rotations:
            self.assertEqual(len(rotation["crop_ids"]), 3)
            self.assertNotIn("sunflower", rotation["crop_ids"])
            self.assertNotIn("maize", rotation["crop_ids"])
        irrigated = generate_valid_rotations({**territory, "irrigation_available": True}, ["maize"], years=1)
        self.assertEqual([r["crop_ids"] for r in irrigated], [["maize"]])

    def test_previous_crop_and_family_repeats_are_penalized(self):
        rotations = generate_valid_rotations(
            {"previously_planted": ["wheat"]},
            list(CROP_BY_ID),
            years=3,
        )

        self.assertTrue(rotations)
        self.assertTrue(any(rotation["crop_ids"][0] == "wheat" for rotation in rotations))
        self.assertTrue(any(rotation["rotation_constraint_penalty"] > 0 for rotation in rotations))
        self.assertTrue(any(rotation["rotation_constraint_penalty"] == 0 for rotation in rotations))

    def test_season_schedule_never_overlaps_and_has_bounded_idle_gaps(self):
        rotations = generate_valid_rotations(
            {},
            list(CROP_BY_ID),
            years=3,
        )

        self.assertTrue(rotations)
        for rotation in rotations:
            schedule = rotation["schedule"]
            for previous, following in zip(schedule, schedule[1:]):
                self.assertGreater(following["sow_index"], previous["harvest_index"])
                self.assertLessEqual(
                    following["sow_index"] - previous["harvest_index"] - 1,
                    10,
                )

    def test_rankings_return_distinct_options_with_weighted_metrics(self):
        territory = {
            "preferred_crops": ["field_pea"],
            "baseline_rotation": ["wheat", "sunflower", "barley"],
            "crop_profit_eur_ha": {crop_id: 300.0 + i for i, crop_id in enumerate(CROP_BY_ID)},
        }
        rotations = generate_valid_rotations(territory, list(CROP_BY_ID), years=3)
        ranked = rank_rotation_strategies(rotations, territory)

        self.assertEqual(len(ranked), 5)
        self.assertGreater(len({tuple(strategy["rotation"]) for strategy in ranked}), 1)
        self.assertTrue(all(strategy["evaluation"]["profit_eur_ha"] for strategy in ranked))
        self.assertTrue(all(strategy["comparison"]["profit_difference_eur_ha"] is not None for strategy in ranked))

    def test_cold_climate_frost_dates_still_give_strategies(self):
        cold = SiteInputs(FAKE_SITE.suitability, agronomy.Frost((4, 25), (10, 10)), FAKE_ET0, [])
        with patch.object(strategies, "site_inputs", lambda cells, rect: cold):
            response = client.post("/api/strategies", json={"bounds": BOUNDS, "crops": ["wheat", "sunflower"]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()["strategies"]), 5)

    def test_baseline_with_a_long_fallow_does_not_fail_the_request(self):
        territory = {"baseline_rotation": ["wheat", "sunflower"]}
        sunflower_in_july = replace(profiles(territory)["sunflower"], sow_month=7, harvest_month=10)
        territory["_profiles"]["sunflower"] = sunflower_in_july
        self.assertIsNone(_schedule((profiles(territory)["wheat"], sunflower_in_july)))
        self.assertEqual(evaluate_baseline(territory)["crop_ids"], ["wheat", "sunflower"])

    def test_single_selected_crop_gives_five_different_rotations(self):
        plans = post(["wheat"])
        self.assertEqual(len(plans), 5)
        self.assertTrue(all("wheat" in plan["rotation"] for plan in plans))
        self.assertEqual(len({tuple(plan["rotation"]) for plan in plans}), 5)

    def test_cover_crops_sit_in_idle_windows_without_overlapping(self):
        territory: dict = {}
        rotations = generate_valid_rotations(territory, ["wheat", "sunflower", "chickpea"], years=3)
        for rotation in rotations:
            schedule = _add_cover_crops(rotation["schedule"], "soil_builder", territory)
            for previous, following in zip(schedule, schedule[1:]):
                self.assertGreater(following["sow_index"], previous["harvest_index"])
        self.assertTrue(any(
            len(_add_cover_crops(r["schedule"], "soil_builder", territory)) > len(r["schedule"])
            for r in rotations
        ))
        self.assertTrue(all(
            _add_cover_crops(r["schedule"], "profit_first", territory) == r["schedule"] for r in rotations
        ))


class SourcedProfileTests(unittest.TestCase):
    def test_profiles_come_from_the_agronomy_table(self):
        table = agronomy.crops()
        pea = CROP_BY_ID["field_pea"]
        self.assertEqual(pea.name, table["dry_pea"].name)
        self.assertEqual(pea.family, "Fabaceae")
        self.assertIn("nitrogen_fixing", pea.traits)
        self.assertEqual(pea.humus_ceq, table["dry_pea"].humus_ceq)
        self.assertIsNone(pea.water_demand_mm)
        self.assertNotIn("reed_canary_grass", CROP_BY_ID)

    def test_site_data_fills_in_water_suitability_risk_and_frost(self):
        territory = {
            "et0_monthly_mm": FAKE_ET0,
            "crop_suitability": {"wheat": 81.0},
            "crop_risk": {"wheat": 30.0},
            "frost": agronomy.Frost((5, 1), (10, 1)),
        }
        wheat = profiles(territory)["wheat"]
        self.assertEqual(wheat.suitability, 81.0)
        self.assertEqual(wheat.risk, 30.0)
        self.assertEqual(
            wheat.water_demand_mm,
            round(drought.seasonal_etc_mm(agronomy.water_crop("wheat", agronomy.Calendar(territory["frost"])), FAKE_ET0)),
        )
        self.assertLess(wheat.sow_month, CROP_BY_ID["wheat"].sow_month)
        self.assertTrue(any(c.role == "cover" for c in profiles(territory).values()))

    def test_crop_risk_combines_yield_and_price_variability(self):
        self.assertIsNone(crop_risk(None, None))
        self.assertEqual(crop_risk(0.1, None), 20.0)
        self.assertEqual(crop_risk(0.3, 0.4), 100.0)
        self.assertLess(crop_risk(0.1, 0.1), crop_risk(0.2, 0.1))


class ExtraCropTests(unittest.TestCase):
    def test_shortlist_follows_the_leading_goal(self):
        selected = ["wheat", "sunflower"]
        soil = _extra_crops({}, selected, STRATEGY_PROFILES["soil_builder"])
        self.assertTrue(soil)
        self.assertTrue(all(c.id not in selected for c in soil))
        self.assertIn("nitrogen_fixing", soil[0].traits)
        priced = {"crop_profit_eur_ha": {"rapeseed": 900.0, "barley": 700.0, "maize": 650.0}}
        profit = _extra_crops(priced, selected, STRATEGY_PROFILES["profit_first"])
        self.assertEqual(profit[0].id, "rapeseed")
        self.assertNotEqual([c.id for c in soil], [c.id for c in profit])

    def test_similarity_is_one_for_identical_and_zero_for_disjoint_rotations(self):
        self.assertEqual(_similarity(["wheat", "barley"], ["wheat", "barley"]), 1.0)
        self.assertEqual(_similarity(["wheat", "barley"], ["oat", "flax"]), 0.0)
        self.assertLess(
            _similarity(["wheat", "barley"], ["barley", "wheat"]),
            _similarity(["wheat", "barley"], ["wheat", "barley"]),
        )


if __name__ == "__main__":
    unittest.main()
