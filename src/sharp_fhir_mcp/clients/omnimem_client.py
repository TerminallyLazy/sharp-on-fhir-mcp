"""OmniSimpleMem REST client for multimodal clinical memory.

OmniSimpleMem (https://github.com/aiming-lab/SimpleMem/tree/main/OmniSimpleMem)
is a multimodal lifelong memory backend (text, image, audio, video) with
hybrid dense+sparse retrieval and a cross-modal knowledge graph. We talk to it
via its bundled FastAPI REST server (``examples/api_server.py`` →
``omni_memory.app:app``). Endpoints used:

    POST /memory/text     - Store text memory.
    POST /memory/image    - Store image memory (multipart).
    POST /memory/audio    - Store audio memory (multipart).
    POST /memory/video    - Store video memory (multipart).
    POST /query           - Hybrid retrieval with token budgeting.
    POST /answer          - RAG answer generation.
    POST /expand          - Expand MAUs to evidence level.
    GET  /events          - Recent session events.
    GET  /events/{id}     - Event detail.
    GET  /mau/{id}        - MAU detail.
    GET  /stats           - System stats.
    GET  /health          - Liveness.

Configuration (env):
    OMNIMEM_API_URL       Base URL (e.g. http://omnimem:8000). Required.
    OMNIMEM_API_TOKEN     Optional bearer token (if you front the service
                          with a reverse proxy that requires auth).

Patient identifiers are FHIR resource ids (strings). We tag every stored
memory with ``patient_id:<id>`` so retrieval can scope to one patient.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx


def _patient_tags(patient_id: str, extra: list[str] | None = None) -> list[str]:
    tags = [f"patient_id:{patient_id}"]
    if extra:
        tags.extend(extra)
    return tags


class OmniMemError(RuntimeError):
    """Raised when the OmniSimpleMem REST API returns an error."""

    def __init__(self, message: str, *, status_code: int | None = None,
                 detail: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail


class OmniMemClient:
    """Async REST client for an OmniSimpleMem server."""

    def __init__(self, api_url: str, access_token: str | None = None) -> None:
        self.api_url = api_url.rstrip("/")
        self.access_token = access_token
        self._client: httpx.AsyncClient | None = None

    # --
    # Lifecycle
    # --

    @classmethod
    def from_env(cls) -> "OmniMemClient | None":
        url = os.getenv("OMNIMEM_API_URL")
        if not url:
            return None
        token = os.getenv("OMNIMEM_API_TOKEN")
        return cls(api_url=url, access_token=token)

    @property
    def is_configured(self) -> bool:
        return bool(self.api_url)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers = {"Accept": "application/json"}
            if self.access_token:
                headers["Authorization"] = f"Bearer {self.access_token}"
            self._client = httpx.AsyncClient(
                base_url=self.api_url,
                headers=headers,
                timeout=120.0,
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "OmniMemClient":
        await self._get_client()
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        await self.close()

    # --
    # Low-level
    # --

    async def _post_json(self, path: str, payload: dict) -> dict:
        client = await self._get_client()
        r = await client.post(path, json=payload)
        return self._unwrap(r)

    async def _post_file(self, path: str, files: dict, data: dict | None = None) -> dict:
        client = await self._get_client()
        r = await client.post(path, files=files, data=data or {})
        return self._unwrap(r)

    async def _get(self, path: str, params: dict | None = None) -> dict:
        client = await self._get_client()
        r = await client.get(path, params=params)
        return self._unwrap(r)

    @staticmethod
    def _unwrap(r: httpx.Response) -> dict:
        if r.status_code >= 400:
            try:
                detail = r.json()
            except Exception:  # noqa: BLE001
                detail = r.text
            raise OmniMemError(
                f"OmniSimpleMem {r.request.method} {r.request.url.path} "
                f"failed: HTTP {r.status_code}",
                status_code=r.status_code,
                detail=detail,
            )
        try:
            return r.json()
        except Exception:  # noqa: BLE001
            return {"raw": r.text}

    # --
    # Health / stats / sessions
    # --

    async def health(self) -> dict:
        return await self._get("/health")

    async def stats(self) -> dict:
        return await self._get("/stats")

    async def start_session(self, session_id: str | None = None) -> dict:
        params = {"session_id": session_id} if session_id else None
        client = await self._get_client()
        r = await client.post("/session/start", params=params)
        return self._unwrap(r)

    async def end_session(self) -> dict:
        client = await self._get_client()
        r = await client.post("/session/end")
        return self._unwrap(r)

    # --
    # Memory addition
    # --

    async def add_text(
        self,
        text: str,
        *,
        session_id: str | None = None,
        tags: list[str] | None = None,
        force: bool = False,
    ) -> dict:
        return await self._post_json(
            "/memory/text",
            {
                "text": text,
                "session_id": session_id,
                "tags": tags,
                "force": force,
            },
        )

    async def add_image(
        self,
        image_path: str | Path,
        *,
        session_id: str | None = None,
        tags: list[str] | None = None,
        force: bool = False,
    ) -> dict:
        path = Path(image_path)
        files = {"image": (path.name, path.read_bytes())}
        data: dict[str, Any] = {"force": str(force).lower()}
        if session_id:
            data["session_id"] = session_id
        if tags:
            data["tags"] = ",".join(tags)
        return await self._post_file("/memory/image", files=files, data=data)

    async def add_audio(
        self,
        audio_path: str | Path,
        *,
        session_id: str | None = None,
        tags: list[str] | None = None,
        force: bool = False,
    ) -> dict:
        path = Path(audio_path)
        files = {"audio": (path.name, path.read_bytes())}
        data: dict[str, Any] = {"force": str(force).lower()}
        if session_id:
            data["session_id"] = session_id
        if tags:
            data["tags"] = ",".join(tags)
        return await self._post_file("/memory/audio", files=files, data=data)

    async def add_video(
        self,
        video_path: str | Path,
        *,
        session_id: str | None = None,
        tags: list[str] | None = None,
        max_frames: int = 100,
    ) -> dict:
        path = Path(video_path)
        files = {"video": (path.name, path.read_bytes())}
        data: dict[str, Any] = {"max_frames": str(max_frames)}
        if session_id:
            data["session_id"] = session_id
        if tags:
            data["tags"] = ",".join(tags)
        return await self._post_file("/memory/video", files=files, data=data)

    # --
    # Retrieval
    # --

    async def query(
        self,
        query: str,
        *,
        top_k: int = 10,
        auto_expand: bool = False,
        token_budget: int | None = None,
    ) -> dict:
        return await self._post_json(
            "/query",
            {
                "query": query,
                "top_k": top_k,
                "auto_expand": auto_expand,
                "token_budget": token_budget,
            },
        )

    async def answer(
        self,
        question: str,
        *,
        top_k: int = 10,
        include_sources: bool = True,
    ) -> dict:
        return await self._post_json(
            "/answer",
            {
                "question": question,
                "top_k": top_k,
                "include_sources": include_sources,
            },
        )

    async def expand(self, mau_ids: list[str], level: str = "evidence") -> dict:
        return await self._post_json(
            "/expand", {"mau_ids": mau_ids, "level": level}
        )

    async def get_events(
        self, *, session_id: str | None = None, limit: int = 10
    ) -> dict:
        params: dict[str, Any] = {"limit": limit}
        if session_id:
            params["session_id"] = session_id
        return await self._get("/events", params=params)

    async def get_event_details(self, event_id: str, *, level: str = "details") -> dict:
        return await self._get(f"/events/{event_id}", params={"level": level})

    async def get_mau(self, mau_id: str) -> dict:
        return await self._get(f"/mau/{mau_id}")

    # --
    # Clinical helpers (FHIR-flavoured tagging)
    # --

    async def store_patient_encounter(
        self,
        *,
        patient_id: str,
        patient_name: str,
        encounter_summary: str,
        visit_date: str,
        practitioner_name: str | None = None,
        chief_complaint: str | None = None,
        diagnosis: list[str] | None = None,
        plan: str | None = None,
    ) -> dict:
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
        return await self.add_text(
            "\n".join(parts),
            session_id=patient_id,
            tags=_patient_tags(patient_id, [f"visit_date:{visit_date}", "type:encounter"]),
        )

    async def store_clinical_alert(
        self,
        *,
        patient_id: str,
        patient_name: str,
        alert_type: str,
        alert_content: str,
        severity: str = "info",
    ) -> dict:
        text = (
            f"CLINICAL ALERT [{severity.upper()}] - {patient_name} "
            f"(FHIR Patient/{patient_id})\n"
            f"Type: {alert_type}\n"
            f"Details: {alert_content}\n"
            f"Recorded: {datetime.now().isoformat()}"
        )
        return await self.add_text(
            text,
            session_id=patient_id,
            tags=_patient_tags(
                patient_id,
                [f"alert_type:{alert_type}", f"severity:{severity}", "type:alert"],
            ),
        )

    async def search_patient_memory(
        self, *, patient_id: str, query: str, top_k: int = 10
    ) -> dict:
        # Scope by piping the patient_id tag into the query string — the
        # hybrid BM25 sparse retriever keys on tag tokens.
        scoped = f"patient_id:{patient_id} {query}".strip()
        return await self.query(scoped, top_k=top_k, auto_expand=True)

    async def get_patient_history(
        self, *, patient_id: str, top_k: int = 20
    ) -> dict:
        return await self.query(
            f"patient_id:{patient_id}",
            top_k=top_k,
            auto_expand=True,
        )


__all__ = ["OmniMemClient", "OmniMemError"]
