"""Optional clinical memory tools backed by mem0.

Tools registered only when mem0 is installed and configured (set
``OPENAI_API_KEY`` or ``OPENAI_API_BASE`` for an OpenAI-compatible
provider — see ``clients/mem0_client.py``).

mem0 is text-only: it ingests conversation messages and runs an LLM-driven
extraction pass to store atomic facts. We expose a clinically-shaped tool
surface on top:

    * memory_store_encounter   — visit summary
    * memory_store_alert       — persistent flag
    * memory_store_note        — free-text note
    * memory_search_history    — semantic search scoped to a patient
    * memory_get_patient_history — list all memories for the patient
    * memory_delete            — remove one memory
    * memory_reset_patient     — wipe all memories for the patient

For non-text clinical data (radiology images, audio dictation, video
clips), the agent host should pre-process them — caption images via a VLM,
transcribe audio with Whisper, summarise video — and then store the
resulting *text* through ``memory_store_note``. That keeps mem0's surface
clean and lets the host pick the right model per modality.

NOTE: Identifiers are FHIR resource ids (strings).
"""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from sharp_fhir_mcp.clients.fhir_client import FHIRError
from sharp_fhir_mcp.clients.mem0_client import Mem0Client, Mem0Error
from sharp_fhir_mcp.context import fhir_client_for_current_context
from sharp_fhir_mcp.fhir_utils import patient_display_name
from sharp_fhir_mcp.tools._helpers import check_fhir_context, resolve_patient_id


