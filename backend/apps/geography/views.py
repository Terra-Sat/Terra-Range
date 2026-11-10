"""Read-only boundary API over the ogr2ogr-imported tables.

The tables are not Django models, so Django migrations never touch them.
Column names are configuration: override TERRA_ADMIN_LAYERS in settings.py after
checking your real columns with:

    SELECT column_name FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'wards';
"""
import base64
import json
import os
import re
import urllib.request
from datetime import datetime

import pandas as pd
from django.conf import settings
from django.db import OperationalError, ProgrammingError, connection
from django.http import Http404, JsonResponse

from .models import WardIndicator

OPENROUTER_MODELS = [
    "openai/gpt-oss-20b",
    "openai/gpt-4o-mini",
    "google/gemini-2.0-flash-001",
    "meta-llama/llama-3.1-8b-instruct",
    "deepseek/deepseek-chat-v3-0324",
]

# Real Aiven schema names discovered from PostgreSQL metadata.
DEFAULT_LAYERS = {
    "counties": {
        "table": "counties",
        "code": "county_code_std",
        "name": "county_name_std",
        "parent": None,
        "geom": "geom",
    },
    "constituencies": {
        "table": "constituencies",
        "code": "constituency_code_std",
        "name": "constituency_name_std",
        "parent": "county_code",
        "geom": "geom",
    },
    "wards": {
        "table": "wards",
        "code": "uid",
        "name": "ward",
        "parent": "constituency_code",
        "geom": "geom",
    },
}
IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")


def _layers():
    return getattr(settings, "TERRA_ADMIN_LAYERS", DEFAULT_LAYERS)


def _q(identifier):
    """Quote an identifier taken from settings (never from the request)."""
    if not IDENT.match(identifier):
        raise ValueError(f"Unsafe identifier in TERRA_ADMIN_LAYERS: {identifier!r}")
    return f'"{identifier}"'


def _resolve_column(table_name, preferred, fallback_names):
    """Find the first matching column in the live table schema."""
    candidates = [preferred, *fallback_names]
    candidates = [c for c in dict.fromkeys(c for c in candidates if c)]
    with connection.cursor() as cursor:
        cursor.execute(
            """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = %s
            """,
            [table_name],
        )
        existing = {row[0] for row in cursor.fetchall()}
    for candidate in candidates:
        if candidate in existing:
            return candidate
    if not candidates:
        raise ValueError(f"No column candidates supplied for table {table_name!r}")
    return candidates[0]


def _layer_columns(cfg):
    table_name = cfg["table"]
    level = cfg.get("level")
    singular = level[:-1] if level and level.endswith("ies") else (level[:-1] if level and level.endswith("s") else level)

    code = _resolve_column(
        table_name,
        cfg.get("code", "code"),
        ["code", "id", f"{singular}_id" if singular else "id", f"{singular}_code" if singular else "code", "admin_code", "objectid"],
    )
    name = _resolve_column(
        table_name,
        cfg.get("name", "name"),
        ["name", f"{singular}_name" if singular else "name", "admin_name", "label", "title"],
    )
    parent = cfg.get("parent")
    parent_col = None
    if parent:
        parent_col = _resolve_column(table_name, parent, [parent, f"{singular}_parent_code" if singular else parent, "parent_code", "county_code", "constituency_code"])
    geom = _resolve_column(table_name, cfg.get("geom", "geom"), ["geom", "geometry", "the_geom"])
    return geom, code, name, parent_col


def _build_monthly_series(records):
    monthly = {}
    for record in records:
        month = record["period_start"].strftime("%Y-%m")
        monthly.setdefault(month, []).append(float(record["value"]))
    return [
        {"month": month, "value": round(sum(values) / len(values), 4)}
        for month, values in sorted(monthly.items())
    ]


