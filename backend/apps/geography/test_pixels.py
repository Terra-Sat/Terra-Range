import csv
import datetime as dt
import io
import os
import tempfile
from unittest import mock

import numpy as np
import rasterio
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from PIL import Image
from rasterio.transform import from_bounds

from . import pixel_store as ps
from .models import PixelLayer, SeasonalOutlook, WardClassShare

BOUNDS = (37.0, 2.0, 38.0, 3.0)


def make_tif(path, arr, bounds=BOUNDS, crs="EPSG:4326", nodata=-9999.0):
    h, w = arr.shape
    with rasterio.open(path, "w", driver="GTiff", height=h, width=w, count=1,
                       dtype="float32", crs=crs, nodata=nodata,
                       transform=from_bounds(*bounds, w, h)) as dst:
        dst.write(arr.astype("float32"), 1)


def make_tif_multi(path, arrays, bounds=BOUNDS, nodata=-9999.0):
    h, w = arrays[0].shape
    with rasterio.open(path, "w", driver="GTiff", height=h, width=w, count=len(arrays),
                       dtype="float32", crs="EPSG:4326", nodata=nodata,
                       transform=from_bounds(*bounds, w, h)) as dst:
        for i, a in enumerate(arrays, 1):
            dst.write(a.astype("float32"), i)


def ndvi_grid():
    g = vci_grid() / 100.0          # ward A 0.10, ward B north 0.60, south 0.30, col 0 NaN
    return g


def rect(w, s, e, n):
    return {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}


WARDS = {"WARDAAAAAAA": rect(37.0, 2.0, 37.5, 3.0), "WARDBBBBBBB": rect(37.5, 2.0, 38.0, 3.0)}


def vci_grid():
    a = np.empty((10, 10), dtype="float32")
    a[:, :5] = 10.0            # ward A: Emergency
    a[:, 0] = np.nan           # first column missing
    a[:5, 5:] = 60.0           # ward B north half: Normal
    a[5:, 5:] = 30.0           # ward B south half: Alarm
    return a


class StoreHelpers(TestCase):
    def test_class_edges_are_exact(self):
        v = np.array([19.99, 20.0, 34.99, 35.0, 49.99, 50.0, 100.0, 140.0, -3.0, np.nan])
        cls = ps.classify(ps.quantise(v))
        self.assertEqual(list(cls), [0, 1, 1, 2, 2, 3, 3, 3, 0, 255])

    def test_pack_roundtrip_and_size_check(self):
        u8 = ps.quantise(np.random.default_rng(1).uniform(0, 100, (50, 60)))
        self.assertTrue((ps.unpack(ps.pack(u8), 60, 50) == u8).all())
        with self.assertRaises(ValueError):
            ps.unpack(ps.pack(u8), 61, 50)

    def test_render_nodata_transparent_and_colours(self):
        u8 = ps.quantise(np.array([[10, 40], [np.nan, 90]]))
        im = np.array(Image.open(io.BytesIO(ps.render_png(u8))))
        self.assertEqual(im[1, 0, 3], 0)                       # nodata transparent
        self.assertEqual(tuple(im[0, 0]), ps.DEFAULT_COLORS[0])  # 10 -> Emergency
        self.assertEqual(tuple(im[0, 1]), ps.DEFAULT_COLORS[2])  # 40 -> Alert
        self.assertEqual(tuple(im[1, 1]), ps.DEFAULT_COLORS[3])  # 90 -> Normal

    def test_sample(self):
        u8 = ps.quantise(vci_grid())
        self.assertIsNone(ps.sample(u8, BOUNDS, 37.05, 2.5))   # nodata column
        self.assertEqual(ps.sample(u8, BOUNDS, 37.25, 2.5), 10)
        self.assertEqual(ps.sample(u8, BOUNDS, 37.75, 2.95), 60)
        self.assertIsNone(ps.sample(u8, BOUNDS, 36.0, 2.5))     # outside

    def test_ward_shares_counts(self):
        s = ps.ward_class_shares(ps.quantise(vci_grid()), BOUNDS, WARDS)
        a, b = s["WARDAAAAAAA"], s["WARDBBBBBBB"]
        self.assertEqual((a["n_total"], a["n_valid"], a["coverage"]), (50, 40, 80.0))
        self.assertEqual(a["pct"], [100.0, 0.0, 0.0, 0.0])
        self.assertEqual(b["pct"], [0.0, 50.0, 0.0, 50.0])

    def test_ward_without_pixel_centre_is_null_not_zero(self):
        tiny = {"T": rect(37.01, 2.01, 37.02, 2.02)}   # between pixel centres
        s = ps.ward_class_shares(ps.quantise(vci_grid()), BOUNDS, tiny)
        self.assertEqual(s["T"], dict(n_total=0, n_valid=0, coverage=None, pct=None))

    def test_read_geotiff_rejects_wrong_crs_and_maps_sentinel(self):
        with tempfile.TemporaryDirectory() as d:
            bad = os.path.join(d, "utm.tif")
            make_tif(bad, vci_grid(), bounds=(500000, 200000, 600000, 300000), crs="EPSG:32637")
            with self.assertRaises(ValueError):
                ps.read_geotiff(bad)
            g = vci_grid(); g[9, 9] = -9999.0
            ok = os.path.join(d, "ok.tif"); make_tif(ok, g)
            arr, bounds = ps.read_geotiff(ok)
            self.assertTrue(np.isnan(arr[9, 9]))
            self.assertEqual(bounds, BOUNDS)

    def test_zonal_mean_all_touched(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "c.tif")
            make_tif(p, np.array([[1.0, 3.0], [1.0, 3.0]]))      # 2x2 coarse grid
            z = ps.zonal_mean_all_touched(p, WARDS)
            self.assertEqual(z["WARDAAAAAAA"][0], 1.0)
            self.assertEqual(z["WARDBBBBBBB"][0], 3.0)

    def test_store_refuses_all_nodata(self):
        with self.assertRaises(ValueError):
            ps.store_layer(indicator="vci", period_start=dt.date(2026, 9, 30),
                           period_end=dt.date(2026, 10, 15), bounds=BOUNDS,
                           arr_float=np.full((4, 4), np.nan), source="t",
                           methodology_version="m")
        self.assertEqual(PixelLayer.objects.count(), 0)


