"""Type definitions and Pydantic models for the sharp-fhir-mcp server.

These ``Literal`` types are intentionally narrow to constrain the LLM's tool
inputs and prevent hallucinated values.
"""

from typing import Literal

from pydantic import BaseModel, Field

# --
# FHIR Literal type constraints
# --

FHIRResourceType = Literal[
    "Patient",
    "Practitioner",
    "PractitionerRole",
    "Organization",
    "Encounter",
    "Appointment",
    "Observation",
    "Condition",
    "MedicationRequest",
    "MedicationStatement",
    "AllergyIntolerance",
    "Immunization",
    "DiagnosticReport",
    "Procedure",
    "DocumentReference",
    "Coverage",
    "CarePlan",
    "CareTeam",
    "Goal",
    "Location",
    "ServiceRequest",
    "Task",
    "Composition",
]

# FHIR ``Observation.category`` codes (subset)
ObservationCategory = Literal[
    "vital-signs",
    "laboratory",
    "imaging",
    "social-history",
    "survey",
    "exam",
    "therapy",
    "activity",
    "procedure",
]

# FHIR ``Patient.gender`` codes
AdministrativeGender = Literal["male", "female", "other", "unknown"]

# FHIR ``Condition.clinicalStatus`` codes
ClinicalStatus = Literal[
    "active",
    "recurrence",
    "relapse",
    "inactive",
    "remission",
    "resolved",
]

# Clinical alert severities used by the in-memory layer
AlertSeverity = Literal["info", "warning", "critical"]


# --
# Pydantic models for tool responses (lightweight summaries)
# --


class FHIRContextHeaders(BaseModel):
    """Per-request SHARP context as carried in HTTP headers.

    See SHARP-on-MCP §3.2 "Context Passing".
    """

    server_url: str | None = Field(
        default=None,
        description="FHIR R4 base URL (X-FHIR-Server-URL header).",
    )
    access_token: str | None = Field(
        default=None,
        description="Bearer token for the FHIR server (X-FHIR-Access-Token header).",
    )
    patient_id: str | None = Field(
        default=None,
        description="Default patient context (X-Patient-ID header).",
    )

    @property
    def has_fhir(self) -> bool:
        """Whether enough FHIR context is present to make a FHIR call."""
        return bool(self.server_url and self.access_token)


class PatientSummary(BaseModel):
    """Compact patient summary derived from a FHIR ``Patient`` resource."""

    id: str
    name: str | None = None
    date_of_birth: str | None = None
    gender: str | None = None
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    age: int | None = None


class CodingSummary(BaseModel):
    """Summary of a FHIR ``CodeableConcept``."""

    text: str | None = None
    code: str | None = None
    system: str | None = None
    display: str | None = None


class PaginatedFHIRResponse(BaseModel):
    """Lightweight wrapper around a FHIR Bundle with pagination hints."""

    resource_type: str
    total: int
    returned: int
    has_more: bool
    next_link: str | None = None
    entries: list[dict] = Field(default_factory=list)