def register_memory_tools(
    mcp: FastMCP,
    memory_client: Mem0Client | None,
) -> None:
    """Register clinical-memory tools.

    No-op when ``memory_client`` is ``None``.
    """
    if memory_client is None or not memory_client.is_configured:
        return

    async def _patient_name(pid: str) -> str:
        try:
            async with fhir_client_for_current_context() as fhir:
                return patient_display_name(await fhir.get_patient(pid))
        except FHIRError:
            return ""

    @mcp.tool
    async def memory_store_encounter(
        encounter_summary: str,
        visit_date: str,
        chief_complaint: str | None = None,
        diagnosis: str | None = None,
        plan: str | None = None,
        practitioner_name: str | None = None,
        patient_id: str | None = None,
    ) -> dict:
        """Store a clinical encounter summary for cross-session recall.

        Args:
            encounter_summary: Brief narrative of the encounter.
            visit_date: ``YYYY-MM-DD``.
            chief_complaint: Optional chief complaint.
            diagnosis: Comma-separated diagnoses.
            plan: Plan/treatment narrative.
            practitioner_name: Optional practitioner display name.
            patient_id: FHIR Patient id (defaults to ``X-Patient-ID`` header).
        """
        if (err := check_fhir_context(require_patient=True, patient_id=patient_id)) is not None:
            return err
        pid = resolve_patient_id(patient_id) or ""
        name = await _patient_name(pid) or pid

        parts = [
            f"Encounter for {name} (FHIR Patient/{pid}) on {visit_date}.",
        ]
        if practitioner_name:
            parts.append(f"Provider: {practitioner_name}.")
        if chief_complaint:
            parts.append(f"Chief complaint: {chief_complaint}.")
        if diagnosis:
            parts.append(f"Diagnosis: {diagnosis}.")
        if plan:
            parts.append(f"Plan: {plan}.")
        parts.append(f"Summary: {encounter_summary}")

        try:
            result = await memory_client.add_text(
                "\n".join(parts),
                patient_id=pid,
                metadata={
                    "type": "encounter",
                    "visit_date": visit_date,
                    "patient_name": name,
                },
            )
        except Mem0Error as e:
            return {"success": False, "error": str(e)}

        return {
            "success": True,
            "patient_id": pid,
            "patient_name": name,
            "visit_date": visit_date,
            "result": result,
        }

    @mcp.tool
    async def memory_store_alert(
        alert_type: str,
        alert_content: str,
        severity: str = "warning",
        patient_id: str | None = None,
    ) -> dict:
        """Store a persistent clinical alert/flag for the patient.

        Args:
            alert_type: ``allergy`` / ``drug_interaction`` / ``lab_critical`` /
                ``patient_preference`` / ``behavioral`` / ``follow_up`` / ``other``.
            alert_content: Detailed description.
            severity: ``info`` | ``warning`` | ``critical``.
            patient_id: FHIR Patient id.
        """
        if (err := check_fhir_context(require_patient=True, patient_id=patient_id)) is not None:
            return err
        pid = resolve_patient_id(patient_id) or ""
        if severity not in {"info", "warning", "critical"}:
            severity = "warning"
        name = await _patient_name(pid) or pid

        text = (
            f"CLINICAL ALERT [{severity.upper()}] for {name} "
            f"(FHIR Patient/{pid}). Type: {alert_type}. {alert_content}"
        )
        try:
            result = await memory_client.add_text(
                text,
                patient_id=pid,
                metadata={
                    "type": "alert",
                    "alert_type": alert_type,
                    "severity": severity,
                    "patient_name": name,
                },
            )
        except Mem0Error as e:
            return {"success": False, "error": str(e)}

        return {
            "success": True,
            "patient_id": pid,
            "alert_type": alert_type,
            "severity": severity,
            "result": result,
        }

    @mcp.tool
    async def memory_store_note(
        note: str,
        note_type: str = "general",
        patient_id: str | None = None,
    ) -> dict:
        """Store a free-text clinical note tagged to the patient.

        Use this for any text the agent has produced from non-text inputs:
        radiology read summaries, audio-dictation transcripts, video-clip
        descriptions, etc. The agent host is responsible for the
        VLM/Whisper/etc pre-processing — this tool only persists text.

        Args:
            note: Note content.
            note_type: Free-form sub-type tag (``radiology``, ``transcript``,
                ``video_summary``, ``general``, …).
            patient_id: FHIR Patient id.
        """
        if (err := check_fhir_context(require_patient=True, patient_id=patient_id)) is not None:
            return err
        pid = resolve_patient_id(patient_id) or ""
        try:
            result = await memory_client.add_text(
                f"Note ({note_type}) for FHIR Patient/{pid}: {note}",
                patient_id=pid,
                metadata={"type": "note", "note_type": note_type},
            )
        except Mem0Error as e:
            return {"success": False, "error": str(e)}
        return {"success": True, "patient_id": pid, "result": result}

    @mcp.tool
    async def memory_search_history(
        query: str,
        limit: int = 10,
        patient_id: str | None = None,
    ) -> dict:
        """Semantic search across the patient's clinical memory.

        Args:
            query: Natural-language search.
            limit: Max results (1–50).
            patient_id: FHIR Patient id.
        """
        if (err := check_fhir_context(require_patient=True, patient_id=patient_id)) is not None:
            return err
        pid = resolve_patient_id(patient_id) or ""
        try:
            result = await memory_client.search(
                query, patient_id=pid, limit=min(max(limit, 1), 50)
            )
        except Mem0Error as e:
            return {"success": False, "error": str(e)}
        return {"patient_id": pid, "query": query, "results": result}

    @mcp.tool
    async def memory_get_patient_history(
        patient_id: str | None = None,
        limit: int = 50,
    ) -> dict:
        """Return all stored memories for the patient.

        Args:
            patient_id: FHIR Patient id.
            limit: Max memories (1–200).
        """
        if (err := check_fhir_context(require_patient=True, patient_id=patient_id)) is not None:
            return err
        pid = resolve_patient_id(patient_id) or ""
        try:
            result = await memory_client.get_all(
                patient_id=pid, limit=min(max(limit, 1), 200)
            )
        except Mem0Error as e:
            return {"success": False, "error": str(e)}
        return {"patient_id": pid, "memories": result}

    @mcp.tool
    async def memory_delete(memory_id: str) -> dict:
        """Delete a single memory by id.

        Args:
            memory_id: The mem0 memory id (returned by store/search calls).
        """
        try:
            result = await memory_client.delete(memory_id)
        except Mem0Error as e:
            return {"success": False, "error": str(e)}
        return {"success": True, "memory_id": memory_id, "result": result}

    @mcp.tool
    async def memory_reset_patient(patient_id: str | None = None) -> dict:
        """Wipe all stored memories for one patient. Irreversible.

        Args:
            patient_id: FHIR Patient id.
        """
        if (err := check_fhir_context(require_patient=True, patient_id=patient_id)) is not None:
            return err
        pid = resolve_patient_id(patient_id) or ""
        try:
            result = await memory_client.delete_all(patient_id=pid)
        except Mem0Error as e:
            return {"success": False, "error": str(e)}
        return {"success": True, "patient_id": pid, "result": result}

    _: Any = (
        memory_store_encounter,
        memory_store_alert,
        memory_store_note,
        memory_search_history,
        memory_get_patient_history,
        memory_delete,
        memory_reset_patient,
    )


__all__ = ["register_memory_tools"]