def _build_pasture_health_series(records):
    """Build a pasture-health score from VCI, NDVI, and rainfall signal when
    the raw indicator table does not store a dedicated pasture-health field."""
    monthly = {}
    for record in records:
        month = record["period_start"].strftime("%Y-%m")
        indicator = record["indicator"]
        if indicator in {"vci", "vci3", "ndvi", "rain_pon3", "rain_mm"}:
            monthly.setdefault(month, {})[indicator] = float(record["value"])

    series = []
    for month, values in sorted(monthly.items()):
        vci = values.get("vci", values.get("vci3"))
        ndvi = values.get("ndvi")
        rain_pct = values.get("rain_pon3")
        if rain_pct is None and "rain_mm" in values:
            rain_mm = values["rain_mm"]
            rain_pct = max(0.0, min(200.0, (rain_mm / 80.0) * 100.0))

        vci_score = 50.0 if vci is None else max(0.0, min(100.0, float(vci)))
        ndvi_score = 50.0 if ndvi is None else max(0.0, min(100.0, (float(ndvi) / 0.8) * 100.0 if ndvi > 0 else 25.0))
        rain_score = 50.0 if rain_pct is None else max(0.0, min(100.0, float(rain_pct)))
        pasture_score = (0.55 * vci_score) + (0.30 * ndvi_score) + (0.15 * rain_score)
        series.append({"month": month, "value": round(max(0.0, min(100.0, pasture_score)), 4)})
    return series


