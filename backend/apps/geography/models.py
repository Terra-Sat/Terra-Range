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

class WaterPoint(models.Model):
    """A mapped water point, usually tied to a ward for local access analysis."""
    ACCESS_PUBLIC = "public"
    ACCESS_RESTRICTED = "restricted"
    ACCESS_CHOICES = [(ACCESS_PUBLIC, "Public"), (ACCESS_RESTRICTED, "Restricted")]

    source = models.CharField(max_length=80)
    source_id = models.CharField(max_length=80)
    name = models.CharField(max_length=160, blank=True, default="")
    kind = models.CharField(max_length=40, default="other")
    lat = models.FloatField()
    lon = models.FloatField()
    ward_code = models.CharField(max_length=32, null=True, blank=True)
    access_restriction = models.CharField(max_length=20, choices=ACCESS_CHOICES, default=ACCESS_PUBLIC)
    reported_status = models.CharField(max_length=32, default="unknown")
    verified_status = models.CharField(max_length=32, default="unknown")
    verified_at = models.DateTimeField(null=True, blank=True)
    raw = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "geography"
        constraints = [
            models.UniqueConstraint(fields=["source", "source_id"], name="uniq_waterpoint_source_source_id")
        ]
        indexes = [
            models.Index(fields=["ward_code"]),
            models.Index(fields=["source"]),
            models.Index(fields=["kind"]),
            models.Index(fields=["verified_status"]),
        ]

    def __str__(self):
        return self.name or f"{self.source}:{self.source_id}"