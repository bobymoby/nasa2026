import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import prices as prices_router
from app.services.prices import pipeline
from app.services.prices.pipeline import CropCodes, Country, CountryPrices, PriceRow
from app.services.prices.sources import (
    EurostatPrice,
    EurostatYield,
    FaoPrice,
    FaoYield,
    PriceSourceError,
    jsonstat_cells,
    parse_eurostat_prices,
    parse_eurostat_yields,
    parse_fao_bulk,
    parse_fao_yields,
)

WHEAT = CropCodes("wheat", "Wheat (soft)", "01110000", 15, "C1110")
BEET = CropCodes("sugarbeet", "Sugar beet", "02400000", 157, "R2000")


def jsonstat(prod_labels, values, years=("2023", "2024")):
    return {
        "id": ["freq", "prod_veg", "time"],
        "size": [1, len(prod_labels), len(years)],
        "dimension": {
            "freq": {"category": {"index": {"A": 0}}},
            "prod_veg": {"category": {"index": {c: i for i, c in enumerate(prod_labels)}, "label": prod_labels}},
            "time": {"category": {"index": {y: i for i, y in enumerate(years)}}},
        },
        "value": values,
        "status": {"3": "p"},
    }


def row(year, eur, source="eurostat", confidence="high", crop="wheat"):
    return PriceRow("BG", crop, year, eur, eur, source, None, confidence)


class SourceParsingTests(unittest.TestCase):
    def test_jsonstat_cells_decode_positions(self):
        data = jsonstat({"A1": "a", "B2": "b"}, {"1": 10.0, "2": 20.0, "3": None})
        cells = list(jsonstat_cells(data))
        self.assertEqual(cells, [
            ({"freq": "A", "prod_veg": "A1", "time": "2024"}, 10.0, None),
            ({"freq": "A", "prod_veg": "B2", "time": "2023"}, 20.0, None),
        ])

    def test_eurostat_units_become_per_tonne(self):
        labels = {
            "01110000": "Soft wheat - prices per 100 kg",
            "02400000": "Sugar beet: unit value - prices per 1000 kg",
            "04210000": "Roses - prices per 100 items",
        }
        data = jsonstat(labels, {"1": 18.51, "3": 40.0, "5": 30.0})
        got = {(p.prod_veg, p.year): (p.eur_per_t, p.flag) for p in parse_eurostat_prices(data)}
        self.assertEqual(got, {("01110000", 2024): (185.1, None), ("02400000", 2024): (40.0, "p")})

    def test_fao_bulk_keeps_annual_usd_rows_for_mapped_items(self):
        header = '"Area Code","Area Code (M49)","Area","Item Code","Item Code (CPC)","Item","Element Code","Element","Year Code","Year","Months Code","Months","Unit","Value","Flag"'
        lines = [
            header,
            '"27","\'100","Bulgaria","15","\'0111","Wheat","5532","USD","2024","2024","7021","Annual value","USD","200.3","A"',
            '"27","\'100","Bulgaria","15","\'0111","Wheat","5530","LCU","2024","2024","7021","Annual value","LCU","390","A"',
            '"27","\'100","Bulgaria","15","\'0111","Wheat","5532","USD","2024","2024","7001","January","USD","210","A"',
            '"27","\'100","Bulgaria","221","\'01371","Almonds","5532","USD","2024","2024","7021","Annual value","USD","5000","A"',
            '"5100","\'002","Africa","15","\'0111","Wheat","5532","USD","2024","2024","7021","Annual value","USD","","E"',
        ]
        self.assertEqual(parse_fao_bulk(lines, {15}), [FaoPrice(100, 15, 2024, 200.3, "A")])


class MergeTests(unittest.TestCase):
    def test_eurostat_wins_and_faostat_fills_gaps(self):
        eurostat = [EurostatPrice("01110000", 2023, 200.0, None)]
        fao = [FaoPrice(100, 15, 2022, 220.0, "A"), FaoPrice(100, 15, 2023, 999.0, "A"), FaoPrice(100, 15, 2021, 110.0, "E")]
        base, rows = pipeline.merge_rows(
            "BG", [WHEAT], eurostat, fao,
            usd_per_eur={2021: 1.1, 2022: 1.1, 2023: 1.0},
            index={2021: 50.0, 2022: 80.0, 2023: 100.0},
        )
        self.assertEqual(base, 2023)
        got = {r.year: (r.eur_per_t, r.nominal_eur_per_t, r.source, r.confidence) for r in rows}
        self.assertEqual(got, {
            2021: (200.0, 100.0, "faostat", "low"),
            2022: (250.0, 200.0, "faostat", "medium"),
            2023: (200.0, 200.0, "eurostat", "high"),
        })

    def test_years_without_fx_or_index_are_dropped(self):
        fao = [FaoPrice(100, 157, 2010, 30.0, "A"), FaoPrice(100, 157, 2011, 30.0, "A")]
        _, rows = pipeline.merge_rows("BG", [BEET], [], fao, usd_per_eur={2011: 1.5}, index={2010: 90.0, 2012: 100.0})
        self.assertEqual(rows, [])

    def test_greece_and_uk_use_eurostat_codes(self):
        self.assertEqual(pipeline.resolve_country("gr").eurostat_geo, "EL")
        self.assertEqual(pipeline.resolve_country("GB").eurostat_geo, "UK")
        self.assertEqual(pipeline.resolve_country("BG").m49, 100)
        with self.assertRaises(ValueError):
            pipeline.resolve_country("XX")


