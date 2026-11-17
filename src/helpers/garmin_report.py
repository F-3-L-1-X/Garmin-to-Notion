from __future__ import annotations

from html import escape
from datetime import datetime, timedelta
import json
import math
import os
from pathlib import Path
import re
from statistics import fmean
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin
from urllib.request import urlopen

from notion_client import Client as NotionClient

WIDTH = 1100
CARD_HEIGHT = 218
ZONE_COLORS = ["#94a3b8", "#3b82f6", "#22c55e", "#f59e0b", "#ef4444", "#8b5cf6"]
CHART_COLORS = {
    "speed": "#3b82f6",
    "heart_rate": "#ef4444",
    "cadence": "#f97316",
}
REPORT_TITLE = "Bilan de la séance"
PAGES_BASE_URL = os.getenv(
    "GARMIN_REPORTS_BASE_URL",
    "https://f-3-l-1-x.github.io/Garmin-to-Notion/",
).rstrip("/") + "/"
REPORTS_DIR = Path("site/reports")
REPORTS_INDEX = REPORTS_DIR / "index.json"



def _write_report_site_index(manifest: dict[str, Any]) -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    reports = sorted(
        manifest.get("reports", []),
        key=lambda item: (item.get("title", "").casefold(), item.get("id", "")),
    )
    REPORTS_INDEX.write_text(
        json.dumps({"reports": reports}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    entries = "".join(
        '<li><a href="reports/'
        + quote(item["filename"], safe="")
        + '">'
        + escape(item.get("title", "Séance Garmin"))
        + "</a></li>"
        for item in reports
    )
    if not entries:
        entries = "<li>Les premiers bilans interactifs apparaîtront ici après une séance Garmin.</li>"
    page = """<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bilans Garmin</title>
<style>
body{font:16px/1.5 system-ui,sans-serif;max-width:860px;margin:3rem auto;padding:0 1.2rem;background:#f8fafc;color:#0f172a}
h1{font-size:2rem}li{margin:.8rem 0}a{color:#1d4ed8}
</style>
</head>
<body>
<h1>Bilans interactifs Garmin</h1>
<p>Chaque bilan contient les courbes disponibles et les zones d’entraînement Garmin.</p>
<ul>__ENTRIES__</ul>
</body>
</html>
""".replace("__ENTRIES__", entries)
    (REPORTS_DIR.parent / "index.html").write_text(page, encoding="utf-8")
    (REPORTS_DIR.parent / ".nojekyll").write_text("", encoding="utf-8")


def prepare_report_site() -> None:
    """Restore prior published reports and prepare the Pages artifact directory."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {"reports": []}
    if REPORTS_INDEX.exists():
        try:
            manifest = json.loads(REPORTS_INDEX.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            manifest = {"reports": []}
    else:
        try:
            with urlopen(urljoin(PAGES_BASE_URL, "reports/index.json"), timeout=20) as response:
                manifest = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            if error.code != 404:
                raise RuntimeError("Impossible de lire l’index public des bilans Garmin.") from error
        except URLError as error:
            raise RuntimeError("Impossible de contacter le site public des bilans Garmin.") from error

    available_reports = []
    for item in manifest.get("reports", []):
        filename = str(item.get("filename", ""))
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}\.html", filename):
            continue
        target = REPORTS_DIR / filename
        if target.exists():
            available_reports.append(item)
            continue
        try:
            with urlopen(
                urljoin(PAGES_BASE_URL, "reports/" + quote(filename, safe="")),
                timeout=20,
            ) as response:
                target.write_bytes(response.read())
            available_reports.append(item)
        except HTTPError as error:
            if error.code != 404:
                raise RuntimeError(f"Impossible de restaurer le bilan publié « {filename} ».") from error
        except URLError as error:
            raise RuntimeError(f"Impossible de restaurer le bilan publié « {filename} ».") from error
    manifest = {"reports": available_reports}

    _write_report_site_index(manifest)


def _save_activity_report(activity_id: str, activity_name: str, html: bytes) -> str:
    prepare_report_site()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", activity_id):
        raise ValueError("Identifiant Garmin invalide pour le bilan interactif.")
    filename = f"{activity_id}.html"
    (REPORTS_DIR / filename).write_bytes(html)
    try:
        manifest = json.loads(REPORTS_INDEX.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        manifest = {"reports": []}
    report = {"id": activity_id, "title": activity_name, "filename": filename}
    reports = [item for item in manifest.get("reports", []) if item.get("id") != activity_id]
    reports.append(report)
    _write_report_site_index({"reports": reports})
    return urljoin(PAGES_BASE_URL, "reports/" + quote(filename, safe=""))

def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _sample_times(samples: list[dict], duration: float | None) -> list[float]:
    raw_timestamps = [_number(sample.get("directTimestamp")) for sample in samples]
    valid_timestamps = [value for value in raw_timestamps if value is not None]

    if len(valid_timestamps) >= 2:
        first = valid_timestamps[0]
        last = valid_timestamps[-1]
        divisor = 1000 if abs(first) > 100_000_000_000 else 1
        if last >= first:
            return [
                max(0.0, (value - first) / divisor) if value is not None else 0.0
                for value in raw_timestamps
            ]

    raw_durations = [
        _number(sample.get("sumDuration") or sample.get("sumElapsedDuration"))
        for sample in samples
    ]
    valid_durations = [value for value in raw_durations if value is not None]
    if len(valid_durations) >= 2:
        scale = 1000 if max(valid_durations) > 100_000 else 1
        first = valid_durations[0]
        return [
            max(0.0, (value - first) / scale) if value is not None else 0.0
            for value in raw_durations
        ]

    total = max(duration or 0.0, 0.0)
    if len(samples) < 2:
        return [0.0] * len(samples)
    return [total * index / (len(samples) - 1) for index in range(len(samples))]


def _metric(sample: dict, keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = _number(sample.get(key))
        if value is not None:
            return value
    return None


def _series(
    samples: list[dict],
    times: list[float],
    keys: tuple[str, ...],
    transform=None,
    *,
    positive_only: bool = False,
) -> list[tuple[float, float]]:
    points = []
    for sample, time_value in zip(samples, times):
        value = _metric(sample, keys)
        if value is None or (positive_only and value <= 0):
            continue
        value = transform(value) if transform else value
        if value is not None and math.isfinite(value):
            points.append((time_value, value))
    return points


def _clock(seconds: float) -> str:
    total = max(0, int(seconds))
    return f"{total // 60}:{total % 60:02d}"


def _format_value(value: float, unit: str, lower_is_better: bool) -> str:
    if lower_is_better:
        total_seconds = max(0, int(value * 60))
        return f"{total_seconds // 60}:{total_seconds % 60:02d}"
    if unit == "bpm":
        return f"{value:.0f}"
    if unit in ("km/h", "tr/min", "spm"):
        return f"{value:.1f}"
    return f"{value:.1f}"


def _chart(
    title: str,
    points: list[tuple[float, float]],
    unit: str,
    color: str,
    y: int,
    *,
    lower_is_better: bool = False,
    chart_index: int | None = None,
) -> str:
    if not points:
        return ""

    plot_left = 88
    plot_right = WIDTH - 34
    plot_top = y + 50
    plot_bottom = y + CARD_HEIGHT - 37
    plot_width = plot_right - plot_left
    plot_height = plot_bottom - plot_top

    values = [value for _, value in points]
    low = min(values)
    high = max(values)
    spread = high - low
    padding = max(spread * 0.12, abs(high) * 0.04, 1.0)
    if lower_is_better:
        top_value, bottom_value = max(0.0, low - padding), high + padding
    else:
        top_value, bottom_value = high + padding, max(0.0, low - padding)
    if bottom_value <= top_value:
        bottom_value = top_value + 1.0

    start_time = min(time_value for time_value, _ in points)
    end_time = max(time_value for time_value, _ in points)
    if end_time <= start_time:
        end_time = start_time + 1.0

    def xy(time_value: float, value: float) -> tuple[float, float]:
        x = plot_left + (time_value - start_time) / (end_time - start_time) * plot_width
        ratio = (value - top_value) / (bottom_value - top_value)
        return x, plot_top + ratio * plot_height

    step = max(1, math.ceil(len(points) / 650))
    sampled = points[::step]
    if sampled[-1] != points[-1]:
        sampled.append(points[-1])
    coordinates = [xy(time_value, value) for time_value, value in sampled]
    path_points = " ".join(
        ("M" if index == 0 else "L") + f" {x:.1f} {point_y:.1f}"
        for index, (x, point_y) in enumerate(coordinates)
    )
    area_path = (
        f"M {coordinates[0][0]:.1f} {plot_bottom:.1f} "
        + " ".join(f"L {x:.1f} {point_y:.1f}" for x, point_y in coordinates)
        + f" L {coordinates[-1][0]:.1f} {plot_bottom:.1f} Z"
    )
    average = fmean(values)
    average_y = xy(start_time, average)[1]
    title_text = escape(title)
    unit_text = escape(unit)
    average_text = (
        f"Moyenne : {_format_value(average, unit, lower_is_better)}"
        + (f" {unit_text}" if unit_text else "")
    )

    parts = [
        f'<g data-chart-index="{chart_index if chart_index is not None else -1}"><rect x="12" y="{y}" width="{WIDTH - 24}" height="{CARD_HEIGHT - 8}" rx="10" fill="#ffffff" stroke="#e2e8f0"/>',
        f'<text x="34" y="{y + 30}" font-size="20" font-weight="600" fill="#1e293b">{title_text}</text>',
        f'<text x="{WIDTH - 42}" y="{y + 30}" text-anchor="end" font-size="14" fill="#475569">{escape(average_text)}</text>',
    ]
    for fraction in (0, 0.5, 1):
        grid_y = plot_top + fraction * plot_height
        value = top_value + fraction * (bottom_value - top_value)
        label = _format_value(value, unit, lower_is_better)
        parts.append(
            f'<line x1="{plot_left}" y1="{grid_y:.1f}" x2="{plot_right}" y2="{grid_y:.1f}" stroke="#e5e7eb" stroke-dasharray="3 5"/>'
        )
        parts.append(
            f'<text x="{plot_left - 10}" y="{grid_y + 5:.1f}" text-anchor="end" font-size="12" fill="#64748b">{escape(label)}</text>'
        )
    parts.extend([
        f'<path d="{area_path}" fill="{color}" opacity="0.22"/>',
        f'<path d="{path_points}" fill="none" stroke="{color}" stroke-width="3" stroke-linejoin="round" stroke-linecap="round"/>',
        f'<line x1="{plot_left}" y1="{average_y:.1f}" x2="{plot_right}" y2="{average_y:.1f}" stroke="{color}" stroke-dasharray="5 5" opacity="0.8"/>',
    ])
    for fraction in (0, 0.5, 1):
        x = plot_left + fraction * plot_width
        label_time = start_time + fraction * (end_time - start_time)
        parts.append(
            f'<text x="{x:.1f}" y="{plot_bottom + 22}" text-anchor="middle" font-size="12" fill="#64748b">{_clock(label_time)}</text>'
        )
    if chart_index is not None:
        parts.extend([
            f'<line class="chart-cursor" x1="{plot_left}" y1="{plot_top}" x2="{plot_left}" y2="{plot_bottom}" stroke="#0f172a" stroke-dasharray="4 4" display="none" pointer-events="none"/>',
            f'<circle class="chart-dot" cx="{plot_left}" cy="{plot_top}" r="5" fill="{color}" stroke="#ffffff" stroke-width="2" display="none" pointer-events="none"/>',
            f'<rect class="chart-hit-area" x="{plot_left}" y="{plot_top}" width="{plot_width}" height="{plot_height}" fill="transparent" pointer-events="all"/>',
        ])
    parts.append("</g>")
    return "".join(parts)


def _run_walk_chart(
    samples: list[dict],
    times: list[float],
    y: int,
) -> str:
    entries = []
    for sample, time_value in zip(samples, times):
        speed = _metric(sample, ("directSpeed", "speed"))
        cadence = _metric(sample, ("directDoubleCadence", "directRunCadence", "runCadence"))
        if speed is None and cadence is None:
            continue
        if (cadence is None or cadence <= 0) and (speed is None or speed < 0.03):
            state = "Arrêt"
        elif (cadence is not None and cadence > 140) or (speed is not None and speed >= 2.235):
            state = "Course"
        else:
            state = "Marche"
        entries.append((time_value, state))

    if len(entries) < 2:
        return ""

    colors = {"Course": "#22a06b", "Marche": "#f59e0b", "Arrêt": "#94a3b8"}
    start = entries[0][0]
    end = entries[-1][0]
    if end <= start:
        return ""
    left, right = 88, WIDTH - 34
    plot_width = right - left
    bar_y, bar_height = y + 80, 74
    grouped = []
    for index, (time_value, state) in enumerate(entries):
        next_time = entries[index + 1][0] if index + 1 < len(entries) else end
        if grouped and grouped[-1][2] == state:
            grouped[-1] = (grouped[-1][0], next_time, state)
        else:
            grouped.append((time_value, next_time, state))

    parts = [
        f'<g><rect x="12" y="{y}" width="{WIDTH - 24}" height="{CARD_HEIGHT - 8}" rx="10" fill="#ffffff" stroke="#e2e8f0"/>',
        f'<text x="34" y="{y + 30}" font-size="20" font-weight="600" fill="#1e293b">Course / Marche</text>',
    ]
    legend_x = WIDTH - 300
    for offset, state in enumerate(("Course", "Marche", "Arrêt")):
        lx = legend_x + offset * 88
        parts.append(f'<rect x="{lx}" y="{y + 17}" width="10" height="10" rx="2" fill="{colors[state]}"/>')
        parts.append(f'<text x="{lx + 15}" y="{y + 27}" font-size="12" fill="#475569">{state}</text>')

    for begin, finish, state in grouped:
        x1 = left + (begin - start) / (end - start) * plot_width
        x2 = left + (finish - start) / (end - start) * plot_width
        if x2 - x1 < 1:
            x2 = x1 + 1
        parts.append(
            f'<rect x="{x1:.1f}" y="{bar_y}" width="{x2 - x1:.1f}" height="{bar_height}" fill="{colors[state]}"/>'
        )
    for fraction in (0, 0.5, 1):
        x = left + fraction * plot_width
        parts.append(
            f'<text x="{x:.1f}" y="{bar_y + bar_height + 24}" text-anchor="middle" font-size="12" fill="#64748b">{_clock(start + fraction * (end - start))}</text>'
        )
    parts.append("</g>")
    return "".join(parts)


def _zone_panel(title: str, zones: Any, unit: str, y: int) -> tuple[str, int]:
    if isinstance(zones, dict):
        zones = zones.get("zones") or zones.get("timeInZones") or []
    if not isinstance(zones, list):
        return "", 0

    rows = []
    for zone in zones:
        if not isinstance(zone, dict):
            continue
        seconds = _number(zone.get("secsInZone") or zone.get("timeInZone")) or 0
        number = _number(zone.get("zoneNumber"))
        if number is None:
            continue
        rows.append((int(number), seconds, _number(zone.get("zoneLowBoundary"))))
    rows.sort(key=lambda item: item[0])
    if not rows or sum(item[1] for item in rows) <= 0:
        return "", 0

    total = sum(item[1] for item in rows)
    height = 54 + len(rows) * 37
    parts = [
        f'<g><rect x="12" y="{y}" width="{WIDTH - 24}" height="{height}" rx="10" fill="#ffffff" stroke="#e2e8f0"/>',
        f'<text x="34" y="{y + 31}" font-size="20" font-weight="600" fill="#1e293b">{escape(title)}</text>',
    ]
    bar_x, bar_width, bar_height = 315, WIDTH - 375, 14
    for index, (number, seconds, low) in enumerate(rows):
        row_y = y + 53 + index * 37
        next_low = rows[index + 1][2] if index + 1 < len(rows) else None
        if low is not None and next_low is not None:
            label = f"Zone {number} · {low:.0f}–{max(low, next_low - 1):.0f} {unit}"
        elif low is not None:
            label = f"Zone {number} · ≥ {low:.0f} {unit}"
        else:
            label = f"Zone {number}"
        fraction = max(0.0, min(1.0, seconds / total))
        minutes = int(seconds // 60)
        seconds_remainder = int(seconds % 60)
        duration_label = f"{minutes}:{seconds_remainder:02d}"
        percent_label = f"{fraction * 100:.0f}%"
        color = ZONE_COLORS[(number - 1) % len(ZONE_COLORS)]
        parts.append(
            f'<text x="34" y="{row_y + 12}" font-size="14" fill="#334155">{escape(label)}</text>'
        )
        parts.append(
            f'<rect x="{bar_x}" y="{row_y}" width="{bar_width}" height="{bar_height}" rx="7" fill="#e5e7eb"/>'
        )
        parts.append(
            f'<rect x="{bar_x}" y="{row_y}" width="{bar_width * fraction:.1f}" height="{bar_height}" rx="7" fill="{color}"/>'
        )
        parts.append(
            f'<text x="{WIDTH - 46}" y="{row_y + 12}" text-anchor="end" font-size="13" fill="#475569">{duration_label} · {percent_label}</text>'
        )
    parts.append("</g>")
    return "".join(parts), height


def render_activity_report(
    activity_name: str,
    activity: dict,
    samples: list[dict],
    hr_zones: Any,
    power_zones: Any,
    discipline: str | None,
) -> bytes | None:
    duration = _number(activity.get("duration"))
    times = _sample_times(samples, duration)
    charts = []

    speed = _series(samples, times, ("directSpeed", "speed"), positive_only=True)
    is_running = discipline == "Course à pied"
    if speed:
        if is_running:
            pace_points = [
                (time_value, 16.6666667 / value)
                for time_value, value in speed
                if value > 0
            ]
            charts.append(("Allure", pace_points, "min/km", CHART_COLORS["speed"], True))
        else:
            speed_points = [(time_value, value * 3.6) for time_value, value in speed]
            charts.append(("Vitesse", speed_points, "km/h", CHART_COLORS["speed"], False))

    heart_rate = _series(
        samples,
        times,
        ("directHeartRate", "heartRate"),
        positive_only=True,
    )
    if heart_rate:
        charts.append(("Fréquence cardiaque", heart_rate, "bpm", CHART_COLORS["heart_rate"], False))

    cadence_keys = (
        ("directBikeCadence", "directCadence", "bikeCadence")
        if discipline == "Vélo"
        else ("directDoubleCadence", "directRunCadence", "directCadence", "directBikeCadence")
    )
    cadence = _series(samples, times, cadence_keys, positive_only=True)
    if cadence:
        cadence_title = "Cadence vélo" if discipline == "Vélo" else "Cadence de course"
        cadence_unit = "tr/min" if discipline == "Vélo" else "spm"
        charts.append((cadence_title, cadence, cadence_unit, CHART_COLORS["cadence"], False))

    report_height = 28
    chart_markup = []
    chart_data = []
    for chart_index, (title, points, unit, color, lower_is_better) in enumerate(charts):
        chart_markup.append(
            _chart(
                title,
                points,
                unit,
                color,
                report_height,
                lower_is_better=lower_is_better,
                chart_index=chart_index,
            )
        )
        values = [value for _, value in points]
        low, high = min(values), max(values)
        spread = high - low
        padding = max(spread * 0.12, abs(high) * 0.04, 1.0)
        if lower_is_better:
            top_value, bottom_value = max(0.0, low - padding), high + padding
        else:
            top_value, bottom_value = high + padding, max(0.0, low - padding)
        if bottom_value <= top_value:
            bottom_value = top_value + 1.0
        start_time = min(time_value for time_value, _ in points)
        end_time = max(time_value for time_value, _ in points)
        if end_time <= start_time:
            end_time = start_time + 1.0
        chart_data.append({
            "title": title,
            "unit": unit,
            "lower_is_better": lower_is_better,
            "points": points,
            "start_time": start_time,
            "end_time": end_time,
            "top_value": top_value,
            "bottom_value": bottom_value,
            "plot_left": 88,
            "plot_right": WIDTH - 34,
            "plot_top": report_height + 50,
            "plot_bottom": report_height + CARD_HEIGHT - 37,
        })
        report_height += CARD_HEIGHT

    if is_running:
        run_walk = _run_walk_chart(samples, times, report_height)
        if run_walk:
            chart_markup.append(run_walk)
            report_height += CARD_HEIGHT

    sections = []
    hr_markup, hr_height = _zone_panel(
        "Zones de fréquence cardiaque",
        hr_zones,
        "bpm",
        report_height,
    )
    if hr_markup:
        sections.append(hr_markup)
        report_height += hr_height + 18

    power_markup, power_height = _zone_panel(
        "Zones de puissance",
        power_zones,
        "W",
        report_height,
    )
    if power_markup:
        sections.append(power_markup)
        report_height += power_height + 18

    if not chart_markup and not sections:
        return None

    title = escape(activity_name, quote=True)
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Courbes et zones Garmin" '
        f'width="100%" height="auto" viewBox="0 0 {WIDTH} {report_height}" style="display:block;width:100%;height:auto;min-width:720px">'
        f'<rect width="{WIDTH}" height="{report_height}" fill="#f8fafc"/>'
        + "".join(chart_markup)
        + "".join(sections)
        + "</svg>"
    )
    chart_json = json.dumps(chart_data, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    page = """<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Graphiques Garmin — __TITLE__</title>
<style>
:root{color-scheme:light}
*{box-sizing:border-box}
body{margin:0;background:#f8fafc;color:#0f172a;font:16px/1.45 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
main{max-width:1180px;margin:0 auto;padding:1.5rem}
header{padding:.5rem 1rem 1rem}
.eyebrow{margin:0;color:#475569;font-size:.78rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase}
h1{margin:.35rem 0;font-size:clamp(1.5rem,4vw,2.25rem)}
.hint{margin:.3rem 0;color:#475569}
.chart-scroll{overflow-x:auto;border-radius:12px}
.chart-cursor{vector-effect:non-scaling-stroke}
.chart-dot{vector-effect:non-scaling-stroke}
.chart-hit-area{cursor:crosshair;touch-action:pan-y}
#tooltip{position:fixed;z-index:10;display:none;pointer-events:none;max-width:230px;padding:.55rem .7rem;border-radius:8px;background:#0f172a;color:#fff;box-shadow:0 4px 18px #0f172a33;font-size:.82rem}
footer{padding:1rem;color:#64748b;font-size:.8rem}
@media(max-width:640px){main{padding:.75rem}header{padding:.4rem .5rem .8rem}}
</style>
</head>
<body>
<main>
<header>
<p class="eyebrow">Bilan interactif Garmin</p>
<h1>__TITLE__</h1>
<p class="hint">Survole une courbe pour lire la valeur précise au point le plus proche. Les zones et le temps passé dans chaque zone apparaissent sous les courbes.</p>
</header>
<div class="chart-scroll">__SVG__</div>
<footer>Source : données détaillées de Garmin Connect. Les métriques ou zones indisponibles pour cette activité ne sont pas affichées.</footer>
</main>
<div id="tooltip" role="status"></div>
<script>
const series = __CHART_DATA__;
const tooltip = document.getElementById("tooltip");
function clockLabel(seconds) {
  const whole = Math.max(0, Math.round(seconds));
  const hours = Math.floor(whole / 3600);
  const minutes = Math.floor((whole % 3600) / 60);
  const remainder = whole % 60;
  return hours ? hours + ":" + String(minutes).padStart(2, "0") + ":" + String(remainder).padStart(2, "0") : minutes + ":" + String(remainder).padStart(2, "0");
}
function valueLabel(value, chart) {
  if (chart.lower_is_better) {
    const total = Math.max(0, Math.round(value * 60));
    return Math.floor(total / 60) + ":" + String(total % 60).padStart(2, "0");
  }
  return chart.unit === "bpm" ? String(Math.round(value)) : Number(value).toFixed(1);
}
document.querySelectorAll(".chart-hit-area").forEach(function(overlay) {
  const group = overlay.closest("[data-chart-index]");
  const chart = series[Number(group.dataset.chartIndex)];
  if (!chart) return;
  const svg = group.ownerSVGElement;
  const cursor = group.querySelector(".chart-cursor");
  const dot = group.querySelector(".chart-dot");
  const pointer = svg.createSVGPoint();
  overlay.addEventListener("pointermove", function(event) {
    pointer.x = event.clientX;
    pointer.y = event.clientY;
    const position = pointer.matrixTransform(svg.getScreenCTM().inverse());
    const ratio = Math.max(0, Math.min(1, (position.x - chart.plot_left) / (chart.plot_right - chart.plot_left)));
    const target = chart.start_time + ratio * (chart.end_time - chart.start_time);
    let low = 0;
    let high = chart.points.length - 1;
    while (low < high) {
      const middle = Math.floor((low + high) / 2);
      if (chart.points[middle][0] < target) low = middle + 1;
      else high = middle;
    }
    const rightIndex = low;
    const leftIndex = Math.max(0, rightIndex - 1);
    const index = Math.abs(chart.points[leftIndex][0] - target) <= Math.abs(chart.points[rightIndex][0] - target) ? leftIndex : rightIndex;
    const point = chart.points[index];
    const x = chart.plot_left + (point[0] - chart.start_time) / (chart.end_time - chart.start_time) * (chart.plot_right - chart.plot_left);
    const yRatio = (point[1] - chart.top_value) / (chart.bottom_value - chart.top_value);
    const y = chart.plot_top + yRatio * (chart.plot_bottom - chart.plot_top);
    cursor.setAttribute("x1", x);
    cursor.setAttribute("x2", x);
    cursor.setAttribute("display", "inline");
    dot.setAttribute("cx", x);
    dot.setAttribute("cy", y);
    dot.setAttribute("display", "inline");
    tooltip.textContent = clockLabel(point[0]) + " · " + chart.title + " : " + valueLabel(point[1], chart) + " " + chart.unit;
    tooltip.style.display = "block";
    tooltip.style.left = Math.min(event.clientX + 12, window.innerWidth - 245) + "px";
    tooltip.style.top = Math.min(event.clientY + 12, window.innerHeight - 65) + "px";
  });
  overlay.addEventListener("pointerleave", function() {
    cursor.setAttribute("display", "none");
    dot.setAttribute("display", "none");
    tooltip.style.display = "none";
  });
});
</script>
</body>
</html>
"""
    return (
        page.replace("__SVG__", svg)
        .replace("__CHART_DATA__", chart_json)
        .replace("__TITLE__", title)
        .encode("utf-8")
    )

def _children(notion: NotionClient, block_id: str) -> list[dict]:
    results = []
    cursor = None
    while True:
        options = {"block_id": block_id, "page_size": 100}
        if cursor:
            options["start_cursor"] = cursor
        response = notion.blocks.children.list(**options)
        results.extend(response.get("results", []))
        if not response.get("has_more"):
            return results
        cursor = response.get("next_cursor")


def _block_text(block: dict) -> str:
    payload = block.get(block.get("type", ""), {})
    rich_text = payload.get("rich_text", [])
    return "".join(part.get("plain_text", "") for part in rich_text).strip()


def find_activity_report_page(
    notion: NotionClient,
    data_source_id: str,
    day: str,
    activity_id: str,
) -> dict | None:
    next_day = (datetime.fromisoformat(day) + timedelta(days=1)).date().isoformat()
    cursor = None
    while True:
        options = {
            "data_source_id": data_source_id,
            "filter": {"and": [
                {"property": "Date de la  séance", "date": {"on_or_after": day}},
                {"property": "Date de la  séance", "date": {"before": next_day}},
            ]},
            "page_size": 100,
        }
        if cursor:
            options["start_cursor"] = cursor
        response = notion.data_sources.query(**options)
        for page in response.get("results", []):
            if page_has_activity_report(notion, page["id"], activity_id):
                return page
        if not response.get("has_more"):
            return None
        cursor = response.get("next_cursor")


def _report_container(notion: NotionClient, page_id: str) -> str:
    for block in _children(notion, page_id):
        block_type = block.get("type", "")
        payload = block.get(block_type, {})
        if _block_text(block).casefold() != REPORT_TITLE.casefold():
            continue
        if block_type == "toggle" or payload.get("is_toggleable"):
            return block["id"]

    response = notion.blocks.children.append(
        block_id=page_id,
        children=[
            {
                "type": "toggle",
                "toggle": {
                    "rich_text": [
                        {"type": "text", "text": {"content": REPORT_TITLE}}
                    ]
                },
            }
        ],
    )
    return response["results"][0]["id"]


def _report_embed_url(activity_id: str) -> str:
    return urljoin(PAGES_BASE_URL, "reports/" + quote(activity_id, safe="") + ".html")


def _legacy_activity_image(child: dict, activity_id: str) -> bool:
    if child.get("type") != "image":
        return False
    image = child.get("image", {})
    file_name = (
        image.get("file", {}).get("name")
        or image.get("external", {}).get("url", "")
    )
    caption = "".join(
        part.get("plain_text", "")
        for part in image.get("caption", [])
    ).casefold()
    return (
        f"garmin-{activity_id}-bilan" in file_name
        or f"activité garmin : {activity_id}" in caption
    )


def page_has_interactive_report(
    notion: NotionClient,
    page_id: str,
    activity_id: str,
) -> bool:
    expected_url = _report_embed_url(activity_id)
    for block in _children(notion, page_id):
        block_type = block.get("type", "")
        payload = block.get(block_type, {})
        if _block_text(block).casefold() != REPORT_TITLE.casefold():
            continue
        if block_type != "toggle" and not payload.get("is_toggleable"):
            continue
        for child in _children(notion, block["id"]):
            if child.get("type") == "embed" and child.get("embed", {}).get("url") == expected_url:
                return True
    return False


def page_has_activity_report(
    notion: NotionClient,
    page_id: str,
    activity_id: str,
) -> bool:
    expected_url = _report_embed_url(activity_id)
    for block in _children(notion, page_id):
        block_type = block.get("type", "")
        payload = block.get(block_type, {})
        if _block_text(block).casefold() != REPORT_TITLE.casefold():
            continue
        if block_type != "toggle" and not payload.get("is_toggleable"):
            continue
        for child in _children(notion, block["id"]):
            if (
                child.get("type") == "embed"
                and child.get("embed", {}).get("url") == expected_url
            ) or _legacy_activity_image(child, activity_id):
                return True
    return False

def add_or_update_activity_report(
    notion: NotionClient,
    page_id: str,
    activity_id: str,
    html: bytes,
    activity_name: str = "Séance Garmin",
) -> None:
    if page_has_interactive_report(notion, page_id, activity_id):
        return

    report_url = _save_activity_report(activity_id, activity_name, html)
    container_id = _report_container(notion, page_id)
    for child in _children(notion, container_id):
        if _legacy_activity_image(child, activity_id):
            notion.blocks.delete(block_id=child["id"])

    notion.blocks.children.append(
        block_id=container_id,
        children=[
            {
                "type": "embed",
                "embed": {"url": report_url},
            }
        ],
    )
