from django.contrib.gis.db import models


class GridCell(models.Model):
    cell_id = models.CharField(max_length=64, unique=True)
    geom = models.PolygonField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "geography"


class GrazingArea(models.Model):
    name = models.CharField(max_length=150)
    geom = models.PolygonField()

    class Meta:
        app_label = "geography"



class WardIndicator(models.Model):
    """One value per ward, indicator and period. Raw (ndvi, rain_mm) and derived
    (vci, vci3, ndvi_anom, rain_pon3, rain_spi3) rows share this table.
    NULL value means unavailable (never zero)."""
    ward_code = models.CharField(max_length=32)
    indicator = models.CharField(max_length=24)
    period_start = models.DateField()
    period_end = models.DateField(null=True, blank=True)
    value = models.FloatField(null=True, blank=True)
    valid_fraction = models.FloatField(null=True, blank=True)
    source = models.CharField(max_length=40)
    methodology_version = models.CharField(max_length=12)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=["ward_code", "indicator", "period_start"], name="uniq_ward_indicator_period")]
        indexes = [models.Index(fields=["indicator", "period_start"])]