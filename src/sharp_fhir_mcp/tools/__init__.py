"""MCP tools exposed by sharp-fhir-mcp.

Tools are organised by clinical domain:

- :mod:`sharp_fhir_mcp.tools.fhir` — Generic FHIR R4 read/search tools.
- :mod:`sharp_fhir_mcp.tools.clinical` — Patient / Encounter / Appointment.
- :mod:`sharp_fhir_mcp.tools.lab_imaging` — Observations, DiagnosticReports,
  DocumentReferences.
- :mod:`sharp_fhir_mcp.tools.clinical_context` — Aggregated visit context.
- :mod:`sharp_fhir_mcp.tools.memory` — Optional SimpleMem-backed clinical memory.
- :mod:`sharp_fhir_mcp.tools.visualization` — MCP-UI rendered charts/dashboards.

Each module exposes a ``register_*_tools(mcp)`` function that wires its tools
into a :class:`mcp.server.fastmcp.FastMCP` instance.
"""

from sharp_fhir_mcp.tools.fhir import register_fhir_tools
from sharp_fhir_mcp.tools.clinical import register_clinical_tools
from sharp_fhir_mcp.tools.clinical_context import register_clinical_context_tools
from sharp_fhir_mcp.tools.lab_imaging import register_lab_imaging_tools
from sharp_fhir_mcp.tools.memory import register_memory_tools
from sharp_fhir_mcp.tools.visualization import register_visualization_tools

__all__ = [
    "register_fhir_tools",
    "register_clinical_tools",
    "register_clinical_context_tools",
    "register_lab_imaging_tools",
    "register_memory_tools",
    "register_visualization_tools",
]
