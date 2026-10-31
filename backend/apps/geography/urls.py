from django.urls import path

from . import indicator_views, stats_views, views, water_views

urlpatterns = [
    path("indicators/", indicator_views.indicators, name="indicators"),
    path("stats/", stats_views.stats, name="stats"),
    path("data-quality/", stats_views.data_quality, name="data-quality"),
    path("water/points/", water_views.points, name="water-points"),
    path("water/summary/", water_views.summary, name="water-summary"),
    path("<str:level>/", views.boundaries, name="boundaries"),
]
# In config/urls.py:  path("api/v1/boundaries/", include("apps.geography.urls")),
