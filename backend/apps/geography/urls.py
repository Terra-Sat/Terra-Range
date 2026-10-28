from django.urls import path

from backend.apps.geography import indicator_views

from . import views

urlpatterns = [
    path("<str:level>/", views.boundaries, name="boundaries"),
    path("indicators/", indicator_views.indicators, name="indicators"),
]
