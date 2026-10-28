from django.urls import path

from . import views

urlpatterns = [
    path("<str:level>/", views.boundaries, name="boundaries"),
]
