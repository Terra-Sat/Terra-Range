"""Offline tests: no Earth Engine, no PostGIS. Run:  python manage.py test apps.geography"""
import datetime as dt
import json
from unittest import mock

import numpy as np
from django.core.management import call_command
from django.test import RequestFactory, TestCase

from .. import indicator_logic as L
from .. import indicator_views, stats_views, water_views
from ..management.commands import update_gee as G
from ..models import IngestRun, WardIndicator, WardStat, WaterPoint

META = {"W1": ("C1", 100.0), "W2": ("C1", 300.0), "W3": ("C2", 100.0)}
rf = RequestFactory()


def body(resp):
    return json.loads(resp.content)


def terra_dates(y0, y1, last=None):
    for y in range(y0, y1 + 1):
        for doy in range(1, 366, 16):
            d = dt.date(y, 1, 1) + dt.timedelta(days=doy - 1)
            if d.year == y and (last is None or d <= last):
                yield d


class SaveAndDerive(TestCase):
    def test_save_is_idempotent(self):
        row = dict(ward_code="W1", indicator="ndvi", period_start=dt.date(2026, 1, 1), value=0.3, source="t")
        L.save([row]); L.save([{**row, "value": 0.4}])
        self.assertEqual(WardIndicator.objects.count(), 1)
        self.assertEqual(WardIndicator.objects.get().value, 0.4)

    def test_vci_flags_a_dry_year_and_keeps_missing_as_null(self):
        last, rows = dt.date(2026, 9, 30), []
        for d in terra_dates(2001, 2026, last):
            v = 0.35 + 0.15 * np.sin(2 * np.pi * d.timetuple().tm_yday / 365) + 0.002 * (d.year - 2001)
            if d.year == 2026 and d >= dt.date(2026, 6, 1):
                v = 0.05                                             # far below anything in the baseline
            if d == dt.date(2010, 1, 17):
                v = None
            rows.append(dict(ward_code="W1", indicator="ndvi", period_start=d, value=v, source="t"))
        L.save(rows); L.derive()
        get = lambda ind, d: WardIndicator.objects.get(ward_code="W1", indicator=ind, period_start=d)
        self.assertIsNone(get("vci", dt.date(2010, 1, 17)).value)                    # missing stays missing
        vcis = [r.value for r in WardIndicator.objects.filter(indicator="vci").exclude(value=None)]
        self.assertTrue(all(0 <= v <= 100 for v in vcis), "VCI must stay within 0-100")
        self.assertLess(get("vci3", last).value, 20)                                 # Emergency-level vegetation
        self.assertLess(get("ndvi_anom", last).value, 0)

    def test_spi_is_roughly_standard_normal_over_its_baseline(self):
        rng, rows = np.random.default_rng(3), []
        for y in range(1991, 2027):
            for m in range(1, 13):
                rows.append(dict(ward_code="W1", indicator="rain_mm", period_start=dt.date(y, m, 1),
                                 value=float(rng.gamma(2.0, 40.0)), source="t"))
        L.save(rows); L.derive()
        spi = [r.value for r in WardIndicator.objects.filter(indicator="rain_spi3", period_start__month=3,
                                                             period_start__year__lte=2020).exclude(value=None)]
        self.assertGreaterEqual(len(spi), 25)
        self.assertLess(abs(np.mean(spi)), 0.3); self.assertTrue(0.8 < np.std(spi) < 1.2)


