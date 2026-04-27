# CLAUDE.md — Architecture & Implementation Notes

This document describes the architecture of `sharp-fhir-mcp` for future
contributors (human or AI). It is intentionally code-focused: every module
has a paragraph and every important design decision has a one-line rationale.

---

## High-level

```
src/sharp_fhir_mcp/
├── __init__.py
├── server.py                  # FastMCP 2.x entry point + SHARP capability injection
├── context.py                 # SharpContext + FastMCP middleware (HTTP-header → ContextVar)
├── fhir_utils.py              # Pure functions normalising FHIR R4 → compact dicts
├── models/
│   └── types.py               # Literals/enums/typed dicts shared across tools
├── clients/
│   ├── fhir_client.py         # Vendor-neutral async FHIR R4 client
│   └── omnimem_client.py      # Optional multimodal-memory REST client (OmniSimpleMem)
├── tools/
│   ├── _helpers.py            # Functional helpers shared by tool modules
│   ├── fhir.py                # Generic FHIR search/read tools
│   ├── clinical.py            # Patient / Encounter / Appointment / Allergy / …
│   ├── lab_imaging.py         # Observation / DiagnosticReport / DocumentReference
│   ├── clinical_context.py    # Aggregated visit context with derived alerts
│   ├── memory.py              # OmniSimpleMem-backed multimodal memory (optional)
│   └── visualization.py       # MCP-UI Chart.js dashboards
└── ui/
    ├── clinical_charts.py     # Chart.js HTML builders
    └── clinical_display.py    # Clinical card / table HTML builders
```

---

## SHARP compliance — how each requirement is met

### Streamable HTTP transport (SHARP §"Scope")

`server.py` builds on **standalone FastMCP 2.x** (gofastmcp.com) — *not*
Anthropic's lower-level `mcp.server.fastmcp` SDK. FastMCP 2.x has proper
async-host integration (its CLI calls `run_async()` rather than
`asyncio.run()`), which is what hosted deployments expect.

The default transport is HTTP (Streamable). stdio remains available behind
`--transport stdio` for legacy local dev, but per SHARP §"Scope" it is not in
scope of the spec.

### Header-based context (SHARP §3.2)

`SharpContextMiddleware` (in `context.py`) is a **FastMCP middleware** (not
a Starlette HTTP middleware) — it reads HTTP headers off the active request
via `fastmcp.server.dependencies.get_http_headers()` and stores the resolved
context in a `ContextVar[SharpContext]`:

| Header                | Stored as            |
| --------------------- | -------------------- |
| `X-FHIR-Server-URL`   | `ctx.server_url`     |
| `X-FHIR-Access-Token` | `ctx.access_token`   |
| `X-Patient-ID`        | `ctx.patient_id`     |

Tools resolve the context with `get_current_context()` or directly via the
`fhir_client_for_current_context()` async context manager which returns a
configured `FHIRClient` for the request.

**Env-var fallback:** when no middleware ran (e.g. in tests or an in-process
stdio run), the helper falls back to `FHIR_SERVER_URL` /
`FHIR_ACCESS_TOKEN` / `PATIENT_ID` environment variables.

**Bearer prefix tolerance:** the middleware strips a leading `Bearer ` from
`X-FHIR-Access-Token` if the agent host accidentally included it.

### Strict vs permissive mode

* **Permissive (default):** the middleware always runs; tools that need FHIR
  context return a structured `{"error": "fhir_context_required", ...}`
  payload describing which headers were missing. This keeps tools that don't
  need context (e.g. introspection) usable without auth.
* **Strict:** when `SHARP_STRICT_CONTEXT=1` is set, the middleware's
  `on_call_tool` hook raises a `fastmcp.exceptions.ToolError` for any
  `tools/call` that arrives without FHIR context. Handshake calls
  (`initialize`, `tools/list`, `ping`) always pass through. Recommended for
  production.

### `fhir_context_required` capability

`server._patch_capabilities()` monkey-patches the inner low-level
`create_initialization_options` (resolved defensively across FastMCP attribute
names — `_mcp_server` / `_low_level_server` / `server`) so every initialise
response includes:

```json
{
  "capabilities": {
    "experimental": {
      "fhir_context_required": { "value": true }
    }
  }
}
```

This is how SHARP-aware MCP clients/agents discover that they must forward
the FHIR headers on every subsequent call.

---

## Tool-helper pattern (functional, not decorator-based)

`tools/_helpers.py` exposes:

```python
def check_fhir_context(*, require_patient: bool = False, patient_id: str | None = None) -> dict | None: ...
def resolve_patient_id(explicit: str | None) -> str | None: ...
```

Tools use them like:

```python
@mcp.tool
async def clinical_get_problems(patient_id: str | None = None, ...) -> dict:
    if (err := check_fhir_context(require_patient=True, patient_id=patient_id)) is not None:
        return err
    pid = resolve_patient_id(patient_id) or ""
    async with fhir_client_for_current_context() as fhir:
        bundle = await fhir.get_conditions(pid, ...)
    return {...}
```

**Why functional, not a decorator?** Decorators that wrap the tool body
break FastMCP's `inspect.signature(...)` JSON-schema generation — the agent
ends up seeing the wrapper's `*args, **kwargs` instead of the real argument
names. Keeping the helper as a plain function lets FastMCP build accurate
schemas while still keeping the boilerplate minimal.

---

## FHIR client (clients/fhir_client.py)

