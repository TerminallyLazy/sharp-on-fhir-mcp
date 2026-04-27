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
from sharp_fhir_mcp.clients.omnimem_client import OmniMemClient
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
# SHARP capability advertisement
# --

SHARP_EXPERIMENTAL_CAPABILITIES: dict[str, dict[str, Any]] = {
    # Per SHARP-on-MCP §"FHIR Context Capability".
    "fhir_context_required": {"value": True},
}


def _patch_capabilities(mcp: FastMCP) -> None:
    """Inject SHARP experimental capabilities into the init handshake.

    FastMCP 2.x doesn't expose ``experimental_capabilities`` on the
    constructor, so we monkey-patch the low-level ``create_initialization_options``
    of the inner server. Defensive about attribute names since FastMCP's
    internals may change between minor versions.
    """
    inner = (
        getattr(mcp, "_mcp_server", None)
        or getattr(mcp, "_low_level_server", None)
        or getattr(mcp, "server", None)
    )
    if inner is None or not hasattr(inner, "create_initialization_options"):
        logger.warning(
            "Could not locate FastMCP low-level server to inject SHARP "
            "experimental capabilities — `fhir_context_required` will NOT "
            "be advertised in the initialise response."
        )
        return

    original = inner.create_initialization_options

    def patched(
        notification_options: Any = None,
        experimental_capabilities: dict[str, dict[str, Any]] | None = None,
    ):
        merged: dict[str, dict[str, Any]] = dict(SHARP_EXPERIMENTAL_CAPABILITIES)
        if experimental_capabilities:
            merged.update(experimental_capabilities)
        return original(notification_options, merged)

    inner.create_initialization_options = patched  # type: ignore[assignment]


# --
# FastMCP factory
# --


def build_server() -> tuple[FastMCP, OmniMemClient | None]:
    """Build the configured :class:`FastMCP` instance + memory client (if any)."""
    load_dotenv()

    strict = _truthy_env("SHARP_STRICT_CONTEXT")

    mcp = FastMCP(
        name="sharp-fhir-mcp",
        instructions=(
            "SHARP-on-MCP compliant FHIR R4 MCP server. Provides clinical "
            "tools (FHIR search/read, patient context, labs, vitals, "
            "appointments, medications, allergies, immunizations) plus "
            "interactive MCP-UI dashboards and multimodal clinical memory "
            "(via OmniSimpleMem). Healthcare context is supplied by the "
            "agent on every request via the X-FHIR-Server-URL, "
            "X-FHIR-Access-Token, and X-Patient-ID headers (per SHARP §3.2)."
        ),
        version=__version__,
    )

    # SHARP context middleware — reads headers per request.
    mcp.add_middleware(SharpContextMiddleware(strict=strict))

    # Optional OmniSimpleMem client for cross-session multimodal memory.
    memory_client = OmniMemClient.from_env()
    if memory_client and memory_client.is_configured:
        logger.info("OmniSimpleMem clinical memory client configured at %s",
                    memory_client.api_url)
    else:
        logger.info("OmniSimpleMem not configured — memory_* tools unavailable")

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


# Module-level instance — discovered by `fastmcp run` and ASGI servers.
mcp, _memory_client = build_server()


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
