import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import strategies
from app.services.prices.pipeline import CropMarket, Market
from app.services.strategies import (
    CROP_BY_ID,
    MOCK_CROPS,
    generate_valid_rotations,
    rank_rotation_strategies,
)

app = FastAPI()
app.include_router(strategies.router, prefix="/api")
client = TestClient(app)

FAKE_MARKET = Market("BG", 2025, {
    crop_id: CropMarket(250.0, (2020, 2024), "eurostat", "high", 4.0, (2021, 2025), "eurostat")
    for crop_id in [*CROP_BY_ID, "dry_pea"]
})
_market_patch = patch.object(strategies, "market_reference", lambda country, crop_ids: FAKE_MARKET)


def setUpModule():
    _market_patch.start()


def tearDownModule():
    _market_patch.stop()

BOUNDS = {
    "topLeft": {"lat": 43.5, "lng": 27.1},
    "bottomRight": {"lat": 43.4, "lng": 27.3},
}


class StrategiesEndpointTests(unittest.TestCase):
    def test_service_context_changes_scores_and_economics(self):
        context = {
            "errors": [],
            "plots": [
                {
                    "crops": {
                        "catalog_crop_name": {
                            "code": "whe",
                            "suitability": 93.0,
                            "yield_kg_ha": 6000,
                        }
                    },
                    "site": {
                        "soc_g_kg": 12.5,
                        "sand_pct": 40.0,
                        "clay_pct": 25.0,
                        "awc_mm_per_m": 155.0,
                    },
                    "previous_season": {"type": "winter_crop"},
                }
            ],
            "climate": {
                "months": [
                    {"month": month, "precip_mm": 50.0, "et0_mm": 40.0}
                    for month in range(1, 13)
                ]
            },
            "prices": {
                "price_year": 2025,
                "crops": {
                    "wheat": {
                        "reference_eur_t": 250.0,
                        "confidence": "high",
                        "variability_cv": 0.1,
                    }
                },
            },
        }
        response = client.post(
            "/api/strategies",
            json={
                "bounds": BOUNDS,
                "crops": ["wheat"],
                "siteContext": context,
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["dataMode"], "mixed_service_and_mock")
        strategy = body["strategies"][0]
        self.assertEqual(strategy["evaluation"]["suitabilityScores"], [93.0] * 3)
        self.assertAlmostEqual(strategy["evaluation"]["soilBenefitScore"], 35.29)
        self.assertEqual(strategy["erosionRisk"]["level"], "unknown")
        self.assertEqual(strategy["economics"]["priceYear"], 2025)
        self.assertEqual(strategy["ratings"]["market"]["score"], 5)
        self.assertIn("3 of 3 crop plantings", strategy["ratings"]["market"]["summary"])
        self.assertTrue(
            all(
                "price eurostat 2020–2024" in planting["source"]
                and "yield eurostat 2021–2025" in planting["source"]
                for planting in strategy["economics"]["plantings"]
            )
        )
        notes = " ".join(strategy["marketConsiderations"])
        self.assertIn("ISRIC SoilGrids field context", notes)
        self.assertIn("annual-normal precipitation 600 mm", notes)
        self.assertIn("FAO GAEZ v4", notes)
        self.assertIn("crop_agronomy.csv", notes)
        self.assertIn("Seasonal crop water demand", notes)

    def test_reference_price_coverage_is_not_reported_as_market_access(self):
        response = client.post(
            "/api/strategies",
            json={
                "bounds": BOUNDS,
                "crops": ["wheat"],
                "siteContext": {
                    "prices": {
                        "price_year": 2025,
                        "crops": {
                            "wheat": {
                                "reference_eur_t": 250.0,
                                "confidence": "high",
                            }
                        },
                    }
                },
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        strategy = response.json()["strategies"][0]
        self.assertIn(
            "does not measure buyer access",
            strategy["ratings"]["market"]["summary"],
        )
        self.assertTrue(
            all(
                "yield eurostat 2021–2025" in planting["source"]
                for planting in strategy["economics"]["plantings"]
            )
        )

    def test_reference_market_yield_is_used_with_gaez_context(self):
        response = client.post(
            "/api/strategies",
            json={
                "bounds": BOUNDS,
                "crops": ["wheat"],
                "siteContext": {
                    "plots": [
                        {
                            "crops": {
                                "wheat": {
                                    "code": "whe",
                                    "suitability": 90.0,
                                    "yield_kg_ha": 7000,
                                }
                            }
                        }
                    ]
                },
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        economics = response.json()["strategies"][0]["economics"]
        self.assertEqual(economics["priceYear"], 2025)
        self.assertTrue(
            all(
                "yield eurostat 2021–2025" in planting["source"]
                for planting in economics["plantings"]
            )
        )

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
        self.assertEqual(body["dataMode"], "mixed_service_and_mock")
        self.assertEqual(plans[0]["economics"]["priceYear"], 2025)
        self.assertEqual(plans[0]["ratings"]["market"]["score"], 5)
        self.assertTrue(
            all(
                "price eurostat 2020–2024" in planting["source"]
                and "yield eurostat 2021–2025" in planting["source"]
                for planting in plans[0]["economics"]["plantings"]
                if planting["role"] != "cover"
            )
        )
        self.assertGreater(body["rotationsEvaluated"], 0)
        self.assertEqual(len(plans), 5)
        self.assertEqual(len({tuple(plan["rotation"]) for plan in plans}), 5)
        self.assertEqual([plan["ranking"] for plan in plans], [1, 2, 3, 4, 5])
        self.assertEqual(
            {plan["strategyType"] for plan in plans},
            {"profit_first", "soil_builder", "risk_balanced", "water_efficient", "balanced"},
        )
        expected_years = max(3, len(selected), min(len(selected) + 1, 5))
        self.assertTrue(
            all(len(plan["rotation"]) == expected_years for plan in plans[:4])
        )
        self.assertGreater(
            len({tuple(plan["rotation"]) for plan in plans[:4]}), 1
        )
        for plan in plans[:4]:
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

    def test_current_crops_come_first_in_planting_order(self):
        response = client.post(
            "/api/strategies",
            json={
                "bounds": BOUNDS,
                "crops": ["wheat", "sunflower", "chickpea"],
                "currentCrops": ["wheat", "maize"],
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        plans = response.json()["strategies"]
        self.assertEqual(len(plans), 6)
        current = plans[0]
        self.assertEqual(current["strategyType"], "current")
        self.assertEqual(current["name"], "Your current rotation")
        self.assertEqual(current["rotation"], ["wheat", "maize"])
        self.assertEqual(current["ranking"], 1)
        self.assertEqual([plan["ranking"] for plan in plans], [1, 2, 3, 4, 5, 6])
        self.assertIn("profitPerHaYear", current["economics"])

    def test_current_crops_accept_crops_page_ids_and_reject_unknown(self):
        ok = client.post(
            "/api/strategies",
            json={"bounds": BOUNDS, "crops": ["wheat"], "currentCrops": ["dry_pea", "oats"]},
        )
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertEqual(
            set(ok.json()["strategies"][0]["rotation"]), {"field_pea", "oat"}
        )

        bad = client.post(
            "/api/strategies",
            json={"bounds": BOUNDS, "crops": ["wheat"], "currentCrops": ["banana"]},
        )
        self.assertEqual(bad.status_code, 422)
        self.assertIn("banana", bad.json()["detail"])

    def test_without_current_crops_no_current_strategy(self):
        response = client.post(
            "/api/strategies", json={"bounds": BOUNDS, "crops": ["wheat", "barley"]}
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn(
            "current", {plan["strategyType"] for plan in response.json()["strategies"]}
        )

    def test_objectives_diversify_repeated_selected_crops(self):
        selected = ["flax", "oat", "rye", "sorghum"]
        response = client.post(
            "/api/strategies",
            json={"bounds": BOUNDS, "crops": selected},
        )

        self.assertEqual(response.status_code, 200, response.text)
        plans = response.json()["strategies"]
        focused = plans[:4]
        self.assertTrue(all(set(plan["rotation"]) == set(selected) for plan in focused))
        self.assertTrue(all(len(plan["rotation"]) == 5 for plan in focused))
        self.assertGreater(len({tuple(plan["rotation"]) for plan in focused}), 1)
        self.assertGreater(
            len({plan["evaluation"]["profitEurHa"] for plan in focused}), 1
        )
        self.assertGreater(
            len({plan["evaluation"]["riskScore"] for plan in focused}), 1
        )

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
