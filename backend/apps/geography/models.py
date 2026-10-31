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


"""Models for stored pixel layers, ward class shares and seasonal outlooks.

Import from models.py with:   from .pixel_models import *  # noqa

Design notes
------------
* PixelLayer keeps the *values* (uint8, 1 VCI unit per step), not a coloured
  image. Colours and class breaks are applied at request time, so changing the
  illustrative VCI classes after NDMA validation needs no re-ingest.
* SeasonalOutlook is a separate table from WardIndicator on purpose: its rows
  describe the future and carry an issue date and a lead month, which would
  break the "latest period with data" logic used for observed indicators.
* No PostGIS fields are used, so these tables are SQLite-testable. Boundary
  tables stay untouched (raw SQL only), as the handover requires.
"""


__all__ = ["PixelLayer", "WardClassShare", "SeasonalOutlook"]


class PixelLayer(models.Model):
    indicator = models.CharField(max_length=32, db_index=True)  # e.g. "vci"
    period_start = models.DateField(db_index=True)
    period_end = models.DateField()
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    # EPSG:4326 extent of the raster edges (not pixel centres)
    west = models.FloatField()
    south = models.FloatField()
    east = models.FloatField()
    north = models.FloatField()
    # zlib-compressed uint8, row-major, north-up. 255 = no data (never zero).
    values = models.BinaryField()
    encoding = models.CharField(max_length=32, default="u8-floor-0-100-nd255")
    source = models.CharField(max_length=200)
    methodology_version = models.CharField(max_length=40)
    status = models.CharField(max_length=24, default="Derived")
    params = models.JSONField(default=dict, blank=True)  # baseline, QA rule, etc.
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("indicator", "period_start")]
        ordering = ["-period_start"]

    def __str__(self):
        return f"{self.indicator} {self.period_start} ({self.width}x{self.height})"

    @property
    def bounds(self):
        return (self.west, self.south, self.east, self.north)


class WardClassShare(models.Model):
    """Share of valid pixels per class inside a ward, for one PixelLayer."""
    layer = models.ForeignKey(PixelLayer, on_delete=models.CASCADE, related_name="shares")
    ward_code = models.CharField(max_length=32, db_index=True)
    n_total = models.PositiveIntegerField()   # pixel centres inside the ward
    n_valid = models.PositiveIntegerField()   # ... that have data
    coverage = models.FloatField(null=True)   # percent valid, NULL if n_total == 0
    pct_emergency = models.FloatField(null=True)
    pct_alarm = models.FloatField(null=True)
    pct_alert = models.FloatField(null=True)
    pct_normal = models.FloatField(null=True)
    breaks = models.CharField(max_length=64)  # breaks used, e.g. "20,35,50"

    class Meta:
        unique_together = [("layer", "ward_code")]


class SeasonalOutlook(models.Model):
    """Ward-level value from an EXTERNAL seasonal forecast. Status is Reported.

    lead 1 = the calendar month of issue, 2 = next month, 3 = the one after
    (a convention of this project, see import_outlook). Not a Terra Range
    prediction and not validated for Marsabit.
    """
    issued = models.DateField(db_index=True)
    lead = models.PositiveSmallIntegerField()
    valid_start = models.DateField()
    valid_end = models.DateField()
    ward_code = models.CharField(max_length=32, db_index=True)
    variable = models.CharField(max_length=40)   # e.g. spi_pctile, p_below
    units = models.CharField(max_length=24, blank=True, default="")
    value = models.FloatField(null=True)         # NULL = missing, never zero
    source = models.CharField(max_length=120)    # issuing centre / product
    methodology_version = models.CharField(max_length=40)
    status = models.CharField(max_length=24, default="Reported")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("issued", "lead", "ward_code", "variable", "source")]
        ordering = ["-issued", "lead", "ward_code"]