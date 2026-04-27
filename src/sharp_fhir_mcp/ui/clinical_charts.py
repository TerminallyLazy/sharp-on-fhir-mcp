"""Clinical Chart.js visualisation builders.

These helpers turn normalised summary dicts (as produced by
:mod:`sharp_fhir_mcp.fhir_utils`) into self-contained HTML snippets that
embed Chart.js. The MCP-UI host renders them as interactive widgets.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from typing import Any


class ClinicalChartBuilder:
    """Build Chart.js HTML snippets for clinical data."""

    # Clinical-friendly colour palette
    CHART_COLORS: dict[str, str] = {
        "primary": "rgb(37, 99, 235)",
        "primary_bg": "rgba(37, 99, 235, 0.1)",
        "success": "rgb(16, 185, 129)",
        "success_bg": "rgba(16, 185, 129, 0.1)",
        "warning": "rgb(245, 158, 11)",
        "warning_bg": "rgba(245, 158, 11, 0.1)",
        "danger": "rgb(220, 38, 38)",
        "danger_bg": "rgba(220, 38, 38, 0.1)",
        "purple": "rgb(139, 92, 246)",
        "purple_bg": "rgba(139, 92, 246, 0.1)",
        "gray": "rgb(107, 114, 128)",
        "gray_bg": "rgba(107, 114, 128, 0.1)",
    }

    # ====
    # Lab trends
    # ====

    @classmethod
    def build_lab_trend_chart(
        cls,
        test_name: str,
        values: list[dict[str, Any]],
        normal_range: tuple[float, float] | None = None,
    ) -> str:
        """Build a line chart for a single lab test.

        Args:
            test_name: Lab name (chart title).
            values: List of ``{"date": "YYYY-MM-DD", "value": float, "unit": "..."}``.
            normal_range: Optional ``(low, high)`` reference band.
        """
        if not values:
            return '<p style="color: #64748b; text-align: center;">No data available</p>'

        labels: list[str] = []
        data_points: list[float] = []
        for v in values:
            try:
                labels.append((v.get("date") or "")[:10])
                data_points.append(float(v.get("value") or 0))
            except (TypeError, ValueError):
                continue

        abnormal_points: list[bool] = [False] * len(data_points)
        if normal_range:
            low, high = normal_range
            abnormal_points = [val < low or val > high for val in data_points]

        point_colors = [
            cls.CHART_COLORS["danger"] if abn else cls.CHART_COLORS["primary"]
            for abn in abnormal_points
        ]

        chart_id = f"lab_chart_{abs(hash(test_name)) % 100000}"

        datasets = [
            {
                "label": test_name,
                "data": data_points,
                "borderColor": cls.CHART_COLORS["primary"],
                "backgroundColor": cls.CHART_COLORS["primary_bg"],
                "pointBackgroundColor": point_colors,
                "pointBorderColor": point_colors,
                "pointRadius": 6,
                "fill": True,
                "tension": 0.3,
            }
        ]

        annotations: dict[str, Any] = {}
        if normal_range:
            annotations = {
                "normalRange": {
                    "type": "box",
                    "yMin": normal_range[0],
                    "yMax": normal_range[1],
                    "backgroundColor": "rgba(16, 185, 129, 0.1)",
                    "borderColor": "rgba(16, 185, 129, 0.3)",
                    "borderWidth": 1,
                    "label": {
                        "content": "Normal Range",
                        "enabled": True,
                        "position": "end",
                    },
                }
            }

        unit = (values[0].get("unit") or "") if values else ""
        return cls._build_chart_html(
            chart_id=chart_id,
            chart_type="line",
            labels=labels,
            datasets=datasets,
            title=f"{test_name} Trend",
            y_axis_label=unit,
            annotations=annotations,
        )

    # ====
    # Vital signs dashboard
    # ====

    @classmethod
    def build_vitals_dashboard(cls, vitals: list[dict[str, Any]]) -> str:
        """Render a multi-chart dashboard from a list of vital observations.

        Each input dict should follow the :func:`fhir_utils.observation_summary`
        shape (``test``, ``value``, ``unit``, ``date``). The chart is grouped
        by ``test`` name.
        """
        if not vitals:
            return '<p style="color: #64748b; text-align: center;">No vitals data</p>'

        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for v in vitals:
            test = (v.get("test") or "Unknown").strip()
            groups[test].append(
                {
                    "date": (v.get("date") or "")[:10],
                    "value": v.get("value"),
                    "unit": v.get("unit") or "",
                }
            )

        # Standard reference ranges for common vital signs.
        normal_ranges: dict[str, tuple[float, float]] = {
            "Heart rate": (60.0, 100.0),
            "Pulse rate": (60.0, 100.0),
            "Body temperature": (36.5, 37.5),
            "Oxygen saturation": (95.0, 100.0),
            "Respiratory rate": (12.0, 20.0),
        }

        charts: list[str] = []
        for test_name, series in sorted(groups.items()):
            # Filter to numeric series only.
            series = [s for s in series if isinstance(s.get("value"), (int, float))]
            if len(series) < 1:
                continue
            charts.append(
                cls.build_lab_trend_chart(
                    test_name,
                    series,
                    normal_ranges.get(test_name),
                )
            )

        if not charts:
            return '<p style="color: #64748b; text-align: center;">No numeric vitals data</p>'

        chart_items = "".join(
            f'<div class="chart-container">{chart}</div>' for chart in charts
        )
        return f"""
        <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(400px, 1fr));
                    gap: 1rem;">
            {chart_items}
        </div>
        """

    # ====
    # Visit frequency
    # ====

    @classmethod
    def build_visit_frequency_chart(cls, visits: list[dict[str, Any]]) -> str:
        """Build a monthly visit-frequency bar chart.

        Args:
            visits: List of dicts with at least a ``date`` or ``start`` key.
        """
        if not visits:
            return '<p style="color: #64748b; text-align: center;">No visits</p>'

        monthly_counts: dict[str, int] = defaultdict(int)
        for v in visits:
            date = v.get("date") or v.get("start") or v.get("scheduled_time") or ""
            if date:
                month_key = date[:7]  # YYYY-MM
                monthly_counts[month_key] += 1

        sorted_months = sorted(monthly_counts.keys())
        if not sorted_months:
            return '<p style="color: #64748b; text-align: center;">No dated visits</p>'

        labels = sorted_months
        data = [monthly_counts[m] for m in sorted_months]

        chart_id = f"visits_chart_{abs(hash(tuple(sorted_months))) % 100000}"
        datasets = [
            {
                "label": "Visits",
                "data": data,
                "backgroundColor": cls.CHART_COLORS["primary_bg"],
                "borderColor": cls.CHART_COLORS["primary"],
                "borderWidth": 2,
                "borderRadius": 4,
            }
        ]
        return cls._build_chart_html(
            chart_id=chart_id,
            chart_type="bar",
            labels=labels,
            datasets=datasets,
            title="Visit Frequency",
            y_axis_label="Number of Visits",
        )

    # ====
    # Problem distribution doughnut
    # ====

    @classmethod
    def build_problem_distribution_chart(
        cls, problems: list[dict[str, Any]]
    ) -> str:
        """Build a doughnut chart showing the distribution of problem categories."""
        if not problems:
            return ""

        categories = {
            "Cardiovascular": 0,
            "Endocrine": 0,
            "Respiratory": 0,
            "Musculoskeletal": 0,
            "Mental Health": 0,
            "Other": 0,
        }

        cardio_terms = ["heart", "hypertension", "cardio", "blood pressure", "cholesterol"]
        endo_terms = ["diabetes", "thyroid", "obesity", "metabolic"]
        resp_terms = ["asthma", "copd", "respiratory", "lung", "breathing"]
        msk_terms = ["arthritis", "pain", "back", "joint", "osteo"]
        mh_terms = ["anxiety", "depression", "mental", "psychiatric", "bipolar"]

        for p in problems:
            name = (p.get("name") or "").lower()
            if any(t in name for t in cardio_terms):
                categories["Cardiovascular"] += 1
            elif any(t in name for t in endo_terms):
                categories["Endocrine"] += 1
            elif any(t in name for t in resp_terms):
                categories["Respiratory"] += 1
            elif any(t in name for t in msk_terms):
                categories["Musculoskeletal"] += 1
            elif any(t in name for t in mh_terms):
                categories["Mental Health"] += 1
            else:
                categories["Other"] += 1

        labels = [k for k, v in categories.items() if v > 0]
        data = [v for v in categories.values() if v > 0]
        if not data:
            return ""

        chart_id = f"problems_chart_{abs(hash(tuple(labels))) % 100000}"
        colors = [
            cls.CHART_COLORS["danger"],
            cls.CHART_COLORS["warning"],
            cls.CHART_COLORS["primary"],
            cls.CHART_COLORS["purple"],
            cls.CHART_COLORS["success"],
            cls.CHART_COLORS["gray"],
        ]
        datasets = [
            {
                "data": data,
                "backgroundColor": colors[: len(data)],
                "borderWidth": 2,
                "borderColor": "#ffffff",
            }
        ]
        return cls._build_chart_html(
            chart_id=chart_id,
            chart_type="doughnut",
            labels=labels,
            datasets=datasets,
            title="Problem Categories",
            options_override={
                "plugins": {
                    "legend": {"position": "right"},
                },
            },
        )

    # ====
    # Medication timeline (horizontal bar chart by duration on med)
    # ====

    @classmethod
    def build_medication_timeline(cls, medications: list[dict[str, Any]]) -> str:
        """Render a horizontal bar chart of duration on each active medication.

        Args:
            medications: List of dicts with ``name`` and either ``authored_on``
                / ``start_date`` keys.
        """
        if not medications:
            return '<p style="color: #64748b; text-align: center;">No medications</p>'

        chart_id = f"med_timeline_{abs(hash(tuple(m.get('name', '') for m in medications))) % 100000}"

        labels: list[str] = []
        durations: list[int] = []
        now = datetime.now()
        for med in medications[:15]:
            labels.append((med.get("name") or "Unknown")[:35])
            start = med.get("authored_on") or med.get("start_date") or ""
            try:
                start_dt = datetime.strptime(start[:10], "%Y-%m-%d") if start else now
            except ValueError:
                start_dt = now
            durations.append(max((now - start_dt).days, 30))

        colors = [
            cls.CHART_COLORS["primary"],
            cls.CHART_COLORS["success"],
            cls.CHART_COLORS["purple"],
            cls.CHART_COLORS["warning"],
            cls.CHART_COLORS["gray"],
        ]
        bg_colors = (colors * ((len(durations) // len(colors)) + 1))[: len(durations)]

        datasets = [
            {
                "label": "Days on medication",
                "data": durations,
                "backgroundColor": bg_colors,
                "borderRadius": 4,
            }
        ]
        return cls._build_chart_html(
            chart_id=chart_id,
            chart_type="bar",
            labels=labels,
            datasets=datasets,
            title="Active Medications",
            x_axis_label="Days on medication",
            options_override={
                "indexAxis": "y",
                "scales": {"x": {"title": {"display": True, "text": "Days"}}},
            },
        )

    # ====
    # Internals
    # ====

    @classmethod
    def _build_chart_html(
        cls,
        *,
        chart_id: str,
        chart_type: str,
        labels: list[Any],
        datasets: list[dict[str, Any]],
        title: str = "",
        y_axis_label: str = "",
        x_axis_label: str = "",
        annotations: dict[str, Any] | None = None,
        options_override: dict[str, Any] | None = None,
    ) -> str:
        options: dict[str, Any] = {
            "responsive": True,
            "maintainAspectRatio": False,
            "plugins": {
                "title": {
                    "display": bool(title),
                    "text": title,
                    "font": {"size": 14, "weight": "bold"},
                },
                "legend": {"display": len(datasets) > 1},
            },
            "scales": {},
        }

        if chart_type in ("line", "bar"):
            options["scales"]["y"] = {
                "beginAtZero": False,
                "title": {"display": bool(y_axis_label), "text": y_axis_label},
            }
            options["scales"]["x"] = {
                "title": {"display": bool(x_axis_label), "text": x_axis_label},
            }

        if annotations:
            options["plugins"]["annotation"] = {"annotations": annotations}

        if options_override:
            cls._deep_merge(options, options_override)

        config = {
            "type": chart_type,
            "data": {"labels": labels, "datasets": datasets},
            "options": options,
        }
        config_json = json.dumps(config)

        return f"""
        <div style="height: 300px; position: relative;">
            <canvas id="{chart_id}"></canvas>
        </div>
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-annotation"></script>
        <script>
            (function() {{
                const ctx = document.getElementById('{chart_id}');
                if (ctx) {{
                    new Chart(ctx, {config_json});
                }}
            }})();
        </script>
        """

    @staticmethod
    def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        for key, value in override.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                ClinicalChartBuilder._deep_merge(base[key], value)
            else:
                base[key] = value
        return base


__all__ = ["ClinicalChartBuilder"]
