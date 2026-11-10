"""URLs for the standalone messaging service: provider webhooks, a health check, and the admin."""
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/messaging/", include("apps.messaging.urls")),
]
