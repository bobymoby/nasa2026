import unittest
from unittest.mock import patch

from app.geo import Rect
from app.services.soilgrids import get_soil_chemistry, get_soil_physical


def layer(name, factor, values):
    return {
        "name": name,
        "unit_measure": {"d_factor": factor},
        "depths": [
            {"label": depth, "values": {"mean": value}}
            for depth, value in values.items()
        ],
    }


class SoilGridsEndpointTests(unittest.TestCase):
    @patch("app.services.soilgrids._cached_properties")
    def test_chemistry_returns_supported_properties_with_scale_applied(self, fetch):
        fetch.return_value = [
            layer("phh2o", 10, {"0-5cm": 69}),
            layer("nitrogen", 100, {"0-5cm": 587}),
            layer("soc", 10, {"0-5cm": 522}),
        ]

        result = get_soil_chemistry(Rect(43.6, 27.8, 43.599, 27.801))

        self.assertEqual(result["properties"]["pH_water"]["values_by_depth"]["0-5cm"], 6.9)
        self.assertEqual(result["properties"]["nitrogen"]["values_by_depth"]["0-5cm"], 5.87)
        self.assertEqual(result["properties"]["organic_carbon"]["values_by_depth"]["0-5cm"], 52.2)
        self.assertEqual(
            set(result["properties"]),
            {"pH_water", "nitrogen", "organic_carbon"},
        )
        self.assertEqual(result["sampling"]["sample_count"], 1)

    @patch("app.services.soilgrids._cached_properties")
    def test_physical_returns_supported_properties_and_depth_profile(self, fetch):
        fetch.return_value = [
            layer("clay", 10, {"0-5cm": 339}),
            layer("sand", 10, {"0-5cm": 26}),
            layer("silt", 10, {"0-5cm": 635}),
            layer("bdod", 100, {"0-5cm": 125}),
        ]

        result = get_soil_physical(Rect(43.6, 27.8, 43.599, 27.801))

        props = result["properties"]
        self.assertEqual(props["clay"]["values_by_depth"]["0-5cm"], 33.9)
        self.assertEqual(props["bulk_density"]["values_by_depth"]["0-5cm"], 1.25)
        self.assertEqual(result["depth_intervals_cm"][0], "0-5cm")
        self.assertEqual(set(props), {"clay", "sand", "silt", "bulk_density"})

    @patch("app.services.soilgrids._cached_properties")
    def test_chemistry_averages_grid_samples(self, fetch):
        calls = []

        def sample(lat, lng, _):
            calls.append((lat, lng))
            value = len(calls) * 10
            return [
                layer("phh2o", 10, {"0-5cm": value}),
                layer("nitrogen", 100, {"0-5cm": value}),
                layer("soc", 10, {"0-5cm": value}),
            ]

        fetch.side_effect = sample
        rect = Rect(43.61, 27.8, 43.6, 27.81)

        result = get_soil_chemistry(rect)

        self.assertGreater(len(calls), 1)
        self.assertLessEqual(len(calls), 5)
        self.assertEqual(result["sampling"]["sample_count"], len(calls))
        self.assertEqual(
            result["properties"]["pH_water"]["values_by_depth"]["0-5cm"],
            sum(range(1, len(calls) + 1)) / len(calls),
        )
        self.assertTrue(all(rect.south <= lat <= rect.north for lat, _ in calls))
        self.assertTrue(all(rect.west <= lng <= rect.east for _, lng in calls))
        self.assertEqual(
            result["properties"]["pH_water"]["sample_counts_by_depth"]["0-5cm"],
            len(calls),
        )


if __name__ == "__main__":
    unittest.main()