class Api(TestCase):
    def setUp(self):
        for start in (dt.date(2026, 9, 14), dt.date(2026, 9, 30)):
            ps.store_layer(indicator="vci", period_start=start,
                           period_end=start + dt.timedelta(days=15), bounds=BOUNDS,
                           arr_float=vci_grid(), source="t", methodology_version="m1",
                           ward_geoms=WARDS)
        self.base = "/api/v1/boundaries/"

    def test_latest_selection_and_no_blob_in_list(self):
        j = self.client.get(self.base + "pixels/?indicator=vci").json()
        self.assertEqual(len(j["layers"]), 2)
        sel = next(l for l in j["layers"] if l["id"] == j["selected"])
        self.assertEqual(sel["period_start"], "2026-09-30")
        self.assertEqual(sel["coordinates"][0], [37.0, 3.0])   # TL
        self.assertEqual(sel["image_url"], f"{self.base}pixels/{sel['id']}/image.png")
        self.assertEqual(j["legend"][0], {"class": "Emergency", "range": "<20"})

    def test_bad_period_400_and_missing_period_null(self):
        self.assertEqual(self.client.get(self.base + "pixels/?period=nope").status_code, 400)
        j = self.client.get(self.base + "pixels/?period=2020-01-01").json()
        self.assertIsNone(j["selected"])

    def test_image_etag_and_catch_all_precedence(self):
        pk = PixelLayer.objects.first().pk
        r = self.client.get(f"{self.base}pixels/{pk}/image.png")
        self.assertEqual((r.status_code, r["Content-Type"]), (200, "image/png"))
        r2 = self.client.get(f"{self.base}pixels/{pk}/image.png", HTTP_IF_NONE_MATCH=r["ETag"])
        self.assertEqual(r2.status_code, 304)
        self.assertEqual(self.client.get(self.base + "wards/").json(), {"catch_all": "wards"})

    def test_value_and_shares(self):
        pk = PixelLayer.objects.first().pk
        j = self.client.get(f"{self.base}pixels/{pk}/value/?lat=2.5&lon=37.25").json()
        self.assertEqual((j["value"], j["class"]), (10, "Emergency"))
        j = self.client.get(f"{self.base}pixels/{pk}/value/?lat=2.5&lon=37.05").json()
        self.assertIsNone(j["value"])
        self.assertEqual(self.client.get(f"{self.base}pixels/{pk}/value/").status_code, 400)
        sh = self.client.get(f"{self.base}pixels/{pk}/shares/").json()
        a = next(w for w in sh["wards"] if w["ward_code"] == "WARDAAAAAAA")
        self.assertEqual((a["coverage"], a["pct"][0]), (80.0, 100.0))

    def test_reingest_replaces_and_recomputes_shares(self):
        n = PixelLayer.objects.count()
        g = vci_grid(); g[:, :5] = 60.0
        layer, _ = ps.store_layer(indicator="vci", period_start=dt.date(2026, 9, 30),
                                  period_end=dt.date(2026, 10, 15), bounds=BOUNDS,
                                  arr_float=g, source="t", methodology_version="m2",
                                  ward_geoms=WARDS)
        self.assertEqual(PixelLayer.objects.count(), n)
        self.assertEqual(layer.shares.count(), 2)
        self.assertEqual(layer.shares.get(ward_code="WARDAAAAAAA").pct_normal, 100.0)

    def test_breaks_change_needs_no_reingest(self):
        pk = PixelLayer.objects.first().pk
        url = f"{self.base}pixels/{pk}/value/?lat=2.5&lon=37.75"   # stored value 30
        self.assertEqual(self.client.get(url).json()["class"], "Alarm")   # 20 <= 30 < 35
        with self.settings(TERRA_VCI_BREAKS=(15, 25, 40)):
            self.assertEqual(self.client.get(url).json()["class"], "Alert")   # 25 <= 30 < 40


