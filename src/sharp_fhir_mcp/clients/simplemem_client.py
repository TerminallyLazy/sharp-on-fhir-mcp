"""SimpleMem MCP client for persistent clinical memory across sessions.

SimpleMem provides hybrid (semantic + lexical + symbolic) memory storage.
This client speaks the SimpleMem MCP JSON-RPC dialect and is used by the
optional ``memory.*`` tools. It is **completely optional** — if the
``SIMPLEMEM_API_URL`` and ``SIMPLEMEM_ACCESS_TOKEN`` environment variables
aren't set, the memory tools simply return a configured=false marker.

NOTE: Patient identifiers are FHIR resource ids (strings), per the SHARP
context model — not vendor-specific integer ids.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

import httpx


class SimpleMemClient:
    """Optional client for the SimpleMem cloud memory service."""

    def __init__(self, api_url: str, access_token: str) -> None:
        self.api_url = api_url.rstrip("/")
        self.access_token = access_token
        self._client: httpx.AsyncClient | None = None

    @classmethod
    def from_env(cls) -> "SimpleMemClient | None":
        """Build a client from ``SIMPLEMEM_API_URL`` / ``SIMPLEMEM_ACCESS_TOKEN``."""
        url = os.getenv("SIMPLEMEM_API_URL")
        token = os.getenv("SIMPLEMEM_ACCESS_TOKEN")
        if not url or not token:
            return None
        return cls(api_url=url, access_token=token)

    @property
    def is_configured(self) -> bool:
        return bool(self.api_url and self.access_token)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers={
                    "Authorization": f"Bearer {self.access_token}",
                    "Content-Type": "application/json",
                },
                timeout=30.0,
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def _call_tool(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """Invoke a SimpleMem MCP tool over JSON-RPC."""
        client = await self._get_client()
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }
        response = await client.post(self.api_url, json=payload)
        response.raise_for_status()
        result = response.json()
        if "error" in result:
            raise RuntimeError(f"SimpleMem error: {result['error']}")
        return result.get("result", {})

    # --
    # Generic memory ops
    # --

    async def store_memory(
        self,
        content: str,
        speaker: str = "system",
        timestamp: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if timestamp is None:
            timestamp = datetime.now().isoformat()
        memory_content = content
        if metadata:
            meta_str = " | ".join(f"{k}:{v}" for k, v in metadata.items())
            memory_content = f"[{meta_str}] {content}"
        return await self._call_tool(
            "add_dialogue",
            {
                "speaker": speaker,
                "content": memory_content,
                "timestamp": timestamp,
            },
        )

    async def search_memories(
        self, query: str, limit: int = 10
    ) -> dict[str, Any]:
        return await self._call_tool("search", {"query": query, "limit": limit})

    async def get_recent_memories(self, limit: int = 20) -> dict[str, Any]:
        return await self._call_tool("get_recent", {"limit": limit})

    # --
    # Clinical helpers (FHIR-flavored)
    # --

    async def store_patient_encounter(
        self,
        patient_id: str,
        patient_name: str,
        encounter_summary: str,
        visit_date: str,
        practitioner_name: str | None = None,
        chief_complaint: str | None = None,
        diagnosis: list[str] | None = None,
        plan: str | None = None,
    ) -> dict[str, Any]:
        parts = [
            f"Patient Encounter: {patient_name} (FHIR Patient/{patient_id})",
            f"Date: {visit_date}",
        ]
        if practitioner_name:
            parts.append(f"Provider: {practitioner_name}")
        if chief_complaint:
            parts.append(f"Chief Complaint: {chief_complaint}")
        if diagnosis:
            parts.append(f"Diagnosis: {', '.join(diagnosis)}")
        if plan:
            parts.append(f"Plan: {plan}")
        parts.append(f"Summary: {encounter_summary}")
        content = "\n".join(parts)
        return await self.store_memory(
            content=content,
            speaker="clinical_system",
            metadata={
                "patient_id": patient_id,
                "visit_date": visit_date,
                "type": "encounter",
            },
        )

    async def store_clinical_alert(
        self,
        patient_id: str,
        patient_name: str,
        alert_type: str,
        alert_content: str,
        severity: str = "info",
    ) -> dict[str, Any]:
        content = (
            f"CLINICAL ALERT [{severity.upper()}] - {patient_name} "
            f"(FHIR Patient/{patient_id})\n"
            f"Type: {alert_type}\n"
            f"Details: {alert_content}"
        )
        return await self.store_memory(
            content=content,
            speaker="alert_system",
            metadata={
                "patient_id": patient_id,
                "alert_type": alert_type,
                "severity": severity,
                "type": "alert",
            },
        )

    async def get_patient_memories(
        self,
        patient_id: str,
        patient_name: str = "",
        limit: int = 20,
    ) -> dict[str, Any]:
        query = f"patient_id:{patient_id} {patient_name}".strip()
        return await self.search_memories(query, limit)


__all__ = ["SimpleMemClient"]
