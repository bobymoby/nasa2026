import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import strategies
from app.services.strategies import (
    CROP_BY_ID,
    MOCK_CROPS,
    generate_valid_rotations,
    rank_rotation_strategies,
)

app = FastAPI()
app.include_router(strategies.router, prefix="/api")
client = TestClient(app)

BOUNDS = {
    "topLeft": {"lat": 43.5, "lng": 27.1},
    "bottomRight": {"lat": 43.4, "lng": 27.3},
}


class StrategiesEndpointTests(unittest.TestCase):
    def test_first_four_use_all_selected_crops_and_balanced_may_add_one(self):
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
        self.assertEqual(body["dataMode"], "illustrative_mock")
        self.assertGreater(body["rotationsEvaluated"], 0)
        self.assertEqual(len(plans), 5)
        self.assertEqual(len({tuple(plan["rotation"]) for plan in plans}), 5)
        self.assertEqual([plan["ranking"] for plan in plans], [1, 2, 3, 4, 5])
        self.assertEqual(
            {plan["strategyType"] for plan in plans},
            {"profit_first", "soil_builder", "risk_balanced", "water_efficient", "balanced"},
        )
        for plan in plans[:4]:
            self.assertEqual(len(plan["rotation"]), len(selected))
            self.assertEqual(set(plan["rotation"]), set(selected))
            self.assertTrue(plan["evaluation"]["feasible"])
            self.assertIn("profit_difference_eur_ha", plan["comparison"])
            self.assertIn("water_demand_difference", plan["comparison"])
            self.assertEqual(
                set(plan["evaluation"]["scoringWeights"]),
                {"profit", "soilBenefit", "riskControl", "waterEfficiency", "feasibility"},
            )
            self.assertTrue(plan["explanations"])
        self.assertTrue(set(selected).issubset(plans[4]["rotation"]))
        self.assertGreater(len(plans[4]["rotation"]), len(selected))

    def test_needs_exactly_one_area(self):
        fields = [{"id": "a", "polygon": [{"lat": 1, "lng": 1}] * 3}]
        self.assertEqual(client.post("/api/strategies", json={}).status_code, 422)
        both = {"bounds": BOUNDS, "fields": fields}
        self.assertEqual(client.post("/api/strategies", json=both).status_code, 422)
        no_crops = {"bounds": BOUNDS}
        self.assertEqual(client.post("/api/strategies", json=no_crops).status_code, 422)

    def test_only_balanced_strategy_can_add_crops_outside_user_selection(self):
        selected = ["wheat", "barley", "sunflower", "chickpea", "field_pea"]
        response = client.post(
            "/api/strategies",
            json={
                "bounds": BOUNDS,
                "crops": selected,
                "availableCrops": [*selected, "maize", "soybean"],
                "irrigationAvailable": True,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        plans = response.json()["strategies"]
        self.assertTrue(all(set(plan["rotation"]) == set(selected) for plan in plans[:4]))
        self.assertTrue(set(selected).issubset(plans[4]["rotation"]))
        self.assertGreater(len(plans[4]["rotation"]), len(selected))

    def test_supports_oat_flax_and_buckwheat_from_crop_suitability(self):
        selected = ["oat", "flax", "buckwheat"]
        response = client.post(
            "/api/strategies",
            json={"bounds": BOUNDS, "crops": selected},
        )

        self.assertEqual(response.status_code, 200, response.text)
        plans = response.json()["strategies"]
        self.assertEqual(len(plans), 5)
        self.assertTrue(all(set(plan["rotation"]) == set(selected) for plan in plans[:4]))
        self.assertTrue(set(selected).issubset(plans[4]["rotation"]))

    def test_supports_rye_and_sorghum_from_crop_suitability(self):
        selected = ["rye", "sorghum"]
        response = client.post(
            "/api/strategies",
            json={"bounds": BOUNDS, "crops": selected},
        )

        self.assertEqual(response.status_code, 200, response.text)
        plans = response.json()["strategies"]
        self.assertEqual(len(plans), 5)
        self.assertTrue(
            all(set(plan["rotation"]) == set(selected) for plan in plans[:4])
        )
        self.assertTrue(set(selected).issubset(plans[4]["rotation"]))

    def test_supports_carrot_and_reed_canary_grass(self):
        selected = ["carrot", "reed_canary_grass"]
        response = client.post(
            "/api/strategies",
            json={"bounds": BOUNDS, "crops": selected},
        )

        self.assertEqual(response.status_code, 200, response.text)
        plans = response.json()["strategies"]
        self.assertEqual(len(plans), 5)
        self.assertTrue(
            all(set(plan["rotation"]) == set(selected) for plan in plans[:4])
        )
        self.assertTrue(set(selected).issubset(plans[4]["rotation"]))

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
    def test_enumerates_only_nonexcluded_and_irrigation_compatible_crops(self):
        territory = {
            "excluded_crops": ["sunflower"],
            "irrigation_available": False,
        }
        rotations = generate_valid_rotations(territory, list(CROP_BY_ID), years=3)

        self.assertTrue(rotations)
        for rotation in rotations:
            self.assertEqual(len(rotation["crop_ids"]), 3)
            self.assertNotIn("sunflower", rotation["crop_ids"])
            self.assertNotIn("maize", rotation["crop_ids"])
            self.assertNotIn("soybean", rotation["crop_ids"])

    def test_previous_crop_and_family_repeats_are_penalized(self):
        rotations = generate_valid_rotations(
            {"previously_planted": ["wheat"], "irrigation_available": True},
            list(MOCK_CROPS),
            years=3,
        )

        self.assertTrue(rotations)
        self.assertTrue(any(rotation["crop_ids"][0] == "wheat" for rotation in rotations))
        self.assertTrue(any(rotation["rotation_constraint_penalty"] > 0 for rotation in rotations))
        self.assertTrue(any(rotation["rotation_constraint_penalty"] == 0 for rotation in rotations))

    def test_season_schedule_never_overlaps_and_has_bounded_idle_gaps(self):
        rotations = generate_valid_rotations(
            {"irrigation_available": True},
            list(MOCK_CROPS),
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
            "irrigation_available": False,
            "baseline_rotation": ["wheat", "sunflower", "barley"],
        }
        rotations = generate_valid_rotations(territory, list(MOCK_CROPS), years=3)
        ranked = rank_rotation_strategies(rotations, territory)

        self.assertEqual(len(ranked), 5)
        self.assertGreater(len({tuple(strategy["rotation"]) for strategy in ranked}), 1)
        self.assertTrue(all(strategy["evaluation"]["profit_eur_ha"] for strategy in ranked))
        self.assertTrue(all(strategy["comparison"]["profit_difference_eur_ha"] is not None for strategy in ranked))

    def test_two_selected_crops_still_produce_five_ranked_strategy_types(self):
        response = client.post(
            "/api/strategies",
            json={
                "bounds": BOUNDS,
                "crops": ["wheat", "sunflower"],
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        plans = response.json()["strategies"]
        self.assertEqual(len(plans), 5)
        selected = {"wheat", "sunflower"}
        self.assertTrue(all(set(plan["rotation"]) == selected for plan in plans[:4]))
        self.assertTrue(selected.issubset(plans[4]["rotation"]))

    def test_single_selected_crop_returns_ranked_profiles_with_reuse_warning(self):
        response = client.post(
            "/api/strategies",
            json={"bounds": BOUNDS, "crops": ["wheat"]},
        )

        self.assertEqual(response.status_code, 200, response.text)
        plans = response.json()["strategies"]
        self.assertEqual(len(plans), 5)
        self.assertTrue(all(set(plan["rotation"]) == {"wheat"} for plan in plans[:4]))
        self.assertIn("wheat", plans[4]["rotation"])
        self.assertTrue(
            any("no distinct sequence" in " ".join(plan["explanations"]) for plan in plans)
        )


if __name__ == "__main__":
    unittest.main()
