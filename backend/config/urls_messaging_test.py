from django.urls import include, path

urlpatterns = [path("api/v1/messaging/", include("apps.messaging.urls"))]
