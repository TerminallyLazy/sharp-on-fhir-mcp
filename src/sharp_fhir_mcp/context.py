"""SHARP-on-MCP per-request context propagation.

Per §3.2 of the SHARP-on-MCP specification, healthcare context (FHIR server
URL, access token, default patient) is delivered to the MCP server via HTTP
headers on every request — *not* via a server-side OAuth flow.

Headers:
    X-FHIR-Server-URL    Base URL of the FHIR R4 server (e.g.,
                         ``https://hapi.fhir.org/baseR4``).
    X-FHIR-Access-Token  Bearer token already minted by the agent's host.
    X-Patient-ID         Optional default patient context.

This module wires those headers into a :class:`contextvars.ContextVar` so that
asynchronous MCP tool implementations can resolve them on a per-invocation
basis without any global state. The middleware is implemented as a FastMCP
middleware (fastmcp 2.x), which has access to the underlying HTTP headers via
``fastmcp.server.dependencies.get_http_headers()``.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, AsyncIterator

from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_headers
from fastmcp.server.middleware import Middleware, MiddlewareContext

# Header names per SHARP §3.2
HEADER_FHIR_SERVER_URL = "X-FHIR-Server-URL"
HEADER_FHIR_ACCESS_TOKEN = "X-FHIR-Access-Token"
HEADER_PATIENT_ID = "X-Patient-ID"

# Optional fallback environment variables — only used when no headers are
# supplied (useful for local dev against a public FHIR sandbox).
ENV_FHIR_SERVER_URL = "FHIR_SERVER_URL"
ENV_FHIR_ACCESS_TOKEN = "FHIR_ACCESS_TOKEN"
ENV_PATIENT_ID = "PATIENT_ID"


@dataclass(frozen=True, slots=True)
class SharpContext:
    """Resolved SHARP context for a single MCP request."""

    server_url: str | None
    access_token: str | None
    patient_id: str | None

    @property
    def has_fhir(self) -> bool:
        return bool(self.server_url and self.access_token)

    def require_fhir(self) -> "SharpContext":
        if not self.has_fhir:
            raise FHIRContextMissingError(
                "FHIR context required. Send the X-FHIR-Server-URL and "
                "X-FHIR-Access-Token headers (per SHARP-on-MCP §3.2)."
            )
        return self

    def require_patient(self) -> str:
        if not self.patient_id:
            raise FHIRContextMissingError(
                "Patient context required. Send the X-Patient-ID header or "
                "pass an explicit patient_id argument."
            )
        return self.patient_id


class FHIRContextMissingError(RuntimeError):
    """Raised when a tool needs FHIR context that the request didn't supply."""


# --
# ContextVar plumbing
# --


_current_context: ContextVar[SharpContext | None] = ContextVar(
    "sharp_fhir_mcp_context", default=None
)


def _normalise_headers(raw: dict[str, str] | None) -> dict[str, str]:
    """Return a case-insensitive lookup dict (lowercased keys)."""
    if not raw:
        return {}
    return {k.lower(): v for k, v in raw.items()}


def _build_context_from_headers(headers: dict[str, str]) -> SharpContext:
    server_url = headers.get(HEADER_FHIR_SERVER_URL.lower()) or os.getenv(
        ENV_FHIR_SERVER_URL
    )
    access_token = headers.get(HEADER_FHIR_ACCESS_TOKEN.lower()) or os.getenv(
        ENV_FHIR_ACCESS_TOKEN
    )
    patient_id = headers.get(HEADER_PATIENT_ID.lower()) or os.getenv(ENV_PATIENT_ID)

    if access_token and access_token.lower().startswith("bearer "):
        access_token = access_token[7:].strip()

    return SharpContext(
        server_url=server_url.rstrip("/") if server_url else None,
        access_token=access_token,
        patient_id=patient_id,
    )


def get_current_context() -> SharpContext:
    """Return the SHARP context for the current request.

    Resolution order:
        1. ContextVar set by SharpContextMiddleware.
        2. Live-read of HTTP headers via fastmcp's request scope (fallback for
           tools called outside a middleware-wrapped flow).
        3. Environment variables (handy for tests / stdio dev).
    """
    ctx = _current_context.get()
    if ctx is not None:
        return ctx

    # Try to read headers directly from the active fastmcp request scope.
    try:
        headers = _normalise_headers(get_http_headers() or {})
    except Exception:  # noqa: BLE001 — outside a request scope
        headers = {}

    if headers:
        return _build_context_from_headers(headers)

    # Pure env-var fallback (tests / stdio).
    server_url = os.getenv(ENV_FHIR_SERVER_URL)
    return SharpContext(
        server_url=server_url.rstrip("/") if server_url else None,
        access_token=os.getenv(ENV_FHIR_ACCESS_TOKEN),
        patient_id=os.getenv(ENV_PATIENT_ID),
    )


def require_fhir_context() -> SharpContext:
    return get_current_context().require_fhir()


@asynccontextmanager
async def fhir_client_for_current_context() -> AsyncIterator[Any]:
    """Yield a configured :class:`FHIRClient` for the current request."""
    from sharp_fhir_mcp.clients.fhir_client import FHIRClient

    ctx = require_fhir_context()
    client = FHIRClient(base_url=ctx.server_url, access_token=ctx.access_token)
    try:
        yield client
    finally:
        await client.close()


# --
# FastMCP middleware
# --


class SharpContextMiddleware(Middleware):
    """Read SHARP headers off every MCP request and stash them in a ContextVar.

    By default the middleware is *permissive*: it never rejects a request,
    and tools themselves return structured ``fhir_context_required`` errors
    when headers are missing. Pass ``strict=True`` to make any ``tools/call``
    without FHIR headers raise a :class:`fastmcp.exceptions.ToolError`
    (handshake calls — ``initialize``, ``tools/list``, ``ping`` — are always
    allowed through).
    """

    def __init__(self, *, strict: bool = False) -> None:
        self.strict = strict

    async def on_message(
        self, context: MiddlewareContext, call_next
    ):  # type: ignore[override]
        headers = _normalise_headers(get_http_headers() or {})
        ctx = _build_context_from_headers(headers)

        token = _current_context.set(ctx)
        try:
            return await call_next(context)
        finally:
            _current_context.reset(token)

    async def on_call_tool(
        self, context: MiddlewareContext, call_next
    ):  # type: ignore[override]
        if self.strict:
            ctx = _current_context.get() or _build_context_from_headers(
                _normalise_headers(get_http_headers() or {})
            )
            if not ctx.has_fhir:
                raise ToolError(
                    "fhir_context_required: send X-FHIR-Server-URL and "
                    "X-FHIR-Access-Token headers per SHARP-on-MCP §3.2."
                )
        return await call_next(context)


__all__ = [
    "HEADER_FHIR_SERVER_URL",
    "HEADER_FHIR_ACCESS_TOKEN",
    "HEADER_PATIENT_ID",
    "SharpContext",
    "FHIRContextMissingError",
    "SharpContextMiddleware",
    "get_current_context",
    "require_fhir_context",
    "fhir_client_for_current_context",
]
