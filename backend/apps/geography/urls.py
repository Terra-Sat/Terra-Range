from django.urls import path

from . import indicator_views, views

urlpatterns = [
    path("indicators/", indicator_views.indicators, name="indicators"),
    path("<str:level>/", views.boundaries, name="boundaries"),
]