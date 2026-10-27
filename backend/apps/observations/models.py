from django.db import models


class ObservationSeries(models.Model):
    cell_id = models.CharField(max_length=64)
    metric_name = models.CharField(max_length=128)
    value = models.FloatField()
    observed_at = models.DateTimeField()

    class Meta:
        app_label = "observations"
