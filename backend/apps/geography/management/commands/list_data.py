"""Print a comprehensive inventory of all data, indicators, statistics, and layers stored in the database.

Usage:
    python manage.py list_data
    python manage.py list_data --json
"""
import json
from django.core.management.base import BaseCommand
from django.db.models import Avg, Count, Max, Min, Q

from ...models import (
    ClimateIndex,
    IngestRun,
    PixelLayer,
    SeasonalOutlook,
    WardClassShare,
    WardIndicator,
    WardStat,
    WaterPoint,
)


class Command(BaseCommand):
    help = "List all datasets, indicators, statistics, and layers stored in the database."

    def add_arguments(self, p):
        p.add_argument("--json", action="store_true", help="Output results as JSON")

    def handle(self, *args, **options):
        # 1. Ward Indicators
        ind_names = sorted(WardIndicator.objects.values_list("indicator", flat=True).distinct())
        indicators_data = []
        for ind in ind_names:
            q = WardIndicator.objects.filter(indicator=ind)
            agg = q.aggregate(
                total=Count("id"),
                non_null=Count("id", filter=Q(value__isnull=False)),
                first=Min("period_start"),
                last=Max("period_start"),
            )
            sources = list(q.values_list("source", flat=True).distinct())
            total = agg["total"] or 0
            non_null = agg["non_null"] or 0
            pct = (non_null / total * 100) if total else 0.0
            indicators_data.append({
                "indicator": ind,
                "total_rows": total,
                "valid_rows": non_null,
                "valid_pct": round(pct, 1),
                "first_date": str(agg["first"]) if agg["first"] else "-",
                "last_date": str(agg["last"]) if agg["last"] else "-",
                "sources": ", ".join(sources),
            })

        # 2. Ward Statistics
        stat_names = sorted(WardStat.objects.values_list("stat", flat=True).distinct())
        stats_data = []
        for stat in stat_names:
            q = WardStat.objects.filter(stat=stat)
            agg = q.aggregate(
                total=Count("id"),
                non_null=Count("id", filter=Q(value__isnull=False)),
                v_min=Min("value"),
                v_max=Max("value"),
                v_avg=Avg("value"),
            )
            sources = list(q.values_list("source", flat=True).distinct())
            stats_data.append({
                "stat": stat,
                "wards": agg["total"] or 0,
                "non_null": agg["non_null"] or 0,
                "min": round(agg["v_min"], 2) if agg["v_min"] is not None else None,
                "max": round(agg["v_max"], 2) if agg["v_max"] is not None else None,
                "avg": round(agg["v_avg"], 2) if agg["v_avg"] is not None else None,
                "sources": ", ".join(sources),
            })

        # 3. Water Points
        wp_total = WaterPoint.objects.count()
        wp_sources = list(WaterPoint.objects.values("source").annotate(count=Count("id")).order_by("-count"))
        wp_kinds = list(WaterPoint.objects.values("kind").annotate(count=Count("id")).order_by("-count"))
        wp_status = list(WaterPoint.objects.values("reported_status").annotate(count=Count("id")).order_by("-count"))
        water_data = {
            "total_points": wp_total,
            "with_seasonal_profile": WaterPoint.objects.filter(season__isnull=False).count(),
            "surface_water_detected": WaterPoint.objects.filter(water_ever=True).count(),
            "by_source": {r["source"]: r["count"] for r in wp_sources},
            "by_kind": {r["kind"]: r["count"] for r in wp_kinds},
            "by_reported_status": {r["reported_status"]: r["count"] for r in wp_status},
        }

        # 4. Climate Driver Indices & Compressed Datasets
        climate_items = []
        for c in ClimateIndex.objects.order_by("name"):
            saved_pct = (1.0 - c.compression_ratio) * 100
            climate_items.append({
                "name": c.name,
                "provider": c.provider,
                "raw_bytes": c.raw_bytes,
                "compressed_bytes": c.compressed_bytes,
                "compression_ratio": f"{c.compression_ratio:.1%}",
                "space_saved": f"{saved_pct:.1f}%",
                "retrieved_at": str(c.retrieved_at),
            })

        # 5. Pixel Layers & Forecast Outlooks
        pixel_layers = list(
            PixelLayer.objects.values("indicator")
            .annotate(count=Count("id"), first=Min("period_start"), last=Max("period_start"))
        )
        outlooks = list(
            SeasonalOutlook.objects.values("variable", "source")
            .annotate(count=Count("id"), first=Min("valid_start"), last=Max("valid_end"))
        )

        # 6. Ingestion Runs
        recent_runs = list(
            IngestRun.objects.order_by("-started_at")
            .values("command", "status", "started_at", "finished_at", "rows_written")[:5]
        )

        all_data = {
            "indicators": indicators_data,
            "ward_statistics": stats_data,
            "water_points": water_data,
            "climate_indices_compressed": climate_items,
            "pixel_layers": pixel_layers,
            "seasonal_outlooks": outlooks,
            "recent_ingest_runs": [
                {
                    **r,
                    "started_at": r["started_at"].isoformat() if r["started_at"] else None,
                    "finished_at": r["finished_at"].isoformat() if r["finished_at"] else None,
                }
                for r in recent_runs
            ],
        }

        if options["json"]:
            self.stdout.write(json.dumps(all_data, indent=2))
            return

        # Terminal Print Formatting
        self.stdout.write(self.style.SUCCESS("=" * 80))
        self.stdout.write(self.style.SUCCESS("           TERRA RANGE DATABASE INVENTORY"))
        self.stdout.write(self.style.SUCCESS("=" * 80))

        # 1. Indicators
        self.stdout.write(self.style.MIGRATE_HEADING(f"\n1. WARD TIME-SERIES INDICATORS ({len(indicators_data)} types)"))
        self.stdout.write(f"{'Indicator':<18} {'Rows':>8} {'Valid':>8} {'Valid%':>7} {'First Date':>11} {'Last Date':>11} {'Source'}")
        self.stdout.write("-" * 80)
        for r in indicators_data:
            self.stdout.write(
                f"{r['indicator']:<18} {r['total_rows']:>8} {r['valid_rows']:>8} {r['valid_pct']:>6.1f}% "
                f"{r['first_date']:>11} {r['last_date']:>11} {r['sources']}"
            )

        # 2. Ward Statistics
        self.stdout.write(self.style.MIGRATE_HEADING(f"\n2. WARD STATIC STATISTICS ({len(stats_data)} types)"))
        self.stdout.write(f"{'Statistic':<28} {'Wards':>6} {'Min':>10} {'Max':>10} {'Avg':>10} {'Source'}")
        self.stdout.write("-" * 80)
        for r in stats_data:
            vmin = f"{r['min']:.1f}" if r["min"] is not None else "-"
            vmax = f"{r['max']:.1f}" if r["max"] is not None else "-"
            vavg = f"{r['avg']:.1f}" if r["avg"] is not None else "-"
            self.stdout.write(
                f"{r['stat']:<28} {r['wards']:>6} {vmin:>10} {vmax:>10} {vavg:>10} {r['sources']}"
            )

        # 3. Water Points
        self.stdout.write(self.style.MIGRATE_HEADING(f"\n3. WATER POINTS INVENTORY (Total: {water_data['total_points']})"))
        self.stdout.write(f"  • With JRC 12-Month Seasonality Profile: {water_data['with_seasonal_profile']}")
        self.stdout.write(f"  • With Detected Surface Water History:  {water_data['surface_water_detected']}")
        self.stdout.write("\n  By Source Provider:")
        for s, c in water_data["by_source"].items():
            self.stdout.write(f"    - {s:<18}: {c:>6} points")
        self.stdout.write("\n  By Point Kind:")
        for k, c in water_data["by_kind"].items():
            self.stdout.write(f"    - {k:<18}: {c:>6} points")
        self.stdout.write("\n  By Reported Status:")
        for st, c in water_data["by_reported_status"].items():
            self.stdout.write(f"    - {st:<18}: {c:>6} points")

        # 4. Climate Indices & Compressed Datasets
        self.stdout.write(self.style.MIGRATE_HEADING(f"\n4. COMPRESSED DATASETS & CLIMATE DRIVERS ({len(climate_items)} stored)"))
        self.stdout.write(f"{'Dataset Name':<32} {'Raw Size':>10} {'Compressed':>12} {'Saved':>8} {'Provider'}")
        self.stdout.write("-" * 80)
        for r in climate_items:
            self.stdout.write(
                f"{r['name']:<32} {r['raw_bytes']:>8} B {r['compressed_bytes']:>10} B {r['space_saved']:>8} {r['provider']}"
            )

        # 5. Pixel Layers / Outlooks
        if pixel_layers:
            self.stdout.write(self.style.MIGRATE_HEADING(f"\n5. PIXEL LAYERS ({len(pixel_layers)})"))
            for p in pixel_layers:
                self.stdout.write(f"  • {p['indicator']}: {p['count']} layers ({p['first']} to {p['last']})")
        if outlooks:
            self.stdout.write(self.style.MIGRATE_HEADING(f"\n6. SEASONAL OUTLOOKS ({len(outlooks)})"))
            for o in outlooks:
                self.stdout.write(f"  • {o['variable']} ({o['source']}): {o['count']} rows ({o['first']} to {o['last']})")

        # Ingest Runs
        if recent_runs:
            self.stdout.write(self.style.MIGRATE_HEADING("\nRECENT INGESTION RUNS"))
            for r in recent_runs:
                fin = r["finished_at"].strftime("%Y-%m-%d %H:%M") if r["finished_at"] else "running"
                self.stdout.write(f"  • [{r['status']:<7}] {r['command']:<20} rows: {r['rows_written']:>7}  finished: {fin}")
        self.stdout.write(self.style.SUCCESS("\n" + "=" * 80))

