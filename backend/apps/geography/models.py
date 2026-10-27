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
