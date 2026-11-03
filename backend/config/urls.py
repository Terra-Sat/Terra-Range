from django.contrib import admin
from django.urls import include, path
from django.views.generic import TemplateView

from apps.geography import water_views

urlpatterns = [
    path("api/v1/messaging/", include("apps.messaging.urls")),
    path("", TemplateView.as_view(template_name="index.html"), name="dashboard"),
    path("admin/", admin.site.urls),
    path("api/v1/boundaries/", include("apps.geography.urls")),
    path("api/v1/boundaries/water/points/", water_views.points, name="boundary_water_points"),
    path("api/v1/boundaries/water/summary/", water_views.summary, name="boundary_water_summary"),
    path("api/v1/water/points/", water_views.points, name="water_points"),
    path("api/v1/water/summary/", water_views.summary, name="water_summary"),
]