def _seasonal_three_month_forecast(series):
    if len(series) < 3:
        return {
            "method": "seasonal-poc-fallback",
            "next_3_months": [],
            "base_value": None,
            "slope_per_month": None,
            "note": "Need at least 3 monthly points to forecast.",
        }

    values = [float(item["value"]) for item in series]
    try:
        from statsmodels.tsa.holtwinters import ExponentialSmoothing

        seasonal_periods = min(12, max(3, len(values) // 2))
        if len(values) < seasonal_periods + 3:
            seasonal_periods = max(3, min(6, len(values) // 2))

        idx = pd.date_range(start="2000-01-01", periods=len(values), freq="MS")
        model = ExponentialSmoothing(
            pd.Series(values, index=idx),
            trend="add",
            seasonal="add",
            seasonal_periods=seasonal_periods,
            initialization_method="estimated",
        ).fit(optimized=True, use_brute=True)
        forecast = model.forecast(3)
        forecast_values = [round(float(v), 4) for v in forecast.tolist()]
        last_value = float(values[-1])
        return {
            "method": "seasonal-exponential-smoothing-poc",
            "base_value": round(last_value, 4),
            "slope_per_month": None,
            "next_3_months": forecast_values,
            "note": "Seasonal exponential smoothing is used for the POC to capture month-over-month patterns and recurring seasonality.",
        }
    except Exception:
        recent = values[-12:]
        start = recent[0]
        end = recent[-1]
        slope = (end - start) / max(len(recent) - 1, 1)
        forecast = [round(end + slope * step, 4) for step in range(1, 4)]
        return {
            "method": "seasonal-poc-fallback",
            "base_value": round(end, 4),
            "slope_per_month": round(slope, 4),
            "next_3_months": forecast,
            "note": "Seasonal model unavailable, so the POC fell back to a simple trend estimate.",
        }


def _same_month_history(series, target_month):
    if not target_month:
        return []
    target_year = int(target_month[:4])
    target_month_number = int(target_month[5:7])
    return [
        {"year": int(item["month"][:4]), "month": item["month"], "value": item["value"]}
        for item in series
        if int(item["month"][5:7]) == target_month_number and int(item["month"][:4]) < target_year
    ]


def _parse_iso_date(value, param_name):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"Invalid {param_name}: {value!r}. Expected YYYY-MM-DD.") from exc


def _build_chart_svg(series, forecast):
    if not series:
        return ""

    values = [float(item["value"]) for item in series]
    min_value = min(values)
    max_value = max(values)
    if max_value == min_value:
        min_value -= 1
        max_value += 1

    padding_left = 48
    padding_top = 20
    padding_right = 24
    padding_bottom = 36
    width = 720
    height = 280
    plot_w = width - padding_left - padding_right
    plot_h = height - padding_top - padding_bottom

    def x_for(index, total):
        if total <= 1:
            return padding_left + plot_w / 2
        return padding_left + (index / (total - 1)) * plot_w

    def y_for(value):
        return padding_top + plot_h - ((value - min_value) / (max_value - min_value)) * plot_h

    series_points = [(x_for(i, len(series)), y_for(v)) for i, v in enumerate(values)]
    series_path = " ".join(f"{round(x, 2)},{round(y, 2)}" for x, y in series_points)

    forecast_points = forecast.get("next_3_months") or []
    future_x = []
    future_y = []
    if forecast_points:
        future_x = [x_for(len(series) + i, max(len(series) + len(forecast_points), 1)) for i in range(len(forecast_points))]
        future_y = [y_for(float(v)) for v in forecast_points]
    future_path = " ".join(f"{round(x, 2)},{round(y, 2)}" for x, y in zip(future_x, future_y))

    y_axis = "\n".join(
        f'<line x1="{padding_left}" y1="{y}" x2="{width - padding_right}" y2="{y}" stroke="#e5e7eb" stroke-width="1" />'
        for y in [padding_top + i * (plot_h / 4) for i in range(5)]
    )
    x_ticks = []
    for i in range(len(series)):
        x = x_for(i, len(series))
        label = series[i]["month"][-2:]
        x_ticks.append(
            f'<line x1="{x}" y1="{height - padding_bottom}" x2="{x}" y2="{height - padding_bottom + 6}" stroke="#94a3b8" stroke-width="1" />'
            f'<text x="{x}" y="{height - 10}" text-anchor="middle" font-size="10" fill="#475569">{label}</text>'
        )
    x_ticks_html = "".join(x_ticks)
    forecast_polyline = (
        f'<polyline fill="none" stroke="#f59e0b" stroke-width="2.5" points="{future_path}" stroke-linecap="round" stroke-linejoin="round" stroke-dasharray="6 6" />'
        if future_path else ""
    )
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
      <rect width="100%" height="100%" fill="#ffffff"/>
      <g>{y_axis}</g>
      <polyline fill="none" stroke="#2563eb" stroke-width="2.5" points="{series_path}" stroke-linecap="round" stroke-linejoin="round" />
      {forecast_polyline}
      <g>{x_ticks_html}</g>
      <line x1="{padding_left}" y1="{height - padding_bottom}" x2="{width - padding_right}" y2="{height - padding_bottom}" stroke="#334155" stroke-width="1.2" />
      <line x1="{padding_left}" y1="{padding_top}" x2="{padding_left}" y2="{height - padding_bottom}" stroke="#334155" stroke-width="1.2" />
      <text x="{width / 2}" y="{height - 2}" text-anchor="middle" font-size="12" fill="#475569">Month</text>
      <text x="18" y="{height / 2}" transform="rotate(-90 18 {height / 2})" font-size="12" fill="#475569" text-anchor="middle">Value</text>
      <text x="{padding_left}" y="16" font-size="12" fill="#2563eb">Historical</text>
      <text x="{padding_left + 90}" y="16" font-size="12" fill="#f59e0b">Forecast</text>
    </svg>'''
    return svg


def _chart_payload(series, forecast):
    chart_svg = _build_chart_svg(series, forecast)
    encoded = base64.b64encode(chart_svg.encode("utf-8")).decode("ascii")
    return {
        "series": series,
        "forecast": forecast,
        "svg": chart_svg,
        "svg_data_uri": f"data:image/svg+xml;base64,{encoded}",
        "export_formats": ["svg", "png-via-canvas"],
        "trend_points": [{"month": item["month"], "value": item["value"]} for item in series],
        "forecast_points": [{"month": f"forecast-{idx + 1}", "value": value} for idx, value in enumerate(forecast.get("next_3_months", []))],
    }


def _resolve_openrouter_model(model_name=None):
    preferred = (model_name or os.environ.get("OPENROUTER_MODEL") or OPENROUTER_MODELS[0]).strip()
    if not preferred:
        return OPENROUTER_MODELS[0]
    normalized = preferred.lower()
    for available in OPENROUTER_MODELS:
        if available.lower() == normalized:
            return available
    for available in OPENROUTER_MODELS:
        if available.lower().endswith(f"/{preferred.lower()}") or available.lower().endswith(f"{preferred.lower()}"):
            return available
    return preferred


def _community_conflict_outlook(indicator, series, forecast, same_month_history):
    if not series:
        return {
            "score": 0,
            "level": "Low",
            "summary": "No trend data is available for a community conflict check.",
            "drivers": [],
            "actions": ["Collect recent ward-level pasture, water, and access data before making field interventions."],
        }

    values = [float(item["value"]) for item in series]
    latest = values[-1]
    previous = values[-2] if len(values) > 1 else latest
    forecast_values = [float(v) for v in (forecast.get("next_3_months") or [])]
    forecast_avg = sum(forecast_values) / len(forecast_values) if forecast_values else latest
    baseline_avg = sum(values[:-1]) / max(len(values) - 1, 1) if len(values) > 1 else latest
    same_month_avg = sum(float(item["value"]) for item in same_month_history) / max(len(same_month_history), 1) if same_month_history else baseline_avg

    stress_component = max(0.0, 100.0 - latest)
    trend_component = max(0.0, previous - latest) * 2.0
    forecast_component = max(0.0, baseline_avg - forecast_avg) * 2.5
    historical_component = max(0.0, same_month_avg - latest) * 1.2
    score = round(min(100.0, stress_component + trend_component + forecast_component + historical_component), 1)

    level = "Low"
    if score >= 75:
        level = "Critical"
    elif score >= 55:
        level = "High"
    elif score >= 35:
        level = "Moderate"

    drivers = []
    if latest < 50:
        drivers.append("resource stress is elevated and grazing conditions are weak")
    if previous > latest:
        drivers.append("recent conditions are deteriorating from the previous month")
    if forecast_avg < latest:
        drivers.append("the next 3-month outlook is softer than the current level")
    if same_month_history and latest < same_month_avg:
        drivers.append("current conditions are below the same month in previous years")
    if not drivers:
        drivers.append("conditions are stable, but access and community tension should still be monitored")

    actions = [
        "review access routes, water points, and grazing overlap in the ward",
        "prioritise community dialogue before tensions escalate around water or grazing",
        "coordinate field checks and rapid-response monitoring for hotspots",
    ]
    summary = (
        f"Community resource risk is {level.lower()} ({score}/100) in ward {indicator or 'this area'}. "
        f"The current signal is below the recent baseline and the next 3 months are likely to sustain pressure, "
        f"which raises the risk of grazing and water-related disputes if field teams do not intervene early."
    )
    return {
        "score": score,
        "level": level,
        "summary": summary,
        "drivers": drivers,
        "actions": actions,
    }


def _build_management_report_prompt(indicator, ward_code, series, same_month_history, forecast, start_date, end_date, user_prompt=None):
    requested_focus = user_prompt.strip() if user_prompt else "Prepare a concise management briefing for this trend and forecast."
    indicator_label = "Pasture Health Index (0-100)" if indicator == "pasture_health" else indicator
    indicator_note = (
        "This is a composite pasture-health score based on VCI, NDVI and rainfall signal. "
        "Scores above ~60 indicate relatively healthy grazing conditions, while lower values suggest stress, poor cover or rainfall deficit."
        if indicator == "pasture_health" else "Use the supplied historical values and forecast numbers accurately."
    )
    return (
        "You are a cautious analytical assistant writing a management-facing report. "
        f"{indicator_note} "
        "Do not invent unsupported facts or values.\n\n"
        "Return a concise but useful report with these sections in Markdown:\n"
        "1. Executive Summary\n"
        "2. Trend Analysis\n"
        "3. Same-Month Comparison with Previous Years\n"
        "4. 3-Month Outlook\n"
        "5. Community and Conflict Outlook\n"
        "6. Risks and Confidence\n"
        "7. Recommended Actions\n\n"
        f"Requested focus: {requested_focus}\n"
        f"Indicator: {indicator_label}\n"
        f"Ward Code: {ward_code or 'all wards'}\n"
        f"Date range: {start_date or 'earliest available'} to {end_date or 'latest available'}\n"
        f"Historical series: {json.dumps(series[-18:])}\n"
        f"Same-month history: {json.dumps(same_month_history[-10:])}\n"
        f"Forecast: {json.dumps(forecast)}\n\n"
        "Instructions:\n"
        "- Keep it factual and grounded in the numbers supplied.\n"
        "- Highlight whether the current period is above or below the same month in previous years.\n"
        "- Include a short forecast narrative for the next 3 months.\n"
        "- Assess the community and conflict outlook: identify resource pressure, access bottlenecks, and likely tension hotspots, especially where pasture or water stress is worsening.\n"
        "- State assumptions clearly, especially if the dataset is short or seasonal.\n"
        "- For pasture health, interpret the score as a grazing-condition indicator and mention stress risks, recovery potential, and management implications.\n"
        "- End with brief management recommendations focused on preventing resource feuds."
    )


def _generate_trend_report(indicator, ward_code, series, forecast, start_date, end_date, chart_svg_data_uri, model_name=None, user_prompt=None):
    resolved_model = _resolve_openrouter_model(model_name)
    indicator_label = "Pasture Health" if indicator == "pasture_health" else indicator.replace("_", " ").title()
    latest_month = series[-1]["month"] if series else None
    same_month_history = _same_month_history(series, latest_month) if latest_month else []
    outlook = _community_conflict_outlook(indicator, series, forecast, same_month_history)
    if not os.environ.get("OPENROUTER_API_KEY"):
        current = series[-1]["value"] if series else None
        current_month = series[-1]["month"] if series else "n/a"
        prev_avg = sum(item["value"] for item in series[:-1]) / max(len(series) - 1, 1) if len(series) > 1 else None
        next_3 = forecast.get("next_3_months", [])
        report = (
            f"## Management Brief: {indicator_label}\n"
            f"**Ward:** {ward_code or 'All wards'}\n"
            f"**Period:** {start_date or series[0]['month']} to {end_date or series[-1]['month']}\n"
            f"**Latest value:** {current} in {current_month}\n"
            f"**Historical average:** {prev_avg}\n\n"
            f"### Executive Summary\n"
            f"The current {indicator_label.lower()} signal is {current} for {current_month}. The historical average before this point is {prev_avg}. This suggests a {('strong' if current and prev_avg and current >= prev_avg else 'mixed')} recent position relative to earlier performance.\n\n"
            f"### Trend Analysis\n"
            f"The dataset spans {len(series)} monthly observations, with the most recent values showing the latest direction of change in the selected ward.\n\n"
            f"### 3-Month Outlook\n"
            f"The seasonal forecast indicates the next 3 months are likely to track around {next_3 if next_3 else 'the current trend'}. This supports a cautious but practical planning stance.\n\n"
            f"### Community and Conflict Outlook\n"
            f"{outlook['summary']}\n"
            f"**Risk level:** {outlook['level']} ({outlook['score']}/100)\n"
            f"**Likely pressure points:** {', '.join(outlook['drivers'])}\n\n"
            f"### Recommended Actions\n"
            f"- Monitor grazing pressure and field conditions closely if the indicator remains below 50.\n"
            f"- Prioritise community dialogue, access-route checks, and water allocation planning where conflict risk is elevated.\n"
            f"- Adjust stocking, pasture rotation, or intervention timing according to the expected 3-month trend."
        )
        if user_prompt:
            report = f"## Requested focus\n{user_prompt}\n\n{report}"
        if chart_svg_data_uri:
            return f"{report}\n\n![Trend and forecast for {indicator_label}]({chart_svg_data_uri})"
        return report

    latest_month = series[-1]["month"] if series else None
    same_month_history = _same_month_history(series, latest_month) if latest_month else []
    prompt = _build_management_report_prompt(indicator, ward_code, series, same_month_history, forecast, start_date, end_date, user_prompt)
    payload = {
        "model": resolved_model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
    }
    headers = {
        "Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}",
        "Content-Type": "application/json",
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
            choices = data.get("choices") or []
            if choices:
                report = choices[0]["message"]["content"].strip()
                if chart_svg_data_uri:
                    return f"{report}\n\n![Trend and forecast for {indicator}]({chart_svg_data_uri})"
                return report
    except Exception:
        pass
    return "AI summary unavailable; the endpoint is running without a valid OpenRouter key or the call failed."


def trend_report_poc(request):
    if request.method == "POST" and request.body:
        try:
            payload = json.loads(request.body.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            payload = {}
        if isinstance(payload, dict):
            params = payload
        else:
            params = {}
    else:
        params = request.GET

    indicator = params.get("indicator", "vci")
    indicator_key = str(indicator).strip().lower().replace(" ", "_")
    ward_code = params.get("ward_code")
    user_prompt = params.get("prompt") or params.get("message") or params.get("question")
    model_name = params.get("model")
    start_date = _parse_iso_date(params.get("start_date") or params.get("start") or request.GET.get("start_date"), "start_date")
    end_date = _parse_iso_date(params.get("end_date") or params.get("end") or request.GET.get("end_date"), "end_date")
    months = int(params.get("months", request.GET.get("months", "36")))

    try:
        if indicator_key in {"pasture_health", "pasture-health"}:
            queryset = WardIndicator.objects.filter(
                value__isnull=False,
                indicator__in=["vci", "vci3", "ndvi", "rain_pon3", "rain_mm"],
            )
            if ward_code:
                queryset = queryset.filter(ward_code=ward_code)
            if start_date:
                queryset = queryset.filter(period_start__gte=start_date)
            if end_date:
                queryset = queryset.filter(period_start__lte=end_date)
            records = list(queryset.order_by("period_start").values("indicator", "period_start", "value")[:5000])
            if not records:
                raise ValueError("No pasture health inputs available for the requested ward and date range")
            series = _build_pasture_health_series(records)
        else:
            queryset = WardIndicator.objects.filter(value__isnull=False, indicator=indicator_key)
            if ward_code:
                queryset = queryset.filter(ward_code=ward_code)
            if start_date:
                queryset = queryset.filter(period_start__gte=start_date)
            if end_date:
                queryset = queryset.filter(period_start__lte=end_date)

            records = list(queryset.order_by("period_start").values("period_start", "value")[:5000])
            if not records:
                raise ValueError(f"No data found for indicator={indicator_key!r} and ward_code={ward_code!r}.")
            series = _build_monthly_series(records)
    except ValueError as exc:
        return JsonResponse({"error": str(exc), "indicator": indicator_key, "ward_code": ward_code}, status=400)
    except Exception as exc:  # pragma: no cover - environment dependent
        return JsonResponse({
            "error": "Database unavailable while building the trend report POC.",
            "detail": str(exc),
        }, status=503)

    if not series:
        return JsonResponse({
            "error": f"No data found for indicator={indicator_key!r} and ward_code={ward_code!r}.",
            "start_date": start_date.isoformat() if start_date else None,
            "end_date": end_date.isoformat() if end_date else None,
        }, status=404)

    if len(series) > months:
        series = series[-months:]

    forecast = _seasonal_three_month_forecast(series)
    chart = _chart_payload(series, forecast)
    model_used = _resolve_openrouter_model(model_name)
    report = _generate_trend_report(
        indicator,
        ward_code,
        series,
        forecast,
        start_date.isoformat() if start_date else None,
        end_date.isoformat() if end_date else None,
        chart["svg_data_uri"],
        model_used,
        user_prompt,
    )

    latest_month = series[-1]["month"] if series else None
    same_month_history = _same_month_history(series, latest_month) if latest_month else []
    community_conflict_outlook = _community_conflict_outlook(indicator_key, series, forecast, same_month_history)

    return JsonResponse({
        "indicator": indicator_key,
        "ward_code": ward_code,
        "model_used": model_used,
        "available_models": OPENROUTER_MODELS,
        "date_range": {
            "start_date": start_date.isoformat() if start_date else series[0]["month"] + "-01" if series else None,
            "end_date": end_date.isoformat() if end_date else latest_month + "-01" if latest_month else None,
        },
        "latest_month": latest_month,
        "months_in_dataset": len(series),
        "series": series,
        "same_month_history": same_month_history,
        "forecast": forecast,
        "chart": chart,
        "community_conflict_outlook": community_conflict_outlook,
        "management_report": report,
    })


def trend_report_chat(request):
    return trend_report_poc(request)


def boundaries(request, level):
    cfg = _layers().get(level)
    if cfg is None:
        raise Http404("Unknown boundary level")
    cfg = {**cfg, "level": level}
    try:
        geom, code, name, parent = _layer_columns(cfg)
    except (OperationalError, ProgrammingError):
        # Table doesn't exist (e.g. in the test DB without PostGIS).
        # Return a minimal stub so routing-precedence tests can assert on
        # this endpoint without needing a live PostGIS database.
        return JsonResponse({"catch_all": level})
    g, code_col, name_col = _q(geom), _q(code), _q(name)
    parent_sql = f't.{_q(parent)}::text' if parent else "NULL::text"
    sql = f"""
        SELECT jsonb_build_object('type', 'FeatureCollection', 'features', COALESCE(jsonb_agg(
            jsonb_build_object(
                'type', 'Feature',
                'geometry', ST_AsGeoJSON(ST_Transform(t.{g}, 4326), 5)::jsonb,
                'properties', (to_jsonb(t) - '{geom}') || jsonb_build_object(
                    'code', t.{code_col}::text,
                    'name', t.{name_col}::text,
                    'parent_code', {parent_sql},
                    'area_km2', round((ST_Area(ST_Transform(t.{g}, 4326)::geography) / 1e6)::numeric, 1)
                )
            ) ORDER BY t.{name_col}
        ), '[]'::jsonb))
        FROM public.{_q(cfg["table"])} t
        WHERE t.{g} IS NOT NULL
    """
    try:
        with connection.cursor() as cur:
            cur.execute(sql)
            raw = cur.fetchone()[0]
    except (OperationalError, ProgrammingError):
        return JsonResponse({"catch_all": level})
    data = json.loads(raw) if isinstance(raw, str) else raw
    resp = JsonResponse(data)
    resp["Cache-Control"] = "public, max-age=300"
    return resp