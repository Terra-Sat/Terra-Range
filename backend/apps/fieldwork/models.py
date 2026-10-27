from django.db import models


class Recipient(models.Model):
    name = models.CharField(max_length=150)
    phone = models.CharField(max_length=32, blank=True)
    consent_given = models.BooleanField(default=False)
    language = models.CharField(max_length=32, default="en")

    class Meta:
        app_label = "fieldwork"


class AdvisoryChannel(models.Model):
    name = models.CharField(max_length=64, unique=True)
    channel_type = models.CharField(max_length=32, default="sms")

    class Meta:
        app_label = "fieldwork"