class SummaryTests(unittest.TestCase):
    def test_reference_is_mean_of_latest_years(self):
        rows = [row(y, p) for y, p in [(2018, 100), (2019, 100), (2020, 200), (2021, 300), (2022, 400)]]
        s = pipeline.summarize(rows, reference_years=3, override=None)
        self.assertEqual(s["reference_price"], 300.0)
        self.assertEqual(s["reference_years"], [2020, 2022])
        self.assertEqual(s["price_source"], "market")

    def test_override_replaces_reference_but_keeps_market(self):
        s = pipeline.summarize([row(2023, 200), row(2024, 220)], reference_years=5, override=180.0)
        self.assertEqual((s["reference_price"], s["price_source"], s["market_reference_price"]), (180.0, "farmer", 210.0))

    def test_confidence_is_the_weakest_reference_year(self):
        rows = [row(2023, 200), row(2024, 220, "faostat", "low")]
        self.assertEqual(pipeline.summarize(rows, 5, None)["confidence"], "low")

    def test_variability_needs_three_years(self):
        self.assertIsNone(pipeline.summarize([row(2023, 100), row(2024, 300)], 5, None)["variability"])
        stable = pipeline.summarize([row(y, 100 + (y % 2)) for y in range(2015, 2025)], 5, None)["variability"]
        self.assertEqual((stable["level"], stable["years"]), ("low", [2015, 2024]))
        swings = pipeline.summarize([row(2022, 100), row(2023, 300), row(2024, 100)], 5, None)["variability"]
        self.assertEqual(swings["level"], "high")


FAO_QCL_HEADER = (
    "Area Code,Area Code (M49),Area,Item Code,Item Code (CPC),Item,Element Code,Element,"
    "Year Code,Year,Unit,Value,Flag,Note"
)


class YieldTests(unittest.TestCase):
    def test_eurostat_yields_keep_crop_year_and_flag(self):
        data = {
            "id": ["freq", "crops", "strucpro", "geo", "time"],
            "size": [1, 2, 1, 1, 2],
            "dimension": {
                "freq": {"category": {"index": {"A": 0}}},
                "crops": {"category": {"index": {"C1110": 0, "C1300": 1}}},
                "strucpro": {"category": {"index": {"YLD_HUMD_EU_T_HA": 0}}},
                "geo": {"category": {"index": {"BG": 0}}},
                "time": {"category": {"index": {"2024": 0, "2025": 1}}},
            },
            "value": {"0": 5.96, "1": 5.97, "3": 5.59},
            "status": {"1": "p"},
        }
        self.assertEqual(parse_eurostat_yields(data), [
            EurostatYield("C1110", 2024, 5.96, None),
            EurostatYield("C1110", 2025, 5.97, "p"),
            EurostatYield("C1300", 2025, 5.59, None),
        ])

    def test_fao_yields_convert_units_and_skip_other_rows(self):
        lines = [
            FAO_QCL_HEADER,
            "27,'100,Bulgaria,15,'0111,Wheat,5412,Yield,2023,2023,kg/ha,5590.0,A,",
            "27,'100,Bulgaria,15,'0111,Wheat,5412,Yield,1999,1999,kg/ha,3000.0,A,",
            "27,'100,Bulgaria,15,'0111,Wheat,5510,Production,2023,2023,t,6776540,A,",
            "27,'100,Bulgaria,15,'0111,Wheat,5412,Yield,2022,2022,100 g/ha,54800,E,",
            "27,'100,Bulgaria,388,'01234,Tomatoes,5412,Yield,2023,2023,kg/ha,36367.9,A,",
            "5000,'001,World,15,'0111,Wheat,5412,Yield,2023,2023,kg/ha,0,A,",
        ]
        self.assertEqual(parse_fao_yields(lines, {15}), [
            FaoYield(100, 15, 2023, 5.59, "A"),
            FaoYield(100, 15, 2022, 5.48, "E"),
        ])

    def test_eurostat_wins_and_faostat_fills_yield_gaps(self):
        merged = pipeline.merge_yields(
            [WHEAT, BEET],
            [EurostatYield("C1110", 2023, 5.59, None), EurostatYield("C1110", 2024, 5.96, None)],
            [FaoYield(100, 15, 2022, 5.48, "A"), FaoYield(100, 15, 2023, 5.2, "A"), FaoYield(100, 157, 2023, 50.0, "A")],
        )
        self.assertEqual(merged["wheat"], [(2022, 5.48, "faostat"), (2023, 5.59, "eurostat"), (2024, 5.96, "eurostat")])
        self.assertEqual(merged["sugarbeet"], [(2023, 50.0, "faostat")])

    def test_reference_yield_is_mean_of_latest_years(self):
        rows = [(y, float(y - 2015), "eurostat") for y in range(2016, 2026)]
        ref = pipeline.reference_yield(rows, 5)
        self.assertEqual((ref.t_ha, ref.years, ref.source), (8.0, (2021, 2025), "eurostat"))
        mixed = pipeline.reference_yield([(2024, 4.0, "faostat"), (2025, 6.0, "eurostat")], 5)
        self.assertEqual((mixed.t_ha, mixed.source), (5.0, "eurostat + faostat"))
        self.assertIsNone(pipeline.reference_yield([], 5))


