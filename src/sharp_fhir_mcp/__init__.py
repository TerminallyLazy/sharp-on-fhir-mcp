"""sharp-fhir-mcp — A SHARP-on-MCP compliant FHIR R4 MCP server.

This package implements a remote Model Context Protocol (MCP) server that
exposes a curated set of FHIR R4 tools for clinical AI agents. It follows
the Standardized Healthcare Agent Remote Protocol (SHARP-on-MCP) spec:

- Streamable HTTP transport (per SHARP "remote-only" requirement)
- Per-request FHIR context via HTTP headers (X-FHIR-Server-URL,
  X-FHIR-Access-Token, X-Patient-ID)
- FHIR context discovery via the MCP ``initialize`` response
  (``capabilities.experimental.fhir_context_required.value = true``)
- Decoupled from any particular EHR — works with any FHIR R4 server
"""

__version__ = "0.1.0"
__all__ = ["__version__"]
