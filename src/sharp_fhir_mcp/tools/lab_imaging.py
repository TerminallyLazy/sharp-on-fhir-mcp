"""Lab results, vitals, diagnostic reports & imaging documents.

Wraps FHIR ``Observation``, ``DiagnosticReport`` and ``DocumentReference``.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from sharp_fhir_mcp.clients.fhir_client import FHIRError
from sharp_fhir_mcp.context import fhir_client_for_current_context
from sharp_fhir_mcp.fhir_utils import (
    bundle_next_link,
    bundle_to_resources,
    bundle_total,
    diagnostic_report_summary,
    document_reference_summary,
    observation_summary,
)
from sharp_fhir_mcp.tools._helpers import check_fhir_context, resolve_patient_id


def register_lab_imaging_tools(mcp: FastMCP) -> None:
    """Register laboratory, vitals, diagnostic-report and imaging tools."""

    # ====
    # Lab results
    # ====

    @mcp.tool()
    async def lab_get_results(
        patient_id: str | None = None,
        code: str | None = None,
        date: str | None = None,
        count: int = 50,
        abnormal_only: bool = False,
    ) -> dict:
        """Return laboratory results (FHIR ``Observation`` ``category=laboratory``).

        Args:
            patient_id: FHIR Patient id (defaults to X-Patient-ID header).
            code: Optional LOINC or other code filter (e.g. ``http://loinc.org|2339-0``
                or just ``2339-0``).
            date: FHIR date filter (e.g. ``ge2024-01-01``).
            count: Max results (1–250).
            abnormal_only: If true, post-filters results to abnormal interpretations.
        """
        if (err := check_fhir_context(require_patient=True, patient_id=patient_id)) is not None:
            return err
        pid = resolve_patient_id(patient_id) or ""

        try:
            async with fhir_client_for_current_context() as fhir:
                bundle = await fhir.get_observations(
                    pid,
                    category="laboratory",
                    code=code,
                    date=date,
                    count=min(max(count, 1), 250),
                )
        except FHIRError as e:
            return e.to_tool_response()

        labs = [observation_summary(o) for o in bundle_to_resources(bundle)]
        if abnormal_only:
            labs = [l for l in labs if l.get("abnormal")]

        return {
            "labs": labs,
            "total_count": bundle_total(bundle),
            "returned": len(labs),
            "has_more": bundle_next_link(bundle) is not None,
            "abnormal_count": sum(1 for l in labs if l.get("abnormal")),
        }

    # ====
    # Vital signs
    # ====

    @mcp.tool()
    async def lab_get_vital_signs(
        patient_id: str | None = None,
        date: str | None = None,
        count: int = 100,
    ) -> dict:
        """Return vital sign observations (``category=vital-signs``).

        Args:
            patient_id: FHIR Patient id (defaults to X-Patient-ID header).
            date: FHIR date filter (e.g. ``ge2024-01-01``).
            count: Max results (1–250). Larger values let you build trend charts.
        """
        if (err := check_fhir_context(require_patient=True, patient_id=patient_id)) is not None:
            return err
        pid = resolve_patient_id(patient_id) or ""

        try:
            async with fhir_client_for_current_context() as fhir:
                bundle = await fhir.get_observations(
                    pid,
                    category="vital-signs",
                    date=date,
                    count=min(max(count, 1), 250),
                )
        except FHIRError as e:
            return e.to_tool_response()

        vitals = [observation_summary(o) for o in bundle_to_resources(bundle)]
        # Group vitals by test name for easier consumption by chart tools.
        grouped: dict[str, list[dict[str, Any]]] = {}
        for v in vitals:
            grouped.setdefault(v.get("test") or "Unknown", []).append(v)

        return {
            "vitals": vitals,
            "by_type": grouped,
            "types": list(grouped.keys()),
            "total_count": bundle_total(bundle),
            "returned": len(vitals),
            "has_more": bundle_next_link(bundle) is not None,
        }

    # ====
    # Diagnostic reports
    # ====

    @mcp.tool()
    async def lab_get_diagnostic_reports(
        patient_id: str | None = None,
        category: str | None = None,
        date: str | None = None,
        count: int = 25,
    ) -> dict:
        """Return ``DiagnosticReport`` resources for the patient.

        Args:
            patient_id: FHIR Patient id (defaults to X-Patient-ID header).
            category: Report category (``LAB``, ``RAD``, ``PAT``, ``CT``, ``CG``...).
            date: FHIR date filter.
            count: Max results (1–250).
        """
        if (err := check_fhir_context(require_patient=True, patient_id=patient_id)) is not None:
            return err
        pid = resolve_patient_id(patient_id) or ""

        try:
            async with fhir_client_for_current_context() as fhir:
                bundle = await fhir.get_diagnostic_reports(
                    pid,
                    category=category,
                    date=date,
                    count=min(max(count, 1), 250),
                )
        except FHIRError as e:
            return e.to_tool_response()

        reports = [diagnostic_report_summary(r) for r in bundle_to_resources(bundle)]
        return {
            "reports": reports,
            "total_count": bundle_total(bundle),
            "returned": len(reports),
            "has_more": bundle_next_link(bundle) is not None,
        }

    # ====
    # Imaging / documents
    # ====

    @mcp.tool()
    async def imaging_get_documents(
        patient_id: str | None = None,
        category: str | None = None,
        type_code: str | None = None,
        count: int = 25,
    ) -> dict:
        """Return ``DocumentReference`` resources (clinical notes, imaging, scans).

        Args:
            patient_id: FHIR Patient id (defaults to X-Patient-ID header).
            category: DocumentReference category (e.g. ``imaging``, ``clinical-note``).
            type_code: Document type code (e.g. ``18748-4`` for diagnostic imaging).
            count: Max results (1–250).
        """
        if (err := check_fhir_context(require_patient=True, patient_id=patient_id)) is not None:
            return err
        pid = resolve_patient_id(patient_id) or ""

        try:
            async with fhir_client_for_current_context() as fhir:
                bundle = await fhir.get_document_references(
                    pid,
                    category=category,
                    type_=type_code,
                    count=min(max(count, 1), 250),
                )
        except FHIRError as e:
            return e.to_tool_response()

        docs = [document_reference_summary(d) for d in bundle_to_resources(bundle)]
        return {
            "documents": docs,
            "total_count": bundle_total(bundle),
            "returned": len(docs),
            "has_more": bundle_next_link(bundle) is not None,
        }

    _: Any = (
        lab_get_results,
        lab_get_vital_signs,
        lab_get_diagnostic_reports,
        imaging_get_documents,
    )


__all__ = ["register_lab_imaging_tools"]
