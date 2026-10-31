from django.db import models


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
    """A mapped water source for the whole region. ward_code is only a tag used to tailor
    summaries for ward officers; it never limits where a point is stored or shown.
    Reported and verified status are separate on purpose: a source is not 'operational'
    until someone has checked it."""
    source = models.CharField(max_length=40)              # osm, icpac-act, wpdx, county, ...
    source_id = models.CharField(max_length=80)
    name = models.CharField(max_length=160, blank=True)
    kind = models.CharField(max_length=20, default="other")   # borehole, well, spring, surface water, waterhole, tap, other
    lat = models.FloatField()
    lon = models.FloatField()
    ward_code = models.CharField(max_length=32, null=True, blank=True)
    reported_status = models.CharField(max_length=20, default="unknown")       # unknown, functional, non_functional
    verified_status = models.CharField(max_length=24, default="unverified")    # unverified, verified_functional, verified_nonfunctional
    verified_at = models.DateField(null=True, blank=True)
    access_restriction = models.CharField(max_length=12, default="public")     # public, restricted
    raw = models.JSONField(default=dict, blank=True)
    season = models.JSONField(null=True, blank=True)        # 12 values: % of years with water near the point, Jan..Dec (JRC)
    water_ever = models.BooleanField(null=True, blank=True)  # JRC detected surface water near the point at least once
    imported_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["source", "source_id"], name="uniq_waterpoint_source_id")]
        indexes = [models.Index(fields=["ward_code", "kind"])]


class WardStat(models.Model):
    """Static (non-time-series) ward statistics, e.g. surface-water areas and population."""
    ward_code = models.CharField(max_length=32)
    stat = models.CharField(max_length=40)
    value = models.FloatField(null=True, blank=True)
    source = models.CharField(max_length=40)
    reference_year = models.IntegerField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["ward_code", "stat"], name="uniq_ward_stat")]


class IngestRun(models.Model):
    """One row per data-pull command, so any indicator can be traced to the run that produced it."""
    command = models.CharField(max_length=40)
    params = models.JSONField(default=dict, blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=12, default="running")      # running, ok, failed
    rows_written = models.IntegerField(default=0)
    notes = models.TextField(blank=True)