class Commands(TestCase):
    def test_update_pixels_tif(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "v.tif"); make_tif(p, vci_grid())
            with mock.patch.object(ps, "load_ward_geoms", return_value=WARDS):
                call_command("update_pixels", tif=p, period_start="2026-09-30",
                             period_end="2026-10-15", ward_code_col="code", stdout=io.StringIO())
        layer = PixelLayer.objects.get()
        self.assertEqual(layer.status, "Derived")
        self.assertEqual(layer.params["baseline"], [2001, 2020])
        self.assertEqual(layer.shares.count(), 2)

    def test_update_pixels_requires_explicit_shares_choice(self):
        with self.assertRaises(CommandError):
            call_command("update_pixels", tif="x.tif", period_start="2026-09-30",
                         period_end="2026-10-15", stdout=io.StringIO())
        with self.assertRaises(CommandError):
            call_command("update_pixels", stdout=io.StringIO())

    def test_import_outlook_csv_leads_skips_and_null(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "o.csv")
            with open(p, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["ward_code", "valid_start", "valid_end", "variable", "value", "units"])
                for code in WARDS:
                    w.writerow([code, "2026-11-01", "2026-11-30", "spi_pctile", "35", "percentile"])
                    w.writerow([code, "2026-12-01", "2026-12-31", "spi_pctile", "", "percentile"])
                    w.writerow([code, "2027-01-01", "2027-01-31", "spi_pctile", "60", "percentile"])
                    w.writerow([code, "2027-02-01", "2027-02-28", "spi_pctile", "70", "percentile"])
            out = io.StringIO()
            call_command("import_outlook", csv=p, issued="2026-11-05", source="TEST-MME", stdout=out)
        self.assertIn("skipped 2 rows", out.getvalue())
        self.assertEqual(SeasonalOutlook.objects.count(), 6)
        self.assertEqual(sorted(set(SeasonalOutlook.objects.values_list("lead", flat=True))), [1, 2, 3])
        self.assertEqual(SeasonalOutlook.objects.filter(value__isnull=True).count(), 2)  # not zero
        j = self.client.get("/api/v1/boundaries/outlook/").json()
        blk = j["outlooks"][0]
        self.assertEqual((blk["source"], blk["status"], blk["issued"]), ("TEST-MME", "Reported", "2026-11-05"))
        self.assertEqual(sorted(blk["leads"]), ["1", "2", "3"])
        self.assertIsNone(blk["leads"]["2"]["variables"]["spi_pctile"]["wards"]["WARDAAAAAAA"])
        self.assertEqual(blk["leads"]["3"]["variables"]["spi_pctile"]["wards"]["WARDBBBBBBB"], 60.0)
        self.assertIn("not a terra range prediction", j["note"].lower())

    def test_import_outlook_tif_and_reimport_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "c.tif"); make_tif(p, np.array([[1.0, 3.0], [1.0, 3.0]]))
            for _ in range(2):
                with mock.patch.object(ps, "load_ward_geoms", return_value=WARDS):
                    call_command("import_outlook", tif=p, issued="2026-11-05", source="T",
                                 variable="p_below", valid_start="2026-12-01",
                                 valid_end="2026-12-31", ward_code_col="c", stdout=io.StringIO())
        self.assertEqual(SeasonalOutlook.objects.count(), 2)
        o = SeasonalOutlook.objects.get(ward_code="WARDBBBBBBB")
        self.assertEqual((o.lead, o.value), (2, 3.0))

    def test_import_outlook_rejects_bad_variable(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "o.csv")
            with open(p, "w") as f:
                f.write("ward_code,valid_start,valid_end,variable,value\nW,2026-11-01,2026-11-30,Bad Name,1\n")
            with self.assertRaises(CommandError):
                call_command("import_outlook", csv=p, issued="2026-11-05", source="T", stdout=io.StringIO())


