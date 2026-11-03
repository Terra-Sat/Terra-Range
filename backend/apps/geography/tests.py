import json
from io import StringIO
from unittest.mock import MagicMock, patch

from datetime import date

from django.core.management import call_command
from django.test import RequestFactory, SimpleTestCase, TestCase

from . import indicator_views, water_views
from .views import trend_report_poc


class AggregationRegressionTests(SimpleTestCase):
    @patch("apps.geography.indicator_views._ward_hierarchy")
    def test_county_level_rollup_uses_county_codes(self, hierarchy):
        hierarchy.return_value = {
            "w1": {"constituency": "c1", "county": "county_a", "area_km2": 5.0},
            "w2": {"constituency": "c2", "county": "county_b", "area_km2": 7.0},
            "w3": {"constituency": "c1", "county": "county_a", "area_km2": 3.0},
        }
        mock_qs = MagicMock()
        mock_qs.values.return_value = [
            {"ward_code": "w1", "period_start": "2024-01-01", "value": 10, "valid_fraction": 1.0, "source": "derived", "methodology_version": "v1"},
            {"ward_code": "w2", "period_start": "2024-01-01", "value": 20, "valid_fraction": 1.0, "source": "derived", "methodology_version": "v1"},
            {"ward_code": "w3", "period_start": "2024-01-01", "value": 30, "valid_fraction": 1.0, "source": "derived", "methodology_version": "v1"},
        ]
        with patch("apps.geography.indicator_views.WardIndicator.objects.filter", return_value=mock_qs):
            resp = indicator_views.indicators(type("Req", (), {"GET": {"indicator": "vci3", "level": "counties"}})())
        body = resp.json()
        codes = {row["code"] for row in body["rows"]}
        self.assertEqual(codes, {"county_a", "county_b"})


class DashboardViewTests(TestCase):
    def test_dashboard_root_serves_frontend_template(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Marsabit boundaries")


class WaterPointViewTests(TestCase):
    def test_water_points_api_returns_feature_collection(self):
        response = self.client.get("/api/v1/water/points/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["type"], "FeatureCollection")
        self.assertIn("features", response.json())

    def test_import_water_command_is_registered(self):
        out = StringIO()
        call_command("import_water", "--help", stdout=out)

        self.assertIn("Import water points", out.getvalue())


class TrendReportContractTests(SimpleTestCase):
    def test_trend_report_accepts_model_selector_and_prompt(self):
        req = RequestFactory().get(
            "/api/v1/boundaries/trend-report/?indicator=ndvi&ward_code=demo-ward&start_date=2023-01-01&end_date=2023-05-31&months=5&model=openai/gpt-4o-mini&prompt=Summarise%20the%20trend%20and%20forecast"
        )

        mock_qs = MagicMock()
        mock_qs.filter.return_value = mock_qs
        mock_qs.order_by.return_value.values.return_value = [
            {"period_start": date(2023, 1, 1), "value": 10.0},
            {"period_start": date(2023, 2, 1), "value": 12.0},
            {"period_start": date(2023, 3, 1), "value": 14.0},
            {"period_start": date(2023, 4, 1), "value": 16.0},
            {"period_start": date(2023, 5, 1), "value": 18.0},
        ]

        with patch("apps.geography.views.WardIndicator.objects.filter", return_value=mock_qs):
            response = trend_report_poc(req)

        self.assertEqual(response.status_code, 200)
        body = json.loads(response.content)
        self.assertEqual(body["model_used"], "openai/gpt-4o-mini")
        self.assertIn("openai/gpt-4o-mini", body["available_models"])
        self.assertIn("chart", body)
        self.assertIn("community_conflict_outlook", body)
        self.assertIn("management_report", body)

    def test_pasture_health_indicator_uses_composite_series(self):
        req = RequestFactory().get(
            "/api/v1/boundaries/trend-report/?indicator=pasture_health&ward_code=ward-1&start_date=2024-01-01&end_date=2024-05-31&months=5"
        )

        mock_qs = MagicMock()
        mock_qs.filter.return_value = mock_qs
        mock_qs.order_by.return_value.values.return_value = [
            {"indicator": "vci", "period_start": date(2024, 1, 1), "value": 45.0},
            {"indicator": "ndvi", "period_start": date(2024, 1, 1), "value": 0.42},
            {"indicator": "rain_pon3", "period_start": date(2024, 1, 1), "value": 90.0},
            {"indicator": "vci", "period_start": date(2024, 2, 1), "value": 50.0},
            {"indicator": "ndvi", "period_start": date(2024, 2, 1), "value": 0.48},
            {"indicator": "rain_pon3", "period_start": date(2024, 2, 1), "value": 110.0},
            {"indicator": "vci", "period_start": date(2024, 3, 1), "value": 58.0},
            {"indicator": "ndvi", "period_start": date(2024, 3, 1), "value": 0.52},
            {"indicator": "rain_pon3", "period_start": date(2024, 3, 1), "value": 120.0},
            {"indicator": "vci", "period_start": date(2024, 4, 1), "value": 62.0},
            {"indicator": "ndvi", "period_start": date(2024, 4, 1), "value": 0.6},
            {"indicator": "rain_pon3", "period_start": date(2024, 4, 1), "value": 130.0},
            {"indicator": "vci", "period_start": date(2024, 5, 1), "value": 68.0},
            {"indicator": "ndvi", "period_start": date(2024, 5, 1), "value": 0.65},
            {"indicator": "rain_pon3", "period_start": date(2024, 5, 1), "value": 140.0},
        ]
        with patch("apps.geography.views.WardIndicator.objects.filter", return_value=mock_qs), \
             patch("apps.geography.views._generate_trend_report", return_value="Pasture health management brief"):
            response = trend_report_poc(req)

        self.assertEqual(response.status_code, 200)
        body = json.loads(response.content)
        self.assertEqual(body["indicator"], "pasture_health")
        self.assertEqual(body["management_report"], "Pasture health management brief")
        self.assertGreater(len(body["series"]), 0)
        self.assertLess(body["series"][0]["value"], 100)
