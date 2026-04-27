"""SHARP-on-MCP compliant FHIR MCP server entry point.

This server speaks the standard MCP protocol over Streamable HTTP transport
(per SHARP §"Scope" — stdio is explicitly not in scope) and reads its
healthcare context from per-request HTTP headers (per SHARP §3.2):

    X-FHIR-Server-URL    Base URL of the FHIR R4 server.
    X-FHIR-Access-Token  Bearer token for the FHIR server.
    X-Patient-ID         Optional default patient context.

It advertises ``capabilities.experimental.fhir_context_required = true`` so
SHARP-aware MCP clients/agents know to forward those headers on every call.
"""

from __future__ import annotations

import argparse
import logging
import os
from typing import Any

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette

from sharp_fhir_mcp import __version__
from sharp_fhir_mcp.clients.simplemem_client import SimpleMemClient
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
    # Tells the MCP client/agent that this server requires healthcare context
    # to be supplied via headers on every tool invocation.
    "fhir_context_required": {"value": True},
}


def _patch_capabilities(mcp: FastMCP) -> None:
    """Inject SHARP experimental capabilities into FastMCP init options.

    FastMCP's :meth:`streamable_http_app` doesn't expose a way to pass
    ``experimental_capabilities`` to ``create_initialization_options``.
    We monkey-patch the inner Server's ``create_initialization_options``
    so that every initialise response carries the SHARP advertisement.
    """
    inner_server = mcp._mcp_server
    original = inner_server.create_initialization_options

    def patched(
        notification_options: Any = None,
        experimental_capabilities: dict[str, dict[str, Any]] | None = None,
    ):
        merged: dict[str, dict[str, Any]] = dict(SHARP_EXPERIMENTAL_CAPABILITIES)
        if experimental_capabilities:
            merged.update(experimental_capabilities)
        return original(notification_options, merged)

    inner_server.create_initialization_options = patched  # type: ignore[assignment]


# --
# FastMCP factory
# --


def build_server() -> tuple[FastMCP, SimpleMemClient | None]:
    """Build the configured :class:`FastMCP` instance + memory client (if any)."""
    load_dotenv()

    # Honour HOST/PORT environment variables (Vercel sets PORT by default).
    host = os.getenv("HOST", "0.0.0.0")
    try:
        port = int(os.getenv("PORT", "8000"))
    except ValueError:
        port = 8000

    mcp = FastMCP(
        "sharp-fhir-mcp",
        instructions=(
            "SHARP-on-MCP compliant FHIR R4 MCP server. Provides clinical "
            "tools (FHIR search/read, patient context, labs, vitals, "
            "appointments, medications, allergies, immunizations) plus "
            "interactive MCP-UI dashboards. Healthcare context is supplied "
            "by the agent on every request via the X-FHIR-Server-URL, "
            "X-FHIR-Access-Token, and X-Patient-ID headers (per SHARP §3.2)."
        ),
        host=host,
        port=port,
        # Stateless: each request is independent; sessions are not persisted
        # server-side. Aligns with serverless deployment targets (Vercel etc.).
        stateless_http=True,
    )

    # Permissive transport security so the MCP client can call from any host
    # (production deployments should narrow this down via env or a custom
    # `transport_security`). Without this, FastMCP's default DNS rebinding
    # protection rejects anything other than 127.0.0.1.
    try:
        mcp.settings.transport_security.allowed_hosts = ["*"]
        mcp.settings.transport_security.allowed_origins = ["*"]
    except Exception:  # noqa: BLE001
        pass

    # Optional SimpleMem client for cross-session clinical memory.
    memory_client = SimpleMemClient.from_env()
    if memory_client and memory_client.is_configured:
        logger.info("SimpleMem clinical memory client configured")
    else:
        logger.info("SimpleMem not configured — memory_* tools will be unavailable")

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


# Module-level instances for ASGI servers (`uvicorn sharp_fhir_mcp.server:app`).
mcp, _memory_client = build_server()


def build_asgi_app(*, strict: bool = False) -> Starlette:
    """Return the Starlette ASGI app with SHARP context middleware applied.

    Args:
        strict: When ``True``, the middleware rejects non-handshake calls that
            arrive without FHIR context headers with a ``403 Forbidden``.
            Defaults to permissive — tools themselves return structured
            ``fhir_context_required`` errors.
    """
    starlette_app = mcp.streamable_http_app()
    starlette_app.add_middleware(SharpContextMiddleware, strict=strict)
    return starlette_app


# Default permissive ASGI app — friendly to local dev and Vercel.
app: Starlette = build_asgi_app()


# --
# CLI
# --


def main(argv: list[str] | None = None) -> None:
    """Run the MCP server.

    By default the server listens for Streamable HTTP on ``HOST:PORT``
    (default ``0.0.0.0:8000``). Pass ``--stdio`` for legacy local dev, but
    note that SHARP-on-MCP §"Scope" specifies that stdio is *not* in scope.
    """
    parser = argparse.ArgumentParser(
        prog="sharp-fhir-mcp",
        description="SHARP-on-MCP compliant FHIR MCP server",
    )
    parser.add_argument(
        "--transport",
        choices=("streamable-http", "stdio", "sse"),
        default=os.getenv("MCP_TRANSPORT", "streamable-http"),
        help="MCP transport (default: streamable-http; stdio is non-SHARP)",
    )
    parser.add_argument("--host", default=None, help="Listen host (overrides HOST env)")
    parser.add_argument("--port", type=int, default=None, help="Listen port")
    parser.add_argument(
        "--strict-context",
        action="store_true",
        help="Reject non-handshake requests missing FHIR context with 403",
    )
    parser.add_argument("--version", action="version", version=__version__)

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)-8s %(name)s %(message)s",
    )

    if args.host:
        mcp.settings.host = args.host
    if args.port:
        mcp.settings.port = args.port

    if args.transport == "stdio":
        logger.warning(
            "stdio transport is NOT in scope per SHARP-on-MCP §Scope — "
            "use streamable-http for production deployments."
        )
        mcp.run("stdio")
        return

    # Streamable HTTP / SSE — both go through Starlette so the SHARP
    # context middleware can inspect every request.
    asgi_app = build_asgi_app(strict=args.strict_context)
    import uvicorn

    config = uvicorn.Config(
        asgi_app,
        host=mcp.settings.host,
        port=mcp.settings.port,
        log_level=mcp.settings.log_level.lower(),
    )
    server = uvicorn.Server(config)
    logger.info(
        "🩺 sharp-fhir-mcp v%s listening on %s:%d (transport=%s, strict_context=%s)",
        __version__,
        mcp.settings.host,
        mcp.settings.port,
        args.transport,
        args.strict_context,
    )
    import asyncio

    asyncio.run(server.serve())


if __name__ == "__main__":  # pragma: no cover
    main()
