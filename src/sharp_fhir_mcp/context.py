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
basis without any global state.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import AsyncIterator

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

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
        """Whether this context can talk to a FHIR server."""
        return bool(self.server_url and self.access_token)

    def require_fhir(self) -> "SharpContext":
        """Return self if FHIR context is present, else raise."""
        if not self.has_fhir:
            raise FHIRContextMissingError(
                "FHIR context required. Send the X-FHIR-Server-URL and "
                "X-FHIR-Access-Token headers (per SHARP-on-MCP §3.2)."
            )
        return self

    def require_patient(self) -> str:
        """Return X-Patient-ID, raising if not present."""
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


def _build_context_from_request(request: Request) -> SharpContext:
    """Extract SHARP headers from a Starlette request, with env fallbacks."""
    headers = request.headers
    server_url = headers.get(HEADER_FHIR_SERVER_URL) or os.getenv(ENV_FHIR_SERVER_URL)
    access_token = headers.get(HEADER_FHIR_ACCESS_TOKEN) or os.getenv(
        ENV_FHIR_ACCESS_TOKEN
    )
    patient_id = headers.get(HEADER_PATIENT_ID) or os.getenv(ENV_PATIENT_ID)

    # Strip "Bearer " prefix if accidentally included by the agent.
    if access_token and access_token.lower().startswith("bearer "):
        access_token = access_token[7:].strip()

    return SharpContext(
        server_url=server_url.rstrip("/") if server_url else None,
        access_token=access_token,
        patient_id=patient_id,
    )


def get_current_context() -> SharpContext:
    """Return the SHARP context for the current request.

    If no middleware ran (e.g., direct in-process call), an env-fallback
    context is returned. This makes tools safely runnable in tests.
    """
    ctx = _current_context.get()
    if ctx is not None:
        return ctx
    # Fall back to environment variables (handy for tests / local dev)
    server_url = os.getenv(ENV_FHIR_SERVER_URL)
    return SharpContext(
        server_url=server_url.rstrip("/") if server_url else None,
        access_token=os.getenv(ENV_FHIR_ACCESS_TOKEN),
        patient_id=os.getenv(ENV_PATIENT_ID),
    )


def require_fhir_context() -> SharpContext:
    """Return current context, raising if FHIR headers are missing."""
    return get_current_context().require_fhir()


@asynccontextmanager
async def fhir_client_for_current_context() -> AsyncIterator["FHIRClient"]:
    """Yield a configured :class:`FHIRClient` for the current request.

    The client is closed automatically when the context manager exits.
    """
    # Imported here to avoid a circular import.
    from sharp_fhir_mcp.clients.fhir_client import FHIRClient

    ctx = require_fhir_context()
    client = FHIRClient(base_url=ctx.server_url, access_token=ctx.access_token)
    try:
        yield client
    finally:
        await client.close()


# --
# Starlette middleware
# --


class SharpContextMiddleware(BaseHTTPMiddleware):
    """Reads SHARP headers off every HTTP request and stores them in a ContextVar.

    By default this is permissive — it does **not** enforce 403 responses.
    Tools themselves return structured ``fhir_context_required`` errors when
    they need context that wasn't supplied. To enable strict enforcement
    (returns ``403 Forbidden`` to the agent for any non-initialize call that
    lacks FHIR context), pass ``strict=True``.
    """

    def __init__(self, app: ASGIApp, *, strict: bool = False) -> None:
        super().__init__(app)
        self.strict = strict

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[override]
        ctx = _build_context_from_request(request)

        if self.strict and request.method == "POST" and not ctx.has_fhir:
            # Allow the body through only if we can determine it is an
            # ``initialize`` or ``tools/list`` call. Otherwise reject early.
            try:
                body = await request.body()
                # Wrap to make body re-readable downstream.
                async def _receive() -> dict:  # type: ignore[no-redef]
                    return {"type": "http.request", "body": body, "more_body": False}

                request._receive = _receive  # type: ignore[attr-defined]
                method = _peek_jsonrpc_method(body)
            except Exception:
                method = None

            if method not in {"initialize", "notifications/initialized", "tools/list", "ping"}:
                return JSONResponse(
                    status_code=403,
                    content={
                        "error": "fhir_context_required",
                        "message": (
                            "This MCP server advertises "
                            "`capabilities.experimental.fhir_context_required = true`. "
                            "Include X-FHIR-Server-URL and X-FHIR-Access-Token headers."
                        ),
                        "required_headers": [
                            HEADER_FHIR_SERVER_URL,
                            HEADER_FHIR_ACCESS_TOKEN,
                        ],
                    },
                )

        token = _current_context.set(ctx)
        try:
            response = await call_next(request)
        finally:
            _current_context.reset(token)
        return response


def _peek_jsonrpc_method(body: bytes) -> str | None:
    """Best-effort sniff of the JSON-RPC method without consuming the body."""
    if not body:
        return None
    try:
        import json

        payload = json.loads(body)
    except Exception:
        return None
    if isinstance(payload, list):
        # Batch — return first method, only used for routing decisions.
        if payload:
            return payload[0].get("method") if isinstance(payload[0], dict) else None
        return None
    if isinstance(payload, dict):
        return payload.get("method")
    return None


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
