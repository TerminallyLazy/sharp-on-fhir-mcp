"""SHARP-on-MCP compliant FHIR MCP server entry point.

Built on FastMCP 2.x (https://gofastmcp.com). Speaks Streamable HTTP transport
(per SHARP §"Scope" — stdio is explicitly not in scope) and reads its
healthcare context from per-request HTTP headers (per SHARP §3.2):

    X-FHIR-Server-URL    Base URL of the FHIR R4 server.
    X-FHIR-Access-Token  Bearer token for the FHIR server.
    X-Patient-ID         Optional default patient context.

Advertises ``capabilities.experimental.fhir_context_required = true`` so
SHARP-aware MCP clients/agents know to forward those headers on every call.
"""

from __future__ import annotations

import argparse
import logging
import os
from typing import Any

from dotenv import load_dotenv
from fastmcp import FastMCP

from sharp_fhir_mcp import __version__
from sharp_fhir_mcp.clients.mem0_client import Mem0Client
from sharp_fhir_mcp.context import SharpContextMiddleware
from sharp_fhir_mcp.tools import (
    register_clinical_context_tools,
    register_clinical_tools,
    register_fhir_tools,
    register_lab_imaging_tools,
    register_memory_tools,
    register_visualization_tools,
)

logger = logging.getLogger("sharp_fhir_mcp")


# --
# Capability advertisement — Prompt Opinion Platform contract
# --

# The Prompt Opinion Platform reads `extensions["ai.promptopinion/fhir-context"]`
# off the initialise response to learn which SMART scopes to mint when it
# forwards a request to this server. The contract is documented (by example)
# in https://github.com/prompt-opinion/po-community-mcp.
#
# Each scope entry: {"name": "<smart-scope-string>", "required": <bool>}
#
# We declare every FHIR resource any tool in this server reads. Marking the
# core Patient scope as required — the others are best-effort, so the host
# can still grant a partial token if the EHR/user denies optional scopes.
PO_FHIR_CONTEXT_EXTENSION: dict[str, Any] = {
    "scopes": [
        {"name": "patient/Patient.rs", "required": True},
        {"name": "patient/Observation.rs"},
        {"name": "patient/Condition.rs"},
        {"name": "patient/MedicationRequest.rs"},
        {"name": "patient/MedicationStatement.rs"},
        {"name": "patient/AllergyIntolerance.rs"},
        {"name": "patient/Immunization.rs"},
        {"name": "patient/DiagnosticReport.rs"},
        {"name": "patient/Procedure.rs"},
        {"name": "patient/Encounter.rs"},
        {"name": "patient/Appointment.rs"},
        {"name": "patient/DocumentReference.rs"},
        {"name": "patient/Coverage.rs"},
    ]
}

# Kept for SHARP-on-MCP-only clients (defensive — Prompt Opinion Platform
# doesn't read this, but other SHARP-aware hosts might).
SHARP_EXPERIMENTAL_CAPABILITIES: dict[str, dict[str, Any]] = {
    "fhir_context_required": {"value": True},
}


def _patch_capabilities(mcp: FastMCP) -> None:
    """Inject Prompt Opinion + SHARP capability advertisements.

    Two slots are populated:
        * ``extensions["ai.promptopinion/fhir-context"]`` — the dialect
          Prompt Opinion's marketplace reads to mint SMART scopes.
        * ``experimental.fhir_context_required`` — SHARP-on-MCP §"FHIR
          Context Capability"; honoured by other SHARP-aware hosts.

    FastMCP doesn't expose either via constructor, so we monkey-patch the
    inner server's ``get_capabilities`` to splice both in. Defensive about
    FastMCP internals.
    """
    inner = (
        getattr(mcp, "_mcp_server", None)
        or getattr(mcp, "_low_level_server", None)
        or getattr(mcp, "server", None)
    )
    if inner is None:
        logger.warning(
            "Could not locate FastMCP low-level server — capability "
            "advertisements (Prompt Opinion + SHARP) will NOT be injected."
        )
        return

    if hasattr(inner, "get_capabilities"):
        original_get = inner.get_capabilities

        def _patched_get(notification_options=None, experimental_capabilities=None):
            # Merge SHARP experimental capability into every call so the
            # advertisement is present whether the caller is the init
            # handshake or `tools/list` introspection.
            merged_exp: dict[str, dict[str, Any]] = dict(SHARP_EXPERIMENTAL_CAPABILITIES)
            if experimental_capabilities:
                merged_exp.update(experimental_capabilities)

            caps = original_get(notification_options, merged_exp)

            # Inject Prompt Opinion's FHIR-context extension.
            extras = getattr(caps, "model_extra", None)
            if extras is None:
                try:
                    setattr(caps, "extensions", {
                        "ai.promptopinion/fhir-context": PO_FHIR_CONTEXT_EXTENSION,
                    })
                except Exception:  # noqa: BLE001
                    pass
            else:
                extras["extensions"] = {
                    "ai.promptopinion/fhir-context": PO_FHIR_CONTEXT_EXTENSION,
                }
            return caps

        inner.get_capabilities = _patched_get  # type: ignore[assignment]

    if hasattr(inner, "create_initialization_options"):
        original_init = inner.create_initialization_options

        def _patched_init(
            notification_options: Any = None,
            experimental_capabilities: dict[str, dict[str, Any]] | None = None,
        ):
            merged: dict[str, dict[str, Any]] = dict(SHARP_EXPERIMENTAL_CAPABILITIES)
            if experimental_capabilities:
                merged.update(experimental_capabilities)
            return original_init(notification_options, merged)

        inner.create_initialization_options = _patched_init  # type: ignore[assignment]


