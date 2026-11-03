from django.urls import path

from apps.messaging import webhooks

app_name = "messaging"

urlpatterns = [
    path("webhooks/africastalking/<str:token>/inbound/", webhooks.africastalking_inbound, name="at-inbound"),
    path("webhooks/africastalking/<str:token>/delivery/", webhooks.africastalking_delivery, name="at-delivery"),
    path("webhooks/whatsapp/<str:token>/", webhooks.meta_whatsapp_webhook, name="whatsapp-webhook"),
]
