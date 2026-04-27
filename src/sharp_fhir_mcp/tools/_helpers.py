"""Internal helpers shared by tool modules.

These helpers are intentionally functional (rather than decorator-based) so
that FastMCP's signature introspection sees each tool's real arguments and
can build accurate JSON schemas for the agent.
"""

from __future__ import annotations

from typing import Any

from sharp_fhir_mcp.context import SharpContext, get_current_context


def fhir_context_error(message: str) -> dict[str, Any]:
    """Standard payload for missing-FHIR-context errors."""
    return {
        "error": "fhir_context_required",
        "message": message,
        "required_headers": [
            "X-FHIR-Server-URL",
            "X-FHIR-Access-Token",
        ],
        "optional_headers": ["X-Patient-ID"],
        "spec": "https://www.sharponmcp.com/overview.html",
    }


def check_fhir_context(
    *,
    require_patient: bool = False,
    patient_id: str | None = None,
) -> dict[str, Any] | None:
    """Return an error response if the SHARP context is incomplete, else ``None``.

    Args:
        require_patient: If true, also requires a patient identifier
            (either via the ``patient_id`` parameter or ``X-Patient-ID`` header).
        patient_id: The explicit ``patient_id`` from the tool argument. The
            header is consulted only when this is ``None``.
    """
    ctx = get_current_context()
    if not ctx.has_fhir:
        return fhir_context_error(
            "This tool requires FHIR context. Please send X-FHIR-Server-URL "
            "and X-FHIR-Access-Token request headers (see SHARP-on-MCP §3.2)."
        )
    if require_patient and not (patient_id or ctx.patient_id):
        return fhir_context_error(
            "Pass an explicit patient_id argument or set the X-Patient-ID header."
        )
    return None


def resolve_patient_id(explicit: str | None) -> str | None:
    """Resolve an effective patient id from arg → header → ``None``."""
    if explicit:
        return explicit
    return get_current_context().patient_id


def current_context() -> SharpContext:
    """Convenience re-export of :func:`get_current_context` for tools."""
    return get_current_context()


__all__ = [
    "fhir_context_error",
    "check_fhir_context",
    "resolve_patient_id",
    "current_context",
]
