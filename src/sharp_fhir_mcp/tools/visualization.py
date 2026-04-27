"""MCP-UI visualisation tools — return HTML resources for the host to render.

Each tool returns a dict with a ``content`` array containing an MCP-UI
``ui://`` resource. The host renders the resource (Chart.js charts, full
clinical dashboard) inside its sidebar / inspector pane.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastmcp import FastMCP
from mcp_ui import CreateUIResourceOptions, RawHtmlContent, create_ui_resource

from sharp_fhir_mcp.clients.fhir_client import FHIRError
from sharp_fhir_mcp.context import fhir_client_for_current_context
from sharp_fhir_mcp.fhir_utils import (
    allergy_summary,
    bundle_to_resources,
    condition_summary,
    encounter_summary,
    immunization_summary,
    medication_request_summary,
    observation_summary,
    patient_summary,
)
from sharp_fhir_mcp.tools._helpers import check_fhir_context, resolve_patient_id
from sharp_fhir_mcp.ui import ClinicalChartBuilder, ClinicalDisplayBuilder


def _ui_resource(uri: str, html: str) -> dict[str, Any]:
    """Wrap raw HTML in an MCP-UI resource payload."""
    return create_ui_resource(
        CreateUIResourceOptions(
            uri=uri,
            content=RawHtmlContent(type="rawHtml", htmlString=html),
            encoding="text",
        )
    )


def register_visualization_tools(mcp: FastMCP) -> None:
    """Register MCP-UI rendering tools (lab trend chart, full dashboard)."""

    # ====
    # Single-test lab trend chart
    # ====

    @mcp.tool
    async def visualize_lab_trend(
        loinc_or_test: str,
        patient_id: str | None = None,
        date_from: str | None = None,
        normal_low: float | None = None,
        normal_high: float | None = None,
    ) -> dict:
        """Render an interactive Chart.js trend chart for a single lab test.

        Args:
            loinc_or_test: LOINC code or partial test name (used as ``code``
                in the FHIR Observation search).
            patient_id: FHIR Patient id (defaults to X-Patient-ID header).
            date_from: Earliest date to include (e.g. ``2024-01-01``).
            normal_low: Optional reference-range lower bound.
            normal_high: Optional reference-range upper bound.

        Returns:
            An MCP-UI ``ui://`` resource containing the rendered chart.
        """
        if (err := check_fhir_context(require_patient=True, patient_id=patient_id)) is not None:
            return err
        pid = resolve_patient_id(patient_id) or ""

        try:
            async with fhir_client_for_current_context() as fhir:
                bundle = await fhir.get_observations(
                    pid,
                    code=loinc_or_test,
                    date=f"ge{date_from}" if date_from else None,
                    count=200,
                    sort="date",
                )
        except FHIRError as e:
            return e.to_tool_response()

        observations = [observation_summary(o) for o in bundle_to_resources(bundle)]
        # The chart wants {date, value, unit}.
        series = [
            {
                "date": (o.get("date") or "")[:10],
                "value": o.get("value"),
                "unit": o.get("unit") or "",
            }
            for o in observations
            if isinstance(o.get("value"), (int, float))
        ]

        normal_range: tuple[float, float] | None = None
        if normal_low is not None and normal_high is not None:
            normal_range = (float(normal_low), float(normal_high))

        if not series:
            html = (
                f'<div style="padding:1rem;color:#64748b;font-family:sans-serif;">'
                f"No numeric observations found for <code>{loinc_or_test}</code>."
                f"</div>"
            )
        else:
            test_label = observations[0].get("test") or loinc_or_test
            html = ClinicalChartBuilder.build_lab_trend_chart(
                test_label, series, normal_range
            )

        uri = f"ui://sharp-fhir-mcp/lab-trend/{pid}/{int(time.time())}"
        return {
            "content": [_ui_resource(uri, html)],
            "patient_id": pid,
            "test": loinc_or_test,
            "data_points": len(series),
        }

    # ====
    # Vitals dashboard
    # ====

    @mcp.tool
    async def visualize_vitals(
        patient_id: str | None = None,
        date_from: str | None = None,
    ) -> dict:
        """Render an interactive dashboard of the patient's vital signs.

        Args:
            patient_id: FHIR Patient id (defaults to X-Patient-ID header).
            date_from: Earliest date to include.
        """
        if (err := check_fhir_context(require_patient=True, patient_id=patient_id)) is not None:
            return err
        pid = resolve_patient_id(patient_id) or ""

        try:
            async with fhir_client_for_current_context() as fhir:
                bundle = await fhir.get_observations(
                    pid,
                    category="vital-signs",
                    date=f"ge{date_from}" if date_from else None,
                    count=200,
                    sort="date",
                )
        except FHIRError as e:
            return e.to_tool_response()

        vitals = [observation_summary(o) for o in bundle_to_resources(bundle)]
        html = ClinicalChartBuilder.build_vitals_dashboard(vitals)

        uri = f"ui://sharp-fhir-mcp/vitals/{pid}/{int(time.time())}"
        return {
            "content": [_ui_resource(uri, html)],
            "patient_id": pid,
            "data_points": len(vitals),
        }

    # ====
    # Full patient dashboard (HTML + charts)
    # ====

    @mcp.tool
    async def visualize_patient_dashboard(
        patient_id: str | None = None,
        include_charts: bool = True,
        lab_lookback_days: int = 90,
    ) -> dict:
        """Render the complete patient clinical dashboard as an MCP-UI page.

        Combines demographics, allergies, medications, problems, recent labs,
        encounters, and (optionally) trend charts into one full-page HTML
        resource that MCP-UI hosts can display in their sidebar.

        Args:
            patient_id: FHIR Patient id (defaults to X-Patient-ID header).
            include_charts: Whether to embed Chart.js trend charts.
            lab_lookback_days: Window for lab observations.
        """
        if (err := check_fhir_context(require_patient=True, patient_id=patient_id)) is not None:
            return err
        pid = resolve_patient_id(patient_id) or ""

        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        lab_since = (now - timedelta(days=lab_lookback_days)).strftime("%Y-%m-%d")

        try:
            async with fhir_client_for_current_context() as fhir:
                results = await asyncio.gather(
                    fhir.get_patient(pid),
                    fhir.get_allergies(pid, count=100),
                    fhir.get_medication_requests(pid, status="active", count=100),
                    fhir.get_conditions(pid, clinical_status="active", count=100),
                    fhir.get_immunizations(pid, count=100),
                    fhir.get_observations(
                        pid,
                        category="laboratory",
                        date=f"ge{lab_since}",
                        count=100,
                        sort="-date",
                    ),
                    fhir.get_encounters(pid, count=10),
                    return_exceptions=True,
                )
        except FHIRError as e:
            return e.to_tool_response()

        def _ok(resp: Any) -> list[dict]:
            return bundle_to_resources(resp) if not isinstance(resp, Exception) else []

        (
            patient_resp,
            allergies_b,
            meds_b,
            problems_b,
            imms_b,
            labs_b,
            encounters_b,
        ) = results
        patient = patient_resp if not isinstance(patient_resp, Exception) else {}

        demographics = patient_summary(patient) if patient else {}
        allergies = [allergy_summary(r) for r in _ok(allergies_b)]
        meds = [medication_request_summary(r) for r in _ok(meds_b)]
        problems = [condition_summary(r) for r in _ok(problems_b)]
        imms = [immunization_summary(r) for r in _ok(imms_b)]
        labs = [observation_summary(r) for r in _ok(labs_b)]
        encounters = [encounter_summary(r) for r in _ok(encounters_b)]

        # Derived alerts (mirrors clinical_get_context).
        alerts: list[dict[str, Any]] = []
        if allergies:
            alerts.append(
                {
                    "type": "allergy_warning",
                    "severity": "high",
                    "message": f"Patient has {len(allergies)} documented allergies",
                    "details": [a.get("allergen") for a in allergies if a.get("allergen")],
                }
            )
        abnormal = [l for l in labs if l.get("abnormal")]
        if abnormal:
            alerts.append(
                {
                    "type": "abnormal_labs",
                    "severity": "medium",
                    "message": f"{len(abnormal)} abnormal lab results in look-back window",
                    "details": [
                        f"{l.get('test')}: {l.get('value')} {l.get('unit') or ''}".strip()
                        for l in abnormal[:5]
                    ],
                }
            )
        if len(meds) >= 10:
            alerts.append(
                {
                    "type": "polypharmacy",
                    "severity": "medium",
                    "message": (
                        f"Patient on {len(meds)} active medications — "
                        "review for interactions"
                    ),
                }
            )

        context = {
            "retrieved_at": now.isoformat(),
            "patient_id": pid,
            "demographics": demographics,
            "allergies": allergies,
            "active_medications": meds,
            "active_problems": problems,
            "immunizations": imms,
            "recent_labs": labs,
            "recent_encounters": encounters,
            "alerts": alerts,
        }

        # Base sections from the display builder.
        body = ClinicalDisplayBuilder.build_clinical_context_display(context)

        # Append optional Chart.js trends.
        if include_charts:
            chart_blocks: list[str] = []

            # Group labs by test for trend lines (only series with ≥2 numeric points).
            lab_groups: dict[str, list[dict[str, Any]]] = {}
            for lab in labs:
                if not isinstance(lab.get("value"), (int, float)):
                    continue
                lab_groups.setdefault(lab.get("test") or "Unknown", []).append(
                    {
                        "date": (lab.get("date") or "")[:10],
                        "value": lab.get("value"),
                        "unit": lab.get("unit") or "",
                    }
                )
            for test_name, series in list(lab_groups.items())[:3]:
                if len(series) >= 2:
                    chart_blocks.append(
                        ClinicalChartBuilder.build_lab_trend_chart(test_name, series)
                    )

            if encounters:
                chart_blocks.append(
                    ClinicalChartBuilder.build_visit_frequency_chart(
                        [
                            {"date": e.get("start") or "", "reason": e.get("reason")}
                            for e in encounters
                        ]
                    )
                )

            if problems:
                chart_blocks.append(
                    ClinicalChartBuilder.build_problem_distribution_chart(problems)
                )

            if meds:
                chart_blocks.append(
                    ClinicalChartBuilder.build_medication_timeline(meds)
                )

            chart_blocks = [c for c in chart_blocks if c]
            if chart_blocks:
                charts_html = "".join(
                    f'<div style="margin-bottom: 1rem;">{c}</div>' for c in chart_blocks
                )
                body += f"""
                <div style="background:#fff;border:1px solid #e2e8f0;border-radius:12px;
                            padding:1rem;margin-bottom:1rem;">
                    <h3 style="margin:0 0 1rem 0;color:#1e293b;">📈 Clinical Trends</h3>
                    {charts_html}
                </div>
                """

        uri = f"ui://sharp-fhir-mcp/dashboard/{pid}/{int(time.time())}"
        return {
            "content": [_ui_resource(uri, body)],
            "patient_id": pid,
            "patient_name": demographics.get("name"),
            "alerts_count": len(alerts),
            "data_summary": {
                "allergies": len(allergies),
                "medications": len(meds),
                "problems": len(problems),
                "immunizations": len(imms),
                "labs": len(labs),
                "encounters": len(encounters),
            },
        }

    _: Any = (
        visualize_lab_trend,
        visualize_vitals,
        visualize_patient_dashboard,
    )


__all__ = ["register_visualization_tools"]