class BackfillHelpers:
    """EE is mocked: list_ee_composites and fetch_ee_vci_tif are replaced."""

    DATES = [dt.date(2025, 1, 1), dt.date(2025, 1, 17), dt.date(2025, 2, 2), dt.date(2025, 2, 18)]

    def _fetch_factory(self, tmpdir, bad=(), empty=()):
        def fake(bbox, out_path, *, indicators, period_start, baseline, min_range, qa_max):
            if period_start in bad:
                raise RuntimeError("EE timed out")
            bands = []
            for ind in indicators:
                if period_start in empty:
                    bands.append(np.full((10, 10), np.nan, dtype="float32"))
                else:
                    bands.append(vci_grid() if ind == "vci" else ndvi_grid())
            make_tif_multi(out_path, bands)
            return period_start, period_start + dt.timedelta(days=15)
        return fake

    def _run(self, fake, ward=True, **kw):
        from .management.commands import update_pixels as up
        out = io.StringIO()
        extra = {"ward_code_col": "c"} if ward else {}
        with mock.patch.object(up, "list_ee_composites", return_value=self.DATES), \
             mock.patch.object(up, "fetch_ee_tif", side_effect=fake), \
             mock.patch.object(ps, "load_ward_geoms", return_value=WARDS):
            call_command("update_pixels", ee=True, from_date="2025-01-01",
                         stdout=out, **extra, **kw)
        return out.getvalue()



