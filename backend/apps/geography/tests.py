from io import StringIO

from django.core.management import call_command
from django.test import TestCase


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
