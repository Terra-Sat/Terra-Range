from django.urls import path

from . import indicator_views, pixel_views as v, stats_views, views, water_views

urlpatterns = [
    path("indicators/", indicator_views.indicators, name="indicators"),
    path("stats/", stats_views.stats, name="stats"),
    path("data-quality/", stats_views.data_quality, name="data-quality"),
    path("water/points/", water_views.points, name="water-points"),
    path("water/summary/", water_views.summary, name="water-summary"),
    # Pixel raster layer routes must come BEFORE the <str:level>/ catch-all
    path("pixels/", v.pixel_layers, name="geo-pixel-layers"),
    path("pixels/<int:pk>/image.png", v.pixel_image, name="geo-pixel-image"),
    path("pixels/<int:pk>/value/", v.pixel_value, name="geo-pixel-value"),
    path("pixels/<int:pk>/shares/", v.pixel_shares, name="geo-pixel-shares"),
    path("outlook/", v.outlook, name="geo-outlook"),
    # Catch-all MUST stay last — it matches any single path segment as a boundary level
    path("<str:level>/", views.boundaries, name="boundaries"),
]
# In config/urls.py:  path("api/v1/boundaries/", include("apps.geography.urls")),