* `FHIRClient(base_url, access_token, extra_headers=...)` — async, httpx-based
* Supports `async with` for explicit lifecycle control
* `FHIRError` carries `status_code` + `detail` (FHIR `OperationOutcome` when present)
* Convenience accessors: `get_patient`, `get_observations`, `get_conditions`,
  `get_medication_requests`, `get_allergies`, `get_immunizations`,
  `get_diagnostic_reports`, `get_procedures`, `get_encounters`,
  `get_appointments`, `get_document_references`, `get_coverage`,
  `get_patient_everything`
* Vendor-neutral: it does not embed any Epic/Cerner/MEDITECH-specific quirks.
  Vendor-specific extensions (e.g. extra headers like `Epic-Client-ID`) can
  be supplied via `extra_headers` if a deployment needs them.

---

## fhir_utils.py — null-safe normalisers

Every helper is null-safe and never raises on partial / malformed FHIR
resources, because clinical data in the wild rarely conforms perfectly to
the spec.

The summarisers (`patient_summary`, `observation_summary`, `condition_summary`,
…) all produce dicts with stable, snake_case keys — these are what the UI
builders and tool responses consume. The `raw` FHIR resource is always
preserved verbatim too where useful (see `fhir_get_patient`).

---

## MCP-UI integration (`tools/visualization.py` + `ui/`)

Visualisations are returned as MCP-UI resources:

```python
from mcp_ui import RawHtmlContent, CreateUIResourceOptions, create_ui_resource

return {
    "content": [create_ui_resource(CreateUIResourceOptions(
        uri=f"ui://sharp-fhir-mcp/dashboard/{pid}/{int(time.time())}",
        content=RawHtmlContent(type="rawHtml", htmlString=html),
        encoding="text",
    ))],
    "patient_id": pid,
    ...
}
```

`ClinicalChartBuilder` (in `ui/clinical_charts.py`) emits self-contained
HTML snippets that pull Chart.js + the annotation plugin from a CDN —
no bundling required. `ClinicalDisplayBuilder` builds the surrounding cards.

---

## Memory (optional, multimodal)

`OmniMemClient.from_env()` returns `None` when `OMNIMEM_API_URL` is unset,
and `register_memory_tools(mcp, None)` is a no-op. This lets the rest of
the server run without any memory backend.

The client speaks the [OmniSimpleMem](https://github.com/aiming-lab/SimpleMem/tree/main/OmniSimpleMem)
REST API:

| Endpoint                   | Used by                                 |
| -------------------------- | --------------------------------------- |
| `POST /memory/text`        | `memory_store_encounter/alert/note`     |
| `POST /memory/image`       | `memory_store_image`                    |
| `POST /memory/audio`       | `memory_store_audio`                    |
| `POST /memory/video`       | `memory_store_video`                    |
| `POST /query`              | `memory_search_history`, `memory_get_patient_history` |
| `POST /answer`             | `memory_answer_question` (RAG)          |
| `POST /expand`             | `memory_expand` (preview → evidence)    |
| `GET  /stats`              | `memory_stats`                          |

Every stored item is tagged with `patient_id:<FHIR id>` plus a `type:` tag
(`encounter` / `alert` / `note` / `image` / `audio` / `video`). Search calls
re-inject the patient tag into the query string so OmniSimpleMem's BM25
sparse retriever scopes results to one patient.

**Multimodal ingestion path:** the agent host writes a media file into the
shared volume (`./shared` on the host → `/shared` in both containers), then
calls `memory_store_image` (or `_audio`/`_video`) with that path. The
OmniMem client streams the file as multipart form data; OmniSimpleMem then
runs CLIP scene-change detection (visual) or VAD silence-filtering (audio)
to drop redundant content before storage.

**Embeddings:** OmniSimpleMem defaults to local sentence-transformers
(`all-MiniLM-L6-v2`, 384-dim). LLM steps (summary / answer / caption) use
an OpenAI-compatible endpoint — set `OPENAI_API_BASE` to point at OpenRouter,
Ollama OpenAI-mode, vLLM, LM Studio, etc.

**Upstream caveat:** OmniSimpleMem's `omni_memory/core/config.py` was missing
on `main` at the time of writing. `docker/omnimem/Dockerfile` checks for the
file and copies in `docker/omnimem/config_shim.py` if absent. Remove the
shim once upstream ships the real config module.

---

## Adding a new tool

1. Pick the right module under `tools/`. New domains (e.g. care plans) get a
   new `tools/care_plans.py` file with `register_care_plan_tools(mcp)`.
2. Add an `async def your_tool(...) -> dict` decorated with `@mcp.tool`
   (no parens — fastmcp 2.x decorator form).
3. Inside the body:
   * Call `check_fhir_context(...)` first.
   * Resolve patient id with `resolve_patient_id(...)`.
   * Use `fhir_client_for_current_context()` to talk to FHIR.
   * Return a compact dict — the LLM should not need to parse raw FHIR.
4. Re-export the registration function from `tools/__init__.py`.
5. Wire it into `server.build_server()`.
6. Document the new tool in `README.md` under "What's included".

---

## Testing tips

* Use the [public HAPI sandbox](https://hapi.fhir.org/baseR4) for integration
  tests — it's anonymous-readable.
* For unit tests, the env-var fallback in `context.py` lets you run tools
  without spinning up the HTTP server: just set `FHIR_SERVER_URL` /
  `FHIR_ACCESS_TOKEN` / `PATIENT_ID`.
* For HTTP mocks, use `respx` (already in dev dependencies).

---

## SHARP-on-MCP spec links

* Overview: https://www.sharponmcp.com/overview.html
* MCP base spec: https://modelcontextprotocol.io
* MCP-UI: https://github.com/idosal/mcp-ui
