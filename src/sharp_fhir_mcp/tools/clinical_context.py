"""Comprehensive clinical context for the current patient.

Aggregates Patient + AllergyIntolerance + MedicationRequest + Condition +
Observation (labs & vitals) + Immunization + Encounter into a single,
LLM-friendly response — including derived clinical alerts.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from fastmcp import FastMCP

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
from sharp_fhir_mcp.tools._helpers import (
    check_fhir_context,
    resolve_patient_id,
)


def register_clinical_context_tools(mcp: FastMCP) -> None:
    """Register the comprehensive clinical context tool."""

    @mcp.tool
    async def clinical_get_context(
        patient_id: str | None = None,
        lab_lookback_days: int = 90,
        vitals_lookback_days: int = 365,
        encounter_lookback_days: int = 365,
        include_alerts: bool = True,
    ) -> dict:
        """Get a comprehensive clinical context for a patient visit.

        Pulls demographics, allergies, active medications, active problems,
        immunizations, recent lab results, vital signs, and recent encounters
        in parallel from the FHIR server. Optionally derives clinical alerts
        (allergies, abnormal labs, polypharmacy).

        This is the recommended tool to call at the start of any clinical
        interaction — it costs ~6-8 FHIR searches but returns everything an
        agent needs to reason about the patient in one shot.

        Args:
            patient_id: FHIR Patient id (defaults to X-Patient-ID header).
            lab_lookback_days: Window for ``Observation`` lab results (default 90).
            vitals_lookback_days: Window for vital-sign observations (default 365).
            encounter_lookback_days: Window for ``Encounter`` history (default 365).
            include_alerts: Whether to derive and include clinical alerts.
        """
        if (err := check_fhir_context(require_patient=True, patient_id=patient_id)) is not None:
            return err
        pid = resolve_patient_id(patient_id) or ""

        now = datetime.now(timezone.utc)
        lab_since = (now - timedelta(days=lab_lookback_days)).strftime("%Y-%m-%d")
        vitals_since = (now - timedelta(days=vitals_lookback_days)).strftime("%Y-%m-%d")
        encounters_since = (now - timedelta(days=encounter_lookback_days)).strftime(
            "%Y-%m-%d"
        )

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
                    ),
                    fhir.get_observations(
                        pid,
                        category="vital-signs",
                        date=f"ge{vitals_since}",
                        count=100,
                    ),
                    fhir.get_encounters(
                        pid,
                        date=f"ge{encounters_since}",
                        count=10,
                    ),
                    return_exceptions=True,
                )
        except FHIRError as e:
            return e.to_tool_response()

        (
            patient_resp,
            allergies_resp,
            meds_resp,
            problems_resp,
            imms_resp,
            labs_resp,
            vitals_resp,
            encounters_resp,
        ) = results

        # Surface partial failures rather than aborting the whole context.
        def _safe_resources(resp: Any) -> list[dict]:
            if isinstance(resp, Exception):
                return []
            return bundle_to_resources(resp)

        patient = patient_resp if not isinstance(patient_resp, Exception) else {}
        allergies = [allergy_summary(r) for r in _safe_resources(allergies_resp)]
        meds = [medication_request_summary(r) for r in _safe_resources(meds_resp)]
        problems = [condition_summary(r) for r in _safe_resources(problems_resp)]
        imms = [immunization_summary(r) for r in _safe_resources(imms_resp)]
        labs = [observation_summary(r) for r in _safe_resources(labs_resp)]
        vitals = [observation_summary(r) for r in _safe_resources(vitals_resp)]
        encounters = [encounter_summary(r) for r in _safe_resources(encounters_resp)]

        partial_errors = {
            key: str(resp)
            for key, resp in zip(
                [
                    "patient",
                    "allergies",
                    "medications",
                    "problems",
                    "immunizations",
                    "labs",
                    "vitals",
                    "encounters",
                ],
                results,
            )
            if isinstance(resp, Exception)
        }

        demographics = patient_summary(patient) if patient else {}

        context = {
            "retrieved_at": now.isoformat(),
            "patient_id": pid,
            "demographics": demographics,
            "allergies": allergies,
            "active_medications": meds,
            "active_problems": problems,
            "immunizations": imms,
            "recent_labs": labs,
            "recent_vitals": vitals,
            "recent_encounters": encounters,
            "counts": {
                "allergies": len(allergies),
                "medications": len(meds),
                "problems": len(problems),
                "immunizations": len(imms),
                "labs": len(labs),
                "vitals": len(vitals),
                "encounters": len(encounters),
            },
        }

        if include_alerts:
            context["alerts"] = _generate_alerts(allergies, meds, labs)

        if partial_errors:
            context["partial_errors"] = partial_errors

        return context

    _: Any = (clinical_get_context,)


# --
# Alert generation (pure helper; FHIR-shape input dicts already normalised)
# --


def _generate_alerts(
    allergies: list[dict[str, Any]],
    medications: list[dict[str, Any]],
    labs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Derive clinical alerts from normalised summaries."""
    alerts: list[dict[str, Any]] = []

    if allergies:
        # Filter to active / unresolved allergies for the count if status known.
        active = [
            a
            for a in allergies
            if (a.get("clinical_status") or "active").lower() != "resolved"
        ]
        if active:
            alerts.append(
                {
                    "type": "allergy_warning",
                    "severity": "high",
                    "message": f"Patient has {len(active)} documented allergies",
                    "details": [a.get("allergen") for a in active if a.get("allergen")],
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

    if len(medications) >= 10:
        alerts.append(
            {
                "type": "polypharmacy",
                "severity": "medium",
                "message": (
                    f"Patient on {len(medications)} active medications — "
                    "review for interactions"
                ),
            }
        )

    return alerts


__all__ = ["register_clinical_context_tools"]
