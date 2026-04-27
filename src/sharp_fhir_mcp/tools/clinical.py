"""Clinical FHIR tools — patient demographics, encounters, appointments,
problems, medications, allergies, immunizations.

All tools are read-only and use the FHIR R4 search interface. They normalise
the raw FHIR resources via :mod:`sharp_fhir_mcp.fhir_utils` to return compact,
LLM-friendly summaries.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastmcp import FastMCP

from sharp_fhir_mcp.clients.fhir_client import FHIRError
from sharp_fhir_mcp.context import fhir_client_for_current_context
from sharp_fhir_mcp.fhir_utils import (
    allergy_summary,
    appointment_summary,
    bundle_next_link,
    bundle_to_resources,
    bundle_total,
    condition_summary,
    encounter_summary,
    immunization_summary,
    medication_request_summary,
    patient_summary,
)
from sharp_fhir_mcp.tools._helpers import (
    check_fhir_context,
    resolve_patient_id,
)


def register_clinical_tools(mcp: FastMCP) -> None:
    """Register patient/encounter/appointment/medication/condition tools."""

    # ====
    # Patient
    # ====

    @mcp.tool
    async def clinical_search_patients(
        name: str | None = None,
        family: str | None = None,
        given: str | None = None,
        birthdate: str | None = None,
        identifier: str | None = None,
        gender: str | None = None,
        count: int = 25,
    ) -> dict:
        """Search for patients on the connected FHIR server.

        Provide at least one search field. Combinations are AND-ed by the
        server. Returns a compact summary of matching patients.

        Args:
            name: Free-text name search (server interprets this loosely).
            family: Family / surname (partial match).
            given: Given / first name (partial match).
            birthdate: Date of birth in ``YYYY-MM-DD`` format.
            identifier: Patient identifier (e.g. MRN).
            gender: ``male``, ``female``, ``other``, or ``unknown``.
            count: Max patients to return (1–250). Defaults to 25.
        """
        if (err := check_fhir_context()) is not None:
            return err

        if not any([name, family, given, birthdate, identifier, gender]):
            return {"error": "Provide at least one search field."}

        try:
            async with fhir_client_for_current_context() as fhir:
                bundle = await fhir.search_patients(
                    name=name,
                    family=family,
                    given=given,
                    birthdate=birthdate,
                    identifier=identifier,
                    gender=gender,
                    count=min(max(count, 1), 250),
                )
        except FHIRError as e:
            return e.to_tool_response()

        patients = [patient_summary(p) for p in bundle_to_resources(bundle)]
        return {
            "patients": patients,
            "total_count": bundle_total(bundle),
            "returned": len(patients),
            "has_more": bundle_next_link(bundle) is not None,
            "next_link": bundle_next_link(bundle),
        }

    @mcp.tool
    async def clinical_get_patient_summary(patient_id: str | None = None) -> dict:
        """Return a summary of a patient — demographics + recent encounters & appointments.

        Args:
            patient_id: FHIR Patient resource id. If omitted, the X-Patient-ID
                header from the SHARP context is used.
        """
        if (err := check_fhir_context(require_patient=True, patient_id=patient_id)) is not None:
            return err
        pid = resolve_patient_id(patient_id) or ""

        try:
            async with fhir_client_for_current_context() as fhir:
                patient_task = fhir.get_patient(pid)
                encounters_task = fhir.get_encounters(pid, count=5)
                appointments_task = fhir.get_appointments(patient_id=pid, count=5)
                patient, encounters_bundle, appointments_bundle = await asyncio.gather(
                    patient_task, encounters_task, appointments_task,
                    return_exceptions=False,
                )
        except FHIRError as e:
            return e.to_tool_response()

        summary = patient_summary(patient)
        return {
            **summary,
            "recent_encounters": [
                encounter_summary(r) for r in bundle_to_resources(encounters_bundle)
            ],
            "recent_appointments": [
                appointment_summary(r) for r in bundle_to_resources(appointments_bundle)
            ],
        }

    # ====
    # Appointments / Encounters
    # ====

    @mcp.tool
    async def clinical_get_appointments(
        patient_id: str | None = None,
        date: str | None = None,
        status: str | None = None,
        count: int = 25,
    ) -> dict:
        """Search FHIR ``Appointment`` resources.

        Args:
            patient_id: FHIR Patient id (defaults to X-Patient-ID header).
                Pass an empty string to search all appointments (server-side
                filtering may still apply).
            date: FHIR date filter (e.g. ``ge2025-01-01``, ``2025-01-15``).
            status: FHIR Appointment status (``booked``, ``arrived``,
                ``fulfilled``, ``cancelled``, etc.).
            count: Max results (1–250).
        """
        if (err := check_fhir_context()) is not None:
            return err
        pid = resolve_patient_id(patient_id)

        try:
            async with fhir_client_for_current_context() as fhir:
                bundle = await fhir.get_appointments(
                    patient_id=pid,
                    date=date,
                    status=status,
                    count=min(max(count, 1), 250),
                )
        except FHIRError as e:
            return e.to_tool_response()

        appointments = [appointment_summary(a) for a in bundle_to_resources(bundle)]
        return {
            "appointments": appointments,
            "total_count": bundle_total(bundle),
            "returned": len(appointments),
            "has_more": bundle_next_link(bundle) is not None,
        }

    @mcp.tool
    async def clinical_get_encounters(
        patient_id: str | None = None,
        date: str | None = None,
        status: str | None = None,
        count: int = 25,
    ) -> dict:
        """Search FHIR ``Encounter`` resources for a patient.

        Args:
            patient_id: FHIR Patient id (defaults to X-Patient-ID header).
            date: FHIR date filter (e.g. ``ge2024-01-01``).
            status: Encounter status (``planned``, ``arrived``, ``in-progress``,
                ``finished``, ``cancelled``, ...).
            count: Max results (1–250).
        """
        if (err := check_fhir_context(require_patient=True, patient_id=patient_id)) is not None:
            return err
        pid = resolve_patient_id(patient_id) or ""

        try:
            async with fhir_client_for_current_context() as fhir:
                bundle = await fhir.get_encounters(
                    pid,
                    date=date,
                    status=status,
                    count=min(max(count, 1), 250),
                )
        except FHIRError as e:
            return e.to_tool_response()

        encounters = [encounter_summary(e) for e in bundle_to_resources(bundle)]
        return {
            "encounters": encounters,
            "total_count": bundle_total(bundle),
            "returned": len(encounters),
            "has_more": bundle_next_link(bundle) is not None,
        }

    # ====
    # Conditions / Problems
    # ====

    @mcp.tool
    async def clinical_get_problems(
        patient_id: str | None = None,
        active_only: bool = True,
        count: int = 50,
    ) -> dict:
        """Return the patient's active problem list (FHIR ``Condition`` resources).

        Args:
            patient_id: FHIR Patient id (defaults to X-Patient-ID header).
            active_only: When true, filters to ``clinical-status=active`` problems.
            count: Max results (1–250).
        """
        if (err := check_fhir_context(require_patient=True, patient_id=patient_id)) is not None:
            return err
        pid = resolve_patient_id(patient_id) or ""

        try:
            async with fhir_client_for_current_context() as fhir:
                bundle = await fhir.get_conditions(
                    pid,
                    clinical_status="active" if active_only else None,
                    count=min(max(count, 1), 250),
                )
        except FHIRError as e:
            return e.to_tool_response()

        problems = [condition_summary(c) for c in bundle_to_resources(bundle)]
        return {
            "problems": problems,
            "total_count": bundle_total(bundle),
            "returned": len(problems),
            "has_more": bundle_next_link(bundle) is not None,
        }

    # ====
    # Medications
    # ====

    @mcp.tool
    async def clinical_get_medications(
        patient_id: str | None = None,
        status: str | None = "active",
        count: int = 50,
    ) -> dict:
        """Return the patient's medications (FHIR ``MedicationRequest``).

        Args:
            patient_id: FHIR Patient id (defaults to X-Patient-ID header).
            status: MedicationRequest status filter — ``active``, ``completed``,
                ``stopped``, ``draft``, etc. Pass ``None`` (or empty) to fetch
                all statuses. Default: ``active``.
            count: Max results (1–250).
        """
        if (err := check_fhir_context(require_patient=True, patient_id=patient_id)) is not None:
            return err
        pid = resolve_patient_id(patient_id) or ""

        try:
            async with fhir_client_for_current_context() as fhir:
                bundle = await fhir.get_medication_requests(
                    pid,
                    status=status or None,
                    count=min(max(count, 1), 250),
                )
        except FHIRError as e:
            return e.to_tool_response()

        meds = [medication_request_summary(m) for m in bundle_to_resources(bundle)]
        return {
            "medications": meds,
            "total_count": bundle_total(bundle),
            "returned": len(meds),
            "has_more": bundle_next_link(bundle) is not None,
        }

    # ====
    # Allergies
    # ====

    @mcp.tool
    async def clinical_get_allergies(
        patient_id: str | None = None,
        count: int = 50,
    ) -> dict:
        """Return the patient's allergy & intolerance list.

        Args:
            patient_id: FHIR Patient id (defaults to X-Patient-ID header).
            count: Max results (1–250).
        """
        if (err := check_fhir_context(require_patient=True, patient_id=patient_id)) is not None:
            return err
        pid = resolve_patient_id(patient_id) or ""

        try:
            async with fhir_client_for_current_context() as fhir:
                bundle = await fhir.get_allergies(pid, count=min(max(count, 1), 250))
        except FHIRError as e:
            return e.to_tool_response()

        allergies = [allergy_summary(a) for a in bundle_to_resources(bundle)]
        return {
            "allergies": allergies,
            "total_count": bundle_total(bundle),
            "returned": len(allergies),
            "has_more": bundle_next_link(bundle) is not None,
        }

    # ====
    # Immunizations
    # ====

    @mcp.tool
    async def clinical_get_immunizations(
        patient_id: str | None = None,
        count: int = 50,
    ) -> dict:
        """Return the patient's immunization history.

        Args:
            patient_id: FHIR Patient id (defaults to X-Patient-ID header).
            count: Max results (1–250).
        """
        if (err := check_fhir_context(require_patient=True, patient_id=patient_id)) is not None:
            return err
        pid = resolve_patient_id(patient_id) or ""

        try:
            async with fhir_client_for_current_context() as fhir:
                bundle = await fhir.get_immunizations(pid, count=min(max(count, 1), 250))
        except FHIRError as e:
            return e.to_tool_response()

        imms = [immunization_summary(i) for i in bundle_to_resources(bundle)]
        return {
            "immunizations": imms,
            "total_count": bundle_total(bundle),
            "returned": len(imms),
            "has_more": bundle_next_link(bundle) is not None,
        }

    # ====
    # Consolidated health record
    # ====

    @mcp.tool
    async def clinical_get_health_record(patient_id: str | None = None) -> dict:
        """One-shot consolidated patient health record.

        Fetches active problems, active medications, allergies, and
        immunizations in parallel for the patient. Useful as a quick clinical
        review before a visit.

        Args:
            patient_id: FHIR Patient id (defaults to X-Patient-ID header).
        """
        if (err := check_fhir_context(require_patient=True, patient_id=patient_id)) is not None:
            return err
        pid = resolve_patient_id(patient_id) or ""

        try:
            async with fhir_client_for_current_context() as fhir:
                problems_b, meds_b, allergies_b, imms_b = await asyncio.gather(
                    fhir.get_conditions(pid, clinical_status="active", count=100),
                    fhir.get_medication_requests(pid, status="active", count=100),
                    fhir.get_allergies(pid, count=100),
                    fhir.get_immunizations(pid, count=100),
                )
        except FHIRError as e:
            return e.to_tool_response()

        problems = [condition_summary(c) for c in bundle_to_resources(problems_b)]
        meds = [medication_request_summary(m) for m in bundle_to_resources(meds_b)]
        allergies = [allergy_summary(a) for a in bundle_to_resources(allergies_b)]
        imms = [immunization_summary(i) for i in bundle_to_resources(imms_b)]

        return {
            "patient_id": pid,
            "problems": problems,
            "medications": meds,
            "allergies": allergies,
            "immunizations": imms,
            "counts": {
                "problems": len(problems),
                "medications": len(meds),
                "allergies": len(allergies),
                "immunizations": len(imms),
            },
        }

    # Make linters happy about the registered closures.
    _: Any = (
        clinical_search_patients,
        clinical_get_patient_summary,
        clinical_get_appointments,
        clinical_get_encounters,
        clinical_get_problems,
        clinical_get_medications,
        clinical_get_allergies,
        clinical_get_immunizations,
        clinical_get_health_record,
    )


__all__ = ["register_clinical_tools"]
