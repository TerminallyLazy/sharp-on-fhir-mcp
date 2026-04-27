"""Generic FHIR R4 read/search MCP tools.

These are vendor-neutral tools — they work with any FHIR R4 server reachable
from the SHARP context headers (``X-FHIR-Server-URL`` / ``X-FHIR-Access-Token``).
"""

from __future__ import annotations

from fastmcp import FastMCP

from sharp_fhir_mcp.clients.fhir_client import FHIRError
from sharp_fhir_mcp.context import fhir_client_for_current_context
from sharp_fhir_mcp.fhir_utils import (
    bundle_next_link,
    bundle_to_resources,
    bundle_total,
    patient_summary,
)
from sharp_fhir_mcp.models.types import FHIRResourceType
from sharp_fhir_mcp.tools._helpers import (
    check_fhir_context,
    fhir_context_error,
    resolve_patient_id,
)


def register_fhir_tools(mcp: FastMCP) -> None:
    """Register generic FHIR R4 tools with the FastMCP instance."""

    @mcp.tool
    async def fhir_get_capability_statement() -> dict:
        """Return the FHIR server's ``CapabilityStatement`` (``GET /metadata``).

        Useful for discovering which resource types and search parameters the
        FHIR server supports before calling other tools.
        """
        if (err := check_fhir_context()) is not None:
            return err
        try:
            async with fhir_client_for_current_context() as fhir:
                cap = await fhir.get_capability_statement()
        except FHIRError as e:
            return e.to_tool_response()

        rest = (cap.get("rest") or [{}])[0]
        resources = rest.get("resource") or []
        return {
            "fhir_version": cap.get("fhirVersion"),
            "status": cap.get("status"),
            "publisher": cap.get("publisher"),
            "software": (cap.get("software") or {}).get("name"),
            "implementation": (cap.get("implementation") or {}).get("description"),
            "supported_resources": [
                {
                    "type": r.get("type"),
                    "interactions": [
                        i.get("code") for i in (r.get("interaction") or [])
                    ],
                    "search_params": [
                        p.get("name") for p in (r.get("searchParam") or [])
                    ],
                }
                for r in resources[:30]
            ],
            "security": rest.get("security", {}),
            "total_resource_types": len(resources),
        }

    @mcp.tool
    async def fhir_get_patient(patient_id: str | None = None) -> dict:
        """Read a single FHIR ``Patient`` resource and return a compact summary.

        Args:
            patient_id: FHIR Patient resource id. If omitted, the X-Patient-ID
                header from the SHARP context is used.

        Returns:
            Compact patient summary with the full FHIR resource under ``raw``.
        """
        if (err := check_fhir_context(require_patient=True, patient_id=patient_id)) is not None:
            return err
        pid = resolve_patient_id(patient_id) or ""
        try:
            async with fhir_client_for_current_context() as fhir:
                patient = await fhir.get_patient(pid)
        except FHIRError as e:
            return e.to_tool_response()
        summary = patient_summary(patient)
        return {**summary, "raw": patient}

    @mcp.tool
    async def fhir_search(
        resource_type: FHIRResourceType,
        patient_id: str | None = None,
        params: str | None = None,
        count: int = 25,
    ) -> dict:
        """Search any FHIR R4 resource type, returning a compact Bundle summary.

        Args:
            resource_type: One of the supported FHIR resource types (Patient,
                Observation, Condition, MedicationRequest, AllergyIntolerance,
                Immunization, DiagnosticReport, Procedure, Encounter,
                DocumentReference, Coverage, ...).
            patient_id: Optional patient filter — adds ``patient=<id>`` to the
                search. Defaults to the X-Patient-ID header when set.
            params: Additional FHIR query string parameters (e.g.
                ``"category=vital-signs&_sort=-date"``).
            count: ``_count`` page size (default 25, max 250).

        Returns:
            Bundle summary with ``total``, ``entries`` (compact resources), and
            a ``next_link`` cursor when more results are available.
        """
        if (err := check_fhir_context()) is not None:
            return err

        search_params: dict[str, str] = {"_count": str(min(max(count, 1), 250))}

        # Patient filter — only meaningful for non-Patient resource types.
        effective_patient = resolve_patient_id(patient_id)
        if effective_patient and resource_type != "Patient":
            search_params["patient"] = effective_patient
        elif resource_type == "Patient" and patient_id:
            # When searching for the Patient resource itself, ``_id`` is the
            # right parameter — not ``patient``.
            search_params["_id"] = patient_id

        if params:
            for pair in params.split("&"):
                if "=" in pair:
                    key, value = pair.split("=", 1)
                    if key:
                        search_params[key] = value

        try:
            async with fhir_client_for_current_context() as fhir:
                bundle = await fhir.search(resource_type, search_params)
        except FHIRError as e:
            return e.to_tool_response()

        resources = bundle_to_resources(bundle)
        return {
            "resource_type": "Bundle",
            "search_type": resource_type,
            "total": bundle_total(bundle),
            "returned": len(resources),
            "has_more": bundle_next_link(bundle) is not None,
            "next_link": bundle_next_link(bundle),
            "entries": [
                {
                    "resourceType": r.get("resourceType"),
                    "id": r.get("id"),
                    "resource": r,
                }
                for r in resources[:50]
            ],
        }

    @mcp.tool
    async def fhir_read(resource_type: FHIRResourceType, resource_id: str) -> dict:
        """Read a single FHIR resource by ``resourceType`` and ``id``.

        Use this when you already know the exact resource id (e.g. from a
        previous search). Returns the raw FHIR resource.
        """
        if (err := check_fhir_context()) is not None:
            return err
        try:
            async with fhir_client_for_current_context() as fhir:
                resource = await fhir.get_resource(resource_type, resource_id)
        except FHIRError as e:
            return e.to_tool_response()
        return resource

    @mcp.tool
    async def fhir_patient_everything(
        patient_id: str | None = None,
        start: str | None = None,
        end: str | None = None,
    ) -> dict:
        """Invoke ``Patient/{id}/$everything`` and summarise the result.

        Returns a roll-up of resource counts plus the first 100 entries.
        Useful for one-shot patient context retrieval, but note that not all
        FHIR servers implement the ``$everything`` operation.

        Args:
            patient_id: FHIR Patient id. Falls back to X-Patient-ID.
            start: Optional clinical date lower bound (YYYY-MM-DD).
            end: Optional clinical date upper bound (YYYY-MM-DD).
        """
        if (err := check_fhir_context(require_patient=True, patient_id=patient_id)) is not None:
            return err
        pid = resolve_patient_id(patient_id) or ""
        try:
            async with fhir_client_for_current_context() as fhir:
                bundle = await fhir.get_patient_everything(pid, start=start, end=end)
        except FHIRError as e:
            if e.status_code in (404, 405, 501):
                return {
                    "error": "operation_not_supported",
                    "message": (
                        "The FHIR server does not support Patient/$everything. "
                        "Use fhir_search for individual resource types instead."
                    ),
                    "alternative_tool": "fhir_search",
                }
            return e.to_tool_response()

        resources = bundle_to_resources(bundle)
        counts: dict[str, int] = {}
        for r in resources:
            t = r.get("resourceType") or "Unknown"
            counts[t] = counts.get(t, 0) + 1

        return {
            "patient_id": pid,
            "total_resources": len(resources),
            "resource_summary": counts,
            "next_link": bundle_next_link(bundle),
            "entries": [
                {
                    "resourceType": r.get("resourceType"),
                    "id": r.get("id"),
                    "resource": r,
                }
                for r in resources[:100]
            ],
        }

    # Make linters happy about the registered closures.
    _ = (
        fhir_get_capability_statement,
        fhir_get_patient,
        fhir_search,
        fhir_read,
        fhir_patient_everything,
    )


__all__ = ["register_fhir_tools"]
