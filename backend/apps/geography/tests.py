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
