import unittest
from unittest.mock import patch

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

        result = get_soil_chemistry(43.6, 27.8)

        self.assertEqual(result["properties"]["pH_water"]["values_by_depth"]["0-5cm"], 6.9)
        self.assertEqual(result["properties"]["nitrogen"]["values_by_depth"]["0-5cm"], 5.87)
        self.assertEqual(result["properties"]["organic_carbon"]["values_by_depth"]["0-5cm"], 52.2)
        self.assertEqual(
            set(result["properties"]),
            {"pH_water", "nitrogen", "organic_carbon"},
        )

    @patch("app.services.soilgrids._cached_properties")
    def test_physical_returns_supported_properties_and_depth_profile(self, fetch):
        fetch.return_value = [
            layer("clay", 10, {"0-5cm": 339}),
            layer("sand", 10, {"0-5cm": 26}),
            layer("silt", 10, {"0-5cm": 635}),
            layer("bdod", 100, {"0-5cm": 125}),
        ]

        result = get_soil_physical(43.6, 27.8)

        props = result["properties"]
        self.assertEqual(props["clay"]["values_by_depth"]["0-5cm"], 33.9)
        self.assertEqual(props["bulk_density"]["values_by_depth"]["0-5cm"], 1.25)
        self.assertEqual(result["depth_intervals_cm"][0], "0-5cm")
        self.assertEqual(set(props), {"clay", "sand", "silt", "bulk_density"})


if __name__ == "__main__":
    unittest.main()
