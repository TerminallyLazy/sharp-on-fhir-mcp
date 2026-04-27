"""Optional clinical memory tools backed by OmniSimpleMem.

Tools registered only when ``OMNIMEM_API_URL`` is set. Provide persistent,
cross-session, multimodal memory of past encounters, alerts, notes, and
clinical media (images / audio / video) keyed to FHIR Patient ids.

OmniSimpleMem features used:
    * Hybrid (FAISS dense + BM25 sparse) retrieval, scoped via patient tags.
    * Pyramid retrieval (preview → details → evidence) for token efficiency.
    * Multimodal ingestion of medical images, audio recordings, and video.
    * Optional RAG-style ``memory_answer_question`` over the patient's memory.

NOTE: Identifiers are FHIR resource ids (strings), per the SHARP context
model.
"""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from sharp_fhir_mcp.clients.fhir_client import FHIRError
from sharp_fhir_mcp.clients.omnimem_client import OmniMemClient, OmniMemError
from sharp_fhir_mcp.context import fhir_client_for_current_context
from sharp_fhir_mcp.fhir_utils import patient_display_name
from sharp_fhir_mcp.tools._helpers import check_fhir_context, resolve_patient_id


def register_memory_tools(
    mcp: FastMCP,
    memory_client: OmniMemClient | None,
) -> None:
    """Register clinical-memory tools.

    No-op when ``memory_client`` is ``None``. Callers should still invoke
    this so registration is consistent across configurations.
    """
    if memory_client is None or not memory_client.is_configured:
        return

    async def _patient_name(pid: str) -> str:
        try:
            async with fhir_client_for_current_context() as fhir:
                return patient_display_name(await fhir.get_patient(pid))
        except FHIRError:
            return ""

    # --
    # Text memories — encounters, alerts, free-text notes
    # --

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
        diagnosis_list = [d.strip() for d in diagnosis.split(",")] if diagnosis else None

        try:
            result = await memory_client.store_patient_encounter(
                patient_id=pid,
                patient_name=name,
                encounter_summary=encounter_summary,
                visit_date=visit_date,
                practitioner_name=practitioner_name,
                chief_complaint=chief_complaint,
                diagnosis=diagnosis_list,
                plan=plan,
            )
        except OmniMemError as e:
            return {"success": False, "error": str(e), "detail": e.detail}

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

        try:
            result = await memory_client.store_clinical_alert(
                patient_id=pid,
                patient_name=name,
                alert_type=alert_type,
                alert_content=alert_content,
                severity=severity,
            )
        except OmniMemError as e:
            return {"success": False, "error": str(e), "detail": e.detail}

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
        tags: str | None = None,
        patient_id: str | None = None,
    ) -> dict:
        """Store a free-text clinical note tagged to the patient.

        Args:
            note: Note content.
            tags: Optional comma-separated extra tags.
            patient_id: FHIR Patient id.
        """
        if (err := check_fhir_context(require_patient=True, patient_id=patient_id)) is not None:
            return err
        pid = resolve_patient_id(patient_id) or ""
        extra = [t.strip() for t in tags.split(",")] if tags else None

        try:
            result = await memory_client.add_text(
                f"Note (FHIR Patient/{pid}): {note}",
                session_id=pid,
                tags=[f"patient_id:{pid}", "type:note", *(extra or [])],
            )
        except OmniMemError as e:
            return {"success": False, "error": str(e), "detail": e.detail}

        return {"success": True, "patient_id": pid, "result": result}

    # --
    # Multimodal memories
    # --

    @mcp.tool
    async def memory_store_image(
        image_path: str,
        description: str | None = None,
        patient_id: str | None = None,
    ) -> dict:
        """Ingest a clinical image (radiology film, dermatology photo, etc.) into memory.

        ``image_path`` must be readable by THIS server process — useful when
        the agent host has uploaded a file to a shared volume. Images are
        filtered by CLIP scene-change detection (~70% storage reduction).

        Args:
            image_path: Path to image file (PNG/JPG/etc).
            description: Optional caption / radiology read.
            patient_id: FHIR Patient id.
        """
        if (err := check_fhir_context(require_patient=True, patient_id=patient_id)) is not None:
            return err
        pid = resolve_patient_id(patient_id) or ""
        tags = [f"patient_id:{pid}", "type:image"]

        try:
            result = await memory_client.add_image(
                image_path, session_id=pid, tags=tags
            )
            if description:
                # Pair the image MAU with a text caption for retrieval coverage.
                await memory_client.add_text(
                    f"Image caption (FHIR Patient/{pid}, file={image_path}): "
                    f"{description}",
                    session_id=pid,
                    tags=[*tags, "type:image_caption"],
                )
        except (OmniMemError, FileNotFoundError) as e:
            return {"success": False, "error": str(e)}

        return {"success": True, "patient_id": pid, "result": result}

    @mcp.tool
    async def memory_store_audio(
        audio_path: str,
        description: str | None = None,
        patient_id: str | None = None,
    ) -> dict:
        """Ingest a clinical audio recording (consult, dictation, heart sound).

        Audio passes through VAD silence-filtering (~40% reduction). Pair
        with a transcript via ``description`` for richer retrieval.

        Args:
            audio_path: Path to audio file (WAV/MP3/etc).
            description: Optional caption / transcript snippet.
            patient_id: FHIR Patient id.
        """
        if (err := check_fhir_context(require_patient=True, patient_id=patient_id)) is not None:
            return err
        pid = resolve_patient_id(patient_id) or ""
        tags = [f"patient_id:{pid}", "type:audio"]

        try:
            result = await memory_client.add_audio(
                audio_path, session_id=pid, tags=tags
            )
            if description:
                await memory_client.add_text(
                    f"Audio caption (FHIR Patient/{pid}, file={audio_path}): "
                    f"{description}",
                    session_id=pid,
                    tags=[*tags, "type:audio_caption"],
                )
        except (OmniMemError, FileNotFoundError) as e:
            return {"success": False, "error": str(e)}

        return {"success": True, "patient_id": pid, "result": result}

    @mcp.tool
    async def memory_store_video(
        video_path: str,
        description: str | None = None,
        max_frames: int = 100,
        patient_id: str | None = None,
    ) -> dict:
        """Ingest a clinical video (procedure recording, ultrasound clip, gait).

        OmniSimpleMem extracts up to ``max_frames`` keyframes via CLIP-based
        scene-change detection.

        Args:
            video_path: Path to video file (MP4/MOV/etc).
            description: Optional caption / procedure note.
            max_frames: Cap on extracted frames (default 100).
            patient_id: FHIR Patient id.
        """
        if (err := check_fhir_context(require_patient=True, patient_id=patient_id)) is not None:
            return err
        pid = resolve_patient_id(patient_id) or ""
        tags = [f"patient_id:{pid}", "type:video"]

        try:
            result = await memory_client.add_video(
                video_path, session_id=pid, tags=tags, max_frames=max_frames
            )
            if description:
                await memory_client.add_text(
                    f"Video caption (FHIR Patient/{pid}, file={video_path}): "
                    f"{description}",
                    session_id=pid,
                    tags=[*tags, "type:video_caption"],
                )
        except (OmniMemError, FileNotFoundError) as e:
            return {"success": False, "error": str(e)}

        return {"success": True, "patient_id": pid, "result": result}

    # --
    # Retrieval
    # --

    @mcp.tool
    async def memory_search_history(
        query: str,
        top_k: int = 10,
        auto_expand: bool = True,
        patient_id: str | None = None,
    ) -> dict:
        """Search the patient's clinical memory with hybrid retrieval.

        Args:
            query: Natural-language search.
            top_k: Max results (1–50).
            auto_expand: Expand top hits to evidence level (slower, more detail).
            patient_id: FHIR Patient id.
        """
        if (err := check_fhir_context(require_patient=True, patient_id=patient_id)) is not None:
            return err
        pid = resolve_patient_id(patient_id) or ""
        top_k = min(max(top_k, 1), 50)
        scoped = f"patient_id:{pid} {query}".strip()

        try:
            result = await memory_client.query(
                scoped, top_k=top_k, auto_expand=auto_expand
            )
        except OmniMemError as e:
            return {"success": False, "error": str(e), "detail": e.detail}

        return {"patient_id": pid, "query": query, "results": result}

    @mcp.tool
    async def memory_get_patient_history(
        patient_id: str | None = None,
        top_k: int = 20,
    ) -> dict:
        """Return recent stored memories for the current patient.

        Args:
            patient_id: FHIR Patient id.
            top_k: Max memories (1–100).
        """
        if (err := check_fhir_context(require_patient=True, patient_id=patient_id)) is not None:
            return err
        pid = resolve_patient_id(patient_id) or ""
        top_k = min(max(top_k, 1), 100)

        try:
            result = await memory_client.get_patient_history(
                patient_id=pid, top_k=top_k
            )
        except OmniMemError as e:
            return {"success": False, "error": str(e), "detail": e.detail}

        return {"patient_id": pid, "memories": result}

    @mcp.tool
    async def memory_answer_question(
        question: str,
        top_k: int = 10,
        include_sources: bool = True,
        patient_id: str | None = None,
    ) -> dict:
        """Answer a question grounded in the patient's clinical memory (RAG).

        Uses OmniSimpleMem's ``/answer`` endpoint, which retrieves relevant
        MAUs and asks the configured LLM to synthesise an answer.

        Args:
            question: Natural-language question.
            top_k: Memories to retrieve as context.
            include_sources: Return source MAUs alongside the answer.
            patient_id: FHIR Patient id (scopes retrieval).
        """
        if (err := check_fhir_context(require_patient=True, patient_id=patient_id)) is not None:
            return err
        pid = resolve_patient_id(patient_id) or ""
        scoped = f"For FHIR Patient/{pid}: {question}"

        try:
            result = await memory_client.answer(
                scoped, top_k=top_k, include_sources=include_sources
            )
        except OmniMemError as e:
            return {"success": False, "error": str(e), "detail": e.detail}

        return {"patient_id": pid, "question": question, "answer": result}

    @mcp.tool
    async def memory_expand(
        mau_ids: str,
        level: str = "evidence",
    ) -> dict:
        """Expand specific Multimodal Atomic Units (MAUs) to full content.

        Use after ``memory_search_history`` returns previews — pass the
        ``mau_id``s you want full evidence for.

        Args:
            mau_ids: Comma-separated MAU ids.
            level: ``summary`` | ``metadata`` | ``details`` | ``evidence``.
        """
        ids = [m.strip() for m in mau_ids.split(",") if m.strip()]
        if not ids:
            return {"success": False, "error": "no mau_ids provided"}
        try:
            result = await memory_client.expand(ids, level=level)
        except OmniMemError as e:
            return {"success": False, "error": str(e), "detail": e.detail}
        return {"mau_ids": ids, "level": level, "result": result}

    @mcp.tool
    async def memory_stats() -> dict:
        """Return OmniSimpleMem system statistics (MAU/event/vector counts)."""
        try:
            return await memory_client.stats()
        except OmniMemError as e:
            return {"success": False, "error": str(e), "detail": e.detail}

    _: Any = (
        memory_store_encounter,
        memory_store_alert,
        memory_store_note,
        memory_store_image,
        memory_store_audio,
        memory_store_video,
        memory_search_history,
        memory_get_patient_history,
        memory_answer_question,
        memory_expand,
        memory_stats,
    )


__all__ = ["register_memory_tools"]
