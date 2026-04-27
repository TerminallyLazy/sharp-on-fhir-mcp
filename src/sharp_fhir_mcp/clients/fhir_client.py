"""Vendor-neutral asynchronous FHIR R4 client.

This client is intentionally generic — it does **not** know or care which
EHR vendor is behind the FHIR endpoint. Per SHARP-on-MCP §3.2, the bearer
token is obtained by the agent (host) before it ever reaches us, and is
forwarded on every MCP invocation via the ``X-FHIR-Access-Token`` header.

Usage::

    async with FHIRClient(base_url, access_token=token) as fhir:
        patient = await fhir.get_patient("123")
"""

from __future__ import annotations

from typing import Any

import httpx

DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=30.0)


class FHIRClient:
    """Async FHIR R4 client.

    Args:
        base_url: FHIR server base URL (e.g. ``https://hapi.fhir.org/baseR4``).
        access_token: Bearer token forwarded as-is in the ``Authorization``
            header. ``None`` is allowed for open/anonymous FHIR servers.
        extra_headers: Additional headers (e.g. ``Epic-Client-ID``).
        timeout: Optional :class:`httpx.Timeout` override.
    """

    def __init__(
        self,
        base_url: str | None,
        access_token: str | None = None,
        *,
        extra_headers: dict[str, str] | None = None,
        timeout: httpx.Timeout | None = None,
    ) -> None:
        if not base_url:
            raise ValueError(
                "FHIRClient requires a base_url. Pass X-FHIR-Server-URL or "
                "set FHIR_SERVER_URL in the environment."
            )
        self.base_url = base_url.rstrip("/")
        self.access_token = access_token
        self._extra_headers = dict(extra_headers or {})
        self._timeout = timeout or DEFAULT_TIMEOUT
        self._client: httpx.AsyncClient | None = None

    # --
    # Lifecycle
    # --

    @property
    def is_configured(self) -> bool:
        """Whether a base URL is configured (token may still be missing)."""
        return bool(self.base_url)

    async def __aenter__(self) -> "FHIRClient":
        await self._get_client()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers = {
                "Accept": "application/fhir+json",
                "Content-Type": "application/fhir+json",
                **self._extra_headers,
            }
            if self.access_token:
                headers["Authorization"] = f"Bearer {self.access_token}"
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=headers,
                timeout=self._timeout,
                follow_redirects=True,
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    # --
    # Generic HTTP helpers
    # --

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        client = await self._get_client()
        # FHIR search params accept comma-separated values; preserve them.
        clean_params = (
            {k: v for k, v in params.items() if v is not None} if params else None
        )
        response = await client.request(method, path, params=clean_params, json=json)
        if response.status_code == 204:
            return {}
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # Surface FHIR OperationOutcome details where possible.
            try:
                detail = response.json()
            except Exception:
                detail = {"text": response.text[:500]}
            raise FHIRError(
                status_code=response.status_code,
                message=str(exc),
                detail=detail,
            ) from exc
        if not response.content:
            return {}
        return response.json()

    async def get(
        self, path: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return await self._request("GET", path, params=params)

    async def post(self, path: str, json: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", path, json=json)

    # --
    # FHIR R4 operations
    # --

    async def get_capability_statement(self) -> dict[str, Any]:
        """Return the server's ``CapabilityStatement`` (``GET /metadata``)."""
        return await self.get("/metadata")

    async def get_resource(
        self, resource_type: str, resource_id: str
    ) -> dict[str, Any]:
        """Read a single resource by type and id."""
        return await self.get(f"/{resource_type}/{resource_id}")

    async def search(
        self,
        resource_type: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Search for resources of ``resource_type`` and return the Bundle."""
        return await self.get(f"/{resource_type}", params)

    # --
    # Convenience accessors (compose ``search`` for common workflows)
    # --

    async def get_patient(self, patient_id: str) -> dict[str, Any]:
        return await self.get_resource("Patient", patient_id)

    async def search_patients(
        self,
        *,
        name: str | None = None,
        family: str | None = None,
        given: str | None = None,
        birthdate: str | None = None,
        identifier: str | None = None,
        gender: str | None = None,
        count: int = 25,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"_count": count}
        if name:
            params["name"] = name
        if family:
            params["family"] = family
        if given:
            params["given"] = given
        if birthdate:
            params["birthdate"] = birthdate
        if identifier:
            params["identifier"] = identifier
        if gender:
            params["gender"] = gender
        return await self.search("Patient", params)

    async def get_observations(
        self,
        patient_id: str,
        *,
        category: str | None = None,
        code: str | None = None,
        date: str | None = None,
        count: int = 50,
        sort: str | None = "-date",
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"patient": patient_id, "_count": count}
        if category:
            params["category"] = category
        if code:
            params["code"] = code
        if date:
            params["date"] = date
        if sort:
            params["_sort"] = sort
        return await self.search("Observation", params)

    async def get_conditions(
        self,
        patient_id: str,
        *,
        clinical_status: str | None = None,
        count: int = 50,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"patient": patient_id, "_count": count}
        if clinical_status:
            params["clinical-status"] = clinical_status
        return await self.search("Condition", params)

    async def get_medication_requests(
        self,
        patient_id: str,
        *,
        status: str | None = None,
        count: int = 50,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"patient": patient_id, "_count": count}
        if status:
            params["status"] = status
        return await self.search("MedicationRequest", params)

    async def get_allergies(
        self, patient_id: str, *, count: int = 50
    ) -> dict[str, Any]:
        return await self.search(
            "AllergyIntolerance", {"patient": patient_id, "_count": count}
        )

    async def get_immunizations(
        self, patient_id: str, *, count: int = 50
    ) -> dict[str, Any]:
        return await self.search(
            "Immunization", {"patient": patient_id, "_count": count}
        )

    async def get_diagnostic_reports(
        self,
        patient_id: str,
        *,
        category: str | None = None,
        date: str | None = None,
        count: int = 50,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"patient": patient_id, "_count": count}
        if category:
            params["category"] = category
        if date:
            params["date"] = date
        return await self.search("DiagnosticReport", params)

    async def get_procedures(
        self, patient_id: str, *, count: int = 50
    ) -> dict[str, Any]:
        return await self.search(
            "Procedure", {"patient": patient_id, "_count": count}
        )

    async def get_encounters(
        self,
        patient_id: str,
        *,
        date: str | None = None,
        status: str | None = None,
        count: int = 50,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"patient": patient_id, "_count": count}
        if date:
            params["date"] = date
        if status:
            params["status"] = status
        return await self.search("Encounter", params)

    async def get_appointments(
        self,
        patient_id: str | None = None,
        *,
        date: str | None = None,
        status: str | None = None,
        count: int = 50,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"_count": count}
        if patient_id:
            params["patient"] = patient_id
        if date:
            params["date"] = date
        if status:
            params["status"] = status
        return await self.search("Appointment", params)

    async def get_document_references(
        self,
        patient_id: str,
        *,
        category: str | None = None,
        type_: str | None = None,
        count: int = 25,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"patient": patient_id, "_count": count}
        if category:
            params["category"] = category
        if type_:
            params["type"] = type_
        return await self.search("DocumentReference", params)

    async def get_coverage(
        self, patient_id: str, *, count: int = 10
    ) -> dict[str, Any]:
        return await self.search(
            "Coverage", {"beneficiary": patient_id, "_count": count}
        )

    # --
    # Patient/$everything
    # --

    async def get_patient_everything(
        self,
        patient_id: str,
        *,
        start: str | None = None,
        end: str | None = None,
        types: list[str] | None = None,
    ) -> dict[str, Any]:
        """Invoke the ``Patient/{id}/$everything`` operation.

        Note: not all FHIR servers implement this operation.
        """
        params: dict[str, Any] = {}
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        if types:
            params["_type"] = ",".join(types)
        return await self.get(f"/Patient/{patient_id}/$everything", params or None)


# --
# Errors
# --


class FHIRError(RuntimeError):
    """Raised when the FHIR server returns a non-2xx response."""

    def __init__(
        self,
        *,
        status_code: int,
        message: str,
        detail: dict | None = None,
    ) -> None:
        super().__init__(f"[{status_code}] {message}")
        self.status_code = status_code
        self.detail = detail or {}

    def to_tool_response(self) -> dict[str, Any]:
        """Render this error as a tool response payload."""
        return {
            "error": "fhir_error",
            "status_code": self.status_code,
            "message": str(self),
            "detail": self.detail,
        }


__all__ = ["FHIRClient", "FHIRError", "DEFAULT_TIMEOUT"]