app = FastAPI()
app.include_router(prices_router.router, prefix="/api")
client = TestClient(app)


def fake_country_prices(code, crop_ids):
    rows = [row(y, 200.0 + y - 2020) for y in range(2020, 2025) if crop_ids is None or "wheat" in crop_ids]
    return CountryPrices(Country("BG", "Bulgaria", "BG", 100), 2025, "BG", rows)


class MarketReferenceTests(unittest.TestCase):
    @patch.object(pipeline, "country_prices", fake_country_prices)
    @patch.object(pipeline, "country_yields", lambda code, ids: {"wheat": pipeline.YieldRef(5.9, (2021, 2025), "eurostat")})
    def test_pairs_price_and_yield_per_crop(self):
        market = pipeline.market_reference("bg", ["wheat", "sugarbeet"])
        self.assertEqual((market.country, market.price_year), ("BG", 2025))
        self.assertEqual(market.crops["wheat"], pipeline.CropMarket(
            202.0, (2020, 2024), "eurostat", "high", 5.9, (2021, 2025), "eurostat",
        ))
        self.assertNotIn("sugarbeet", market.crops)

    def test_failed_source_leaves_its_half_out(self):
        def no_prices(code, ids):
            raise PriceSourceError("down")

        yields = {"wheat": pipeline.YieldRef(5.9, (2021, 2025), "eurostat")}
        with patch.object(pipeline, "country_prices", no_prices), \
                patch.object(pipeline, "country_yields", lambda code, ids: yields):
            market = pipeline.market_reference("BG", ["wheat"])
        self.assertIsNone(market.price_year)
        self.assertIsNone(market.crops["wheat"].price_eur_t)
        self.assertEqual(market.crops["wheat"].yield_t_ha, 5.9)


@patch.object(pipeline, "crop_codes", lambda: {"wheat": WHEAT, "sugarbeet": BEET})
@patch.object(pipeline, "country_prices", fake_country_prices)
class PricesEndpointTests(unittest.TestCase):
    def test_reference_override_and_unknown_crops(self):
        res = client.post("/api/prices", json={
            "country": "BG", "crops": ["wheat", "sugarbeet", "mango"], "overrides": {"sugarbeet": 45},
        })
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["unknownCrops"], ["mango"])
        crops = {c["cropId"]: c for c in body["crops"]}
        self.assertEqual(crops["wheat"]["referencePrice"], 202.0)
        self.assertEqual(len(crops["wheat"]["history"]), 5)
        self.assertEqual((crops["sugarbeet"]["referencePrice"], crops["sugarbeet"]["priceSource"]), (45.0, "farmer"))
        self.assertIsNone(crops["sugarbeet"]["marketReferencePrice"])

    def test_rejects_bad_input(self):
        self.assertEqual(client.post("/api/prices", json={"country": "BGR"}).status_code, 422)
        self.assertEqual(client.post("/api/prices", json={"country": "BG", "overrides": {"wheat": 0}}).status_code, 422)

    def test_table_is_flat(self):
        res = client.get("/api/prices/BG/table")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()[0], {
            "country": "BG", "crop": "wheat", "year": 2020, "eurPerT": 200.0, "source": "eurostat", "confidence": "high",
        })


if __name__ == "__main__":
    unittest.main()