# --
# FastMCP factory
# --


def build_server() -> tuple[FastMCP, Mem0Client | None]:
    """Build the configured :class:`FastMCP` instance + memory client (if any)."""
    load_dotenv()

    strict = _truthy_env("SHARP_STRICT_CONTEXT")

    mcp = FastMCP(
        name="sharp-fhir-mcp",
        instructions=(
            "SHARP-on-MCP compliant FHIR R4 MCP server. Provides clinical "
            "tools (FHIR search/read, patient context, labs, vitals, "
            "appointments, medications, allergies, immunizations) plus "
            "interactive MCP-UI dashboards and optional cross-session "
            "clinical memory (via mem0). Healthcare context is supplied "
            "by the agent on every request via the X-FHIR-Server-URL, "
            "X-FHIR-Access-Token, and X-Patient-ID headers (per SHARP §3.2)."
        ),
        version=__version__,
    )

    # SHARP context middleware — reads headers per request.
    mcp.add_middleware(SharpContextMiddleware(strict=strict))

    # Optional mem0 client for cross-session clinical memory.
    memory_client = Mem0Client.from_env()
    if memory_client and memory_client.is_configured:
        logger.info("mem0 clinical memory configured")
    else:
        logger.info("mem0 not configured — memory_* tools unavailable")

    # Register tool groups.
    register_fhir_tools(mcp)
    register_clinical_tools(mcp)
    register_lab_imaging_tools(mcp)
    register_clinical_context_tools(mcp)
    register_memory_tools(mcp, memory_client)
    register_visualization_tools(mcp)

    # Inject SHARP experimental capability into init handshake.
    _patch_capabilities(mcp)

    return mcp, memory_client


def _truthy_env(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


# Module-level instances — discovered by `fastmcp run`, ASGI servers, and
# serverless platforms (Vercel/Fly/Render) that import `app`.
mcp, _memory_client = build_server()
app = mcp.http_app()  # Starlette ASGI — `from sharp_fhir_mcp.server import app`


# --
# CLI (local dev only — hosted deployments use `fastmcp run` or uvicorn)
# --


def main(argv: list[str] | None = None) -> None:
    """Run the MCP server locally."""
    parser = argparse.ArgumentParser(
        prog="sharp-fhir-mcp",
        description="SHARP-on-MCP compliant FHIR MCP server (FastMCP 2.x)",
    )
    parser.add_argument(
        "--transport",
        choices=("http", "stdio", "sse"),
        default=os.getenv("MCP_TRANSPORT", "http"),
        help="MCP transport (default: http; stdio is non-SHARP)",
    )
    parser.add_argument("--host", default=os.getenv("HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")))
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)-8s %(name)s %(message)s",
    )

    if args.transport == "stdio":
        logger.warning(
            "stdio transport is NOT in scope per SHARP-on-MCP §Scope — "
            "use http for production deployments."
        )
        mcp.run(transport="stdio")
        return

    logger.info(
        "🩺 sharp-fhir-mcp v%s starting on %s:%d (transport=%s)",
        __version__,
        args.host,
        args.port,
        args.transport,
    )
    mcp.run(transport=args.transport, host=args.host, port=args.port)


if __name__ == "__main__":  # pragma: no cover
    main()
