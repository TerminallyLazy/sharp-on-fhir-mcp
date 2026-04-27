"""Optional clinical memory tools backed by SimpleMem.

These tools are registered only when ``SIMPLEMEM_API_URL`` and
``SIMPLEMEM_ACCESS_TOKEN`` are configured. They give the agent persistent,
cross-session memory of past encounters, alerts and notes.

NOTE: All identifiers here are FHIR resource ids (strings), per the SHARP
context model.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from sharp_fhir_mcp.clients.fhir_client import FHIRError
from sharp_fhir_mcp.clients.simplemem_client import SimpleMemClient
from sharp_fhir_mcp.context import fhir_client_for_current_context
from sharp_fhir_mcp.fhir_utils import patient_display_name
from sharp_fhir_mcp.tools._helpers import check_fhir_context, resolve_patient_id


def register_memory_tools(
    mcp: FastMCP,
    memory_client: SimpleMemClient | None,
) -> None:
    """Register clinical-memory tools.

    If ``memory_client`` is ``None`` (i.e. SimpleMem isn't configured), no
    tools are registered. Callers should still invoke this function so the
    server can decide whether to advertise these tools at startup.
    """
    if memory_client is None or not memory_client.is_configured:
        return

    @mcp.tool()
    async def memory_store_encounter(
        encounter_summary: str,
        visit_date: str,
        chief_complaint: str | None = None,
        diagnosis: str | None = None,
        plan: str | None = None,
        patient_id: str | None = None,
    ) -> dict:
        """Store a clinical encounter summary for cross-session recall.

        Use this at the END of a patient visit to persist key clinical
        information for future encounters. Memories are tagged with the
        FHIR patient id so they can be retrieved later.

        Args:
            encounter_summary: Brief narrative summary of the encounter.
            visit_date: Visit date in ``YYYY-MM-DD`` format.
            chief_complaint: Optional chief complaint.
            diagnosis: Optional comma-separated diagnoses.
            plan: Optional plan/treatment narrative.
            patient_id: FHIR Patient id (defaults to X-Patient-ID header).
        """
        if (err := check_fhir_context(require_patient=True, patient_id=patient_id)) is not None:
            return err
        pid = resolve_patient_id(patient_id) or ""

        # Best-effort look up the patient name to enrich the memory.
        patient_name = ""
        try:
            async with fhir_client_for_current_context() as fhir:
                patient = await fhir.get_patient(pid)
                patient_name = patient_display_name(patient)
        except FHIRError:
            patient_name = ""

        diagnosis_list = [d.strip() for d in diagnosis.split(",")] if diagnosis else None

        try:
            result = await memory_client.store_patient_encounter(
                patient_id=pid,
                patient_name=patient_name or pid,
                encounter_summary=encounter_summary,
                visit_date=visit_date,
                chief_complaint=chief_complaint,
                diagnosis=diagnosis_list,
                plan=plan,
            )
        except Exception as e:  # noqa: BLE001 — surface upstream error
            return {"success": False, "error": str(e)}

        return {
            "success": True,
            "patient_id": pid,
            "patient_name": patient_name,
            "visit_date": visit_date,
            "stored": True,
            "result": result,
        }

    @mcp.tool()
    async def memory_store_alert(
        alert_type: str,
        alert_content: str,
        severity: str = "warning",
        patient_id: str | None = None,
    ) -> dict:
        """Store a persistent clinical alert/flag for the patient.

        Args:
            alert_type: One of ``allergy``, ``drug_interaction``, ``lab_critical``,
                ``patient_preference``, ``behavioral``, ``follow_up``, ``other``.
            alert_content: Detailed description of the alert.
            severity: ``info``, ``warning``, or ``critical``.
            patient_id: FHIR Patient id (defaults to X-Patient-ID header).
        """
        if (err := check_fhir_context(require_patient=True, patient_id=patient_id)) is not None:
            return err
        pid = resolve_patient_id(patient_id) or ""

        if severity not in {"info", "warning", "critical"}:
            severity = "warning"

        patient_name = ""
        try:
            async with fhir_client_for_current_context() as fhir:
                patient = await fhir.get_patient(pid)
                patient_name = patient_display_name(patient)
        except FHIRError:
            patient_name = ""

        try:
            result = await memory_client.store_clinical_alert(
                patient_id=pid,
                patient_name=patient_name or pid,
                alert_type=alert_type,
                alert_content=alert_content,
                severity=severity,
            )
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": str(e)}

        return {
            "success": True,
            "patient_id": pid,
            "alert_type": alert_type,
            "severity": severity,
            "result": result,
        }

    @mcp.tool()
    async def memory_search_history(
        query: str,
        limit: int = 10,
        patient_id: str | None = None,
    ) -> dict:
        """Search the patient's clinical history in memory.

        Args:
            query: Natural-language search query
                (e.g. "previous cardiac workup", "diabetes management").
            limit: Max results to return (1–50).
            patient_id: FHIR Patient id (defaults to X-Patient-ID header).
        """
        if (err := check_fhir_context(require_patient=True, patient_id=patient_id)) is not None:
            return err
        pid = resolve_patient_id(patient_id) or ""

        patient_name = ""
        try:
            async with fhir_client_for_current_context() as fhir:
                patient = await fhir.get_patient(pid)
                patient_name = patient_display_name(patient)
        except FHIRError:
            patient_name = ""

        full_query = f"patient_id:{pid} {patient_name} {query}".strip()
        try:
            results = await memory_client.search_memories(
                full_query, limit=min(max(limit, 1), 50)
            )
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": str(e)}

        return {
            "patient_id": pid,
            "patient_name": patient_name,
            "query": query,
            "results": results.get("content", results),
        }

    @mcp.tool()
    async def memory_get_patient_history(
        patient_id: str | None = None,
        limit: int = 20,
    ) -> dict:
        """Return all stored memories for the current patient (chronological).

        Args:
            patient_id: FHIR Patient id (defaults to X-Patient-ID header).
            limit: Max memories to return (1–100).
        """
        if (err := check_fhir_context(require_patient=True, patient_id=patient_id)) is not None:
            return err
        pid = resolve_patient_id(patient_id) or ""

        patient_name = ""
        try:
            async with fhir_client_for_current_context() as fhir:
                patient = await fhir.get_patient(pid)
                patient_name = patient_display_name(patient)
        except FHIRError:
            patient_name = ""

        try:
            results = await memory_client.get_patient_memories(
                pid,
                patient_name=patient_name,
                limit=min(max(limit, 1), 100),
            )
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": str(e)}

        return {
            "patient_id": pid,
            "patient_name": patient_name,
            "memories": results.get("content", results),
        }

    _: Any = (
        memory_store_encounter,
        memory_store_alert,
        memory_search_history,
        memory_get_patient_history,
    )


__all__ = ["register_memory_tools"]