class BackfillAndPrune(BackfillHelpers, TestCase):
    def test_backfill_stores_all_then_skips_existing(self):
        out = self._run(self._fetch_factory(None))
        self.assertEqual(PixelLayer.objects.count(), 4)
        self.assertEqual(WardClassShare.objects.count(), 8)
        out2 = self._run(self._fetch_factory(None))
        self.assertIn("4 complete for ['vci'], 0 to fetch", out2)

    def test_backfill_continues_after_failure_and_reports(self):
        fake = self._fetch_factory(None, bad={self.DATES[1]}, empty={self.DATES[2]})
        with self.assertRaises(CommandError) as cm:
            self._run(fake)
        self.assertIn("1 failed and 1 fully masked of 4", str(cm.exception))
        stored = sorted(PixelLayer.objects.values_list("period_start", flat=True))
        self.assertEqual(stored, [self.DATES[0], self.DATES[3]])      # others still stored
        # retry fetches only what is missing: the failed date now succeeds, the
        # fully masked one is reported again, and the stored two are skipped
        with self.assertRaises(CommandError) as cm2:
            self._run(self._fetch_factory(None, empty={self.DATES[2]}))
        self.assertIn("0 failed and 1 fully masked of 2", str(cm2.exception))
        self.assertIn(self.DATES[1], PixelLayer.objects.values_list("period_start", flat=True))

    def test_from_date_rules(self):
        with self.assertRaises(CommandError):
            call_command("update_pixels", tif="x.tif", from_date="2025-01-01",
                         no_shares=True, stdout=io.StringIO())
        with self.assertRaises(CommandError):
            call_command("update_pixels", ee=True, from_date="2025-01-01",
                         period_start="2025-01-01", no_shares=True, stdout=io.StringIO())

    def test_prune_dry_run_default_then_delete_keeps_outlooks(self):
        for d in self.DATES:
            ps.store_layer(indicator="vci", period_start=d, period_end=d + dt.timedelta(days=15),
                           bounds=BOUNDS, arr_float=vci_grid(), source="t",
                           methodology_version="m", ward_geoms=WARDS)
        SeasonalOutlook.objects.create(issued=dt.date(2024, 11, 5), lead=1,
                                       valid_start=dt.date(2024, 11, 1), valid_end=dt.date(2024, 11, 30),
                                       ward_code="W", variable="v", value=1.0, source="s",
                                       methodology_version="o")
        out = io.StringIO()
        call_command("prune_pixels", keep_from="2025-02-01", stdout=out)
        self.assertIn("2 layers before 2025-02-01", out.getvalue())
        self.assertEqual(PixelLayer.objects.count(), 4)                # dry run
        call_command("prune_pixels", keep_from="2025-02-01", yes=True, stdout=io.StringIO())
        self.assertEqual(PixelLayer.objects.count(), 2)
        self.assertEqual(WardClassShare.objects.count(), 4)            # cascaded
        self.assertEqual(SeasonalOutlook.objects.count(), 1)           # untouched

    def test_prune_refuses_to_delete_everything(self):
        ps.store_layer(indicator="vci", period_start=self.DATES[0], period_end=self.DATES[1],
                       bounds=BOUNDS, arr_float=vci_grid(), source="t", methodology_version="m")
        with self.assertRaises(CommandError):
            call_command("prune_pixels", keep_from="2030-01-01", yes=True, stdout=io.StringIO())
        self.assertEqual(PixelLayer.objects.count(), 1)


