import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from shapely.geometry import box

from app.routers import soil as soil_router
from app.services import soilgrids


class SoilSelectionTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(soil_router.router, prefix="/api")
        self.client = TestClient(app)

    def test_selected_field_sampling_is_capped_and_spread_across_fields(self):
        polygons = [
            box(index, 43, index + 0.01, 43.01)
            for index in range(10)
        ]

        points = soilgrids._field_sample_points(polygons)

        self.assertEqual(len(points), soilgrids.MAX_RECTANGLE_SAMPLES)
        self.assertEqual(points[0][1], 0.005)
        self.assertAlmostEqual(points[-1][1], 9.005)
        self.assertEqual(points, sorted(points, key=lambda point: point[1]))

    def test_selection_returns_combined_properties_and_source_metadata(self):
        layers = [
            {
                "name": name,
                "unit_measure": {"d_factor": 1},
                "depths": [
                    {"label": depth, "values": {"mean": 12}}
                    for depth in soilgrids.DEPTH_INTERVALS
                ],
            }
            for name in soilgrids.SELECTION_PROPERTIES
        ]
        polygon = box(27.0, 43.0, 27.01, 43.01)
        with patch.object(soilgrids, "_cached_properties", return_value=layers) as fetch:
            result = soilgrids.get_soil_selection(polygons=[polygon])

        self.assertEqual(result["source"], "ISRIC SoilGrids")
        self.assertEqual(result["sampling"]["method"], "field_representative_points")
        self.assertEqual(result["sampling"]["sample_count"], 1)
        self.assertEqual(result["sampling"]["selected_field_count"], 1)
        self.assertEqual(
            result["properties"]["organic_carbon"]["values_by_depth"]["0-5cm"],
            12,
        )
        self.assertEqual(fetch.call_count, 1)

    @patch.object(soil_router, "get_soil_selection", return_value={"source": "ISRIC SoilGrids"})
    def test_rectangle_selection_uses_camel_case_bounds(self, fetch):
        response = self.client.post(
            "/api/soil/selection",
            json={
                "bounds": {
                    "topLeft": {"lat": 43.62, "lng": 27.92},
                    "bottomRight": {"lat": 43.61, "lng": 27.93},
                }
            },
        )

        self.assertEqual(response.status_code, 200)
        rect = fetch.call_args.kwargs["rect"]
        self.assertEqual((rect.north, rect.west, rect.south, rect.east), (43.62, 27.92, 43.61, 27.93))

    @patch.object(soil_router, "get_soil_selection", return_value={"source": "ISRIC SoilGrids"})
    def test_field_selection_passes_polygons_to_soilgrids(self, fetch):
        response = self.client.post(
            "/api/soil/selection",
            json={
                "fields": [
                    {
                        "id": "field-1",
                        "polygon": [
                            {"lat": 43.61, "lng": 27.93},
                            {"lat": 43.61, "lng": 27.94},
                            {"lat": 43.60, "lng": 27.94},
                        ],
                    }
                ]
            },
        )

        self.assertEqual(response.status_code, 200)
        polygon = fetch.call_args.kwargs["polygons"][0]
        self.assertTrue(polygon.is_valid)
        self.assertAlmostEqual(polygon.area, 0.00005)


if __name__ == "__main__":
    unittest.main()