class UpdateGeeCommand(TestCase):
    def setUp(self):
        WaterPoint.objects.create(id=1, source="t", source_id="1", lat=2.3, lon=37.9, ward_code="W1", kind="surface water")

    def patches(self, **extra):
        today = dt.date.today()
        ps = dict(
            init_ee=lambda: object(), ward_geoms=lambda: [("W1", "{}")], ward_fc=lambda ee, g: None,
            ndvi_year=lambda ee, w, y: [{"properties": {"code": "W1", "date": f"{y}-01-01", "ndvi": 0.3, "valid": 0.9}},
                                        {"properties": {"code": "W1", "date": f"{y}-01-09", "ndvi": 0.9, "valid": 0.9}}],  # Aqua-offset date
            rain_year=lambda ee, w, y: [{"properties": {"code": "W1", "date": f"{y}-02-01", "rain_mm": 10.0}}],
            points_fc=lambda ee: ([(1, 2.3, 37.9)], None),
            ward_static=lambda ee, g, p: [("W1", {"ward_area_km2": 10.0, "water_seasonal_km2": 1.0, "pop_2020": 1000.0, "pop_within_5km_water": 400.0})],
            point_season=lambda ee, pts: {1: {**{f"m{m}": 60.0 for m in range(1, 13)}, "max_extent": 1}})
        ps.update(extra)
        return [mock.patch.object(G, k, v) for k, v in ps.items()]

    def run_cmd(self, **kw):
        ctx = self.patches(**kw)
        for c in ctx: c.start()
        self.addCleanup(mock.patch.stopall)
        call_command("update_gee", "--no-derive")

    def test_full_run_stores_everything_and_logs_it(self):
        self.run_cmd()
        run = IngestRun.objects.get()
        self.assertEqual((run.status, run.command), ("ok", "update_gee")); self.assertGreater(run.rows_written, 0)
        self.assertFalse(WardIndicator.objects.filter(indicator="ndvi", period_start__day=9).exists(), "Aqua dates must be dropped")
        self.assertTrue(WardIndicator.objects.filter(indicator="ndvi", period_start=dt.date(2001, 1, 1)).exists())
        self.assertEqual(WardStat.objects.filter(ward_code="W1").count(), 4)
        wp = WaterPoint.objects.get(id=1)
        self.assertEqual((len(wp.season), wp.water_ever), (12, True))

    def test_failure_is_logged_and_raised(self):
        def boom(ee, w, y): raise RuntimeError("boom")
        with self.assertRaises(RuntimeError):
            self.run_cmd(ndvi_year=boom)
        run = IngestRun.objects.get()
        self.assertEqual(run.status, "failed"); self.assertIn("boom", run.notes)


class Api(TestCase):
    def setUp(self):
        for mod in (indicator_views, stats_views, water_views):
            p = mock.patch.object(mod, "_ward_meta", lambda: META); p.start(); self.addCleanup(p.stop)
        d = dt.date(2026, 9, 30)
        L.save([dict(ward_code=c, indicator="vci3", period_start=d, value=v, source="derived") for c, v in
                (("W1", 40.0), ("W2", 20.0), ("W3", None))] +
               [dict(ward_code="W1", indicator="vci3", period_start=dt.date(2026, 10, 16), value=None, source="derived")])
        for c, s, v in (("W1", "ward_area_km2", 100.0), ("W2", "ward_area_km2", 300.0), ("W1", "pop_2020", 1000.0),
                        ("W2", "pop_2020", 3000.0), ("W1", "pop_within_5km_water", 500.0), ("W2", "pop_within_5km_water", 600.0),
                        ("W1", "water_seasonal_km2", 10.0), ("W2", "water_seasonal_km2", 30.0)):
            WardStat.objects.create(ward_code=c, stat=s, value=v, source="t")
        WaterPoint.objects.create(source="t", source_id="a", lat=1, lon=1, ward_code="W1", kind="borehole", season=[80] * 6 + [10] * 6)
        WaterPoint.objects.create(source="t", source_id="b", lat=1, lon=1, ward_code="W2", kind="well", verified_status="verified_functional")

    def test_indicators_default_to_latest_period_with_data_and_roll_up_by_area(self):
        wards = body(indicator_views.indicators(rf.get("/", {"indicator": "vci3"})))["rows"]
        self.assertEqual({r["period_start"] for r in wards}, {"2026-09-30"})
        c = {r["code"]: r for r in body(indicator_views.indicators(rf.get("/", {"indicator": "vci3", "level": "constituencies"})))["rows"]}
        self.assertAlmostEqual(c["C1"]["value"], (40 * 100 + 20 * 300) / 400)          # area-weighted, not a plain mean
        self.assertIsNone(c["C2"]["value"])                                            # no valid ward -> unavailable, not zero

    def test_stats_sum_up_and_give_percentages(self):
        r = body(stats_views.stats(rf.get("/", {"level": "counties"})))["rows"][0]
        self.assertEqual((r["pop_2020"], r["pop_within_5km_water"]), (4000.0, 1100.0))
        self.assertEqual((r["pop_within_5km_pct"], r["water_seasonal_pct"]), (27.5, 10.0))

    def test_water_points_and_summary(self):
        pts = {f["properties"]["kind"]: f["properties"] for f in body(water_views.points(rf.get("/")))["features"]}
        self.assertEqual(pts["borehole"]["months_50"], 6)
        s = {r["code"]: r for r in body(water_views.summary(rf.get("/", {"level": "constituencies"})))["rows"]}
        self.assertEqual((s["C1"]["total"], s["C1"]["verified"], s["C2"]["total"]), (2, 1, 0))

    def test_data_quality_reports_last_period_with_data(self):
        q = {r["indicator"]: r for r in body(stats_views.data_quality(rf.get("/")))["indicators"]}
        self.assertEqual(q["vci3"]["last"], "2026-10-16"); self.assertEqual(q["vci3"]["last_with_data"], "2026-09-30")