class NdviPixels(TestCase):
    base = "/api/v1/boundaries/"

    def test_ndvi_quantise_roundtrip_clip_and_nodata(self):
        v = np.array([-0.5, -0.2, 0.0, 0.1234, 0.6, 1.0, 1.5, np.nan])
        u8 = ps.quantise_ndvi(v)
        back = ps.decode_ndvi(u8)
        self.assertEqual(u8[-1], 255)
        self.assertTrue(np.isnan(back[-1]))
        self.assertAlmostEqual(float(back[0]), -0.2, places=6)       # clipped low
        self.assertAlmostEqual(float(back[-2]), 1.07, places=6)      # clipped high
        ok = slice(1, 6)
        self.assertLessEqual(float(np.abs(back[ok] - v[ok]).max()), 0.0025 + 1e-6)

    def test_ndvi_render_ramp_and_transparency(self):
        u8 = ps.quantise_ndvi(np.array([[0.0, 0.7], [np.nan, 0.3]]))
        im = np.array(Image.open(io.BytesIO(ps.render_ndvi_png(u8))))
        self.assertEqual(im[1, 0, 3], 0)                    # nodata transparent
        self.assertGreater(im[0, 0, 0], im[0, 0, 1])        # bare: red > green
        self.assertGreater(im[0, 1, 1], im[0, 1, 0] + 50)   # dense: green >> red

    def test_unknown_indicator_rejected(self):
        with self.assertRaises(ValueError):
            ps.quantise_for("lst", np.zeros((2, 2)))
        self.assertEqual(self.client.get(self.base + "pixels/?indicator=lst").status_code, 400)

    def test_store_and_serve_ndvi_next_to_vci(self):
        d = dt.date(2026, 9, 30)
        for ind, grid in (("vci", vci_grid()), ("ndvi", ndvi_grid())):
            ps.store_layer(indicator=ind, period_start=d, period_end=d + dt.timedelta(days=15),
                           bounds=BOUNDS, arr_float=grid, source="t", methodology_version="m",
                           ward_geoms=WARDS)
        self.assertEqual(PixelLayer.objects.count(), 2)                 # same period, two rows
        nd = PixelLayer.objects.get(indicator="ndvi")
        self.assertEqual(nd.encoding, ps.NDVI_ENCODING)
        self.assertEqual(nd.shares.count(), 0)                          # no NDVI class shares
        self.assertEqual(WardClassShare.objects.filter(layer__indicator="vci").count(), 2)
        j = self.client.get(self.base + "pixels/?indicator=ndvi").json()
        self.assertEqual((j["legend_type"], j["breaks"], j["selected"]), ("ramp", [], nd.pk))
        self.assertTrue(j["legend"][0]["color"].startswith("#"))
        v = self.client.get(f"{self.base}pixels/{nd.pk}/value/?lat=2.5&lon=37.25").json()
        self.assertEqual((v["value"], v["class"]), (0.1, None))
        v = self.client.get(f"{self.base}pixels/{nd.pk}/value/?lat=2.5&lon=37.05").json()
        self.assertIsNone(v["value"])                                   # nodata, not zero
        sh = self.client.get(f"{self.base}pixels/{nd.pk}/shares/").json()
        self.assertEqual(sh["wards"], [])
        r = self.client.get(f"{self.base}pixels/{nd.pk}/image.png")
        self.assertEqual((r.status_code, r["Content-Type"]), (200, "image/png"))
        self.assertEqual(self.client.get(f"{self.base}pixels/{nd.pk}/image.png",
                                         HTTP_IF_NONE_MATCH=r["ETag"]).status_code, 304)
        vj = self.client.get(self.base + "pixels/?indicator=vci").json()
        self.assertEqual(vj["legend_type"], "classes")                  # VCI unchanged

    def test_update_pixels_tif_band_for_ndvi(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "two.tif")
            make_tif_multi(p, [vci_grid(), ndvi_grid()])
            call_command("update_pixels", tif=p, indicator="ndvi", band=2,
                         period_start="2026-09-30", period_end="2026-10-15",
                         stdout=io.StringIO())            # no ward col needed for NDVI
        layer = PixelLayer.objects.get()
        u8 = ps.unpack(layer.values, layer.width, layer.height)
        self.assertEqual(ps.decode_value("ndvi", int(u8[0, 9])), 0.6)
        self.assertEqual(layer.params["quantisation"], ps.NDVI_ENCODING)

    def test_tif_rejects_indicator_list(self):
        with self.assertRaises(CommandError):
            call_command("update_pixels", tif="x.tif", indicator="vci,ndvi",
                         period_start="2026-09-30", period_end="2026-10-15",
                         no_shares=True, stdout=io.StringIO())


class BackfillBoth(BackfillHelpers, TestCase):
    """Same fixtures, two indicators per download."""

    def test_backfill_vci_and_ndvi_and_fills_only_missing(self):
        ps.store_layer(indicator="vci", period_start=self.DATES[0],
                       period_end=self.DATES[0] + dt.timedelta(days=15), bounds=BOUNDS,
                       arr_float=vci_grid(), source="t", methodology_version="m")
        out = self._run(self._fetch_factory(None), indicator="vci,ndvi")
        self.assertIn("[1/4] 2025-01-01 -> ['ndvi']", out)               # vci already there
        self.assertEqual(PixelLayer.objects.filter(indicator="vci").count(), 4)
        self.assertEqual(PixelLayer.objects.filter(indicator="ndvi").count(), 4)
        self.assertEqual(WardClassShare.objects.count(), 6)              # 3 new vci layers x 2

    def test_ndvi_only_backfill_needs_no_ward_column(self):
        out = self._run(self._fetch_factory(None), ward=False, indicator="ndvi",
                        bbox=[37.0, 2.0, 38.0, 3.0])
        self.assertEqual(PixelLayer.objects.filter(indicator="ndvi").count(), 4)
        self.assertEqual(WardClassShare.objects.count(), 0)

    def test_vci_still_requires_explicit_ward_choice(self):
        with self.assertRaises(CommandError):
            self._run(self._fetch_factory(None), ward=False, indicator="vci,ndvi",
                      bbox=[37.0, 2.0, 38.0, 3.0])
