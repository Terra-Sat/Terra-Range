from django.db import models


class RestrictedEvent(models.Model):
    event_type = models.CharField(max_length=128)
    severity = models.CharField(max_length=32, default="medium")
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "restricted"
        permissions = (("view_restricted", "Can view restricted data"),)
