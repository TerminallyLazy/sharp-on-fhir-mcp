# sharp-fhir-mcp

> A clean-room **[SHARP-on-MCP](https://www.sharponmcp.com)** compliant FHIR R4
> MCP server with interactive **[MCP-UI](https://github.com/idosal/mcp-ui)** clinical dashboards.

Built for the **Prompt Opinion "Build the Future of Healthcare AI" Hackathon** — a
vendor-neutral MCP server that any SMART-on-FHIR app, agent, or LLM host can
plug into without server-side OAuth, API keys, or proprietary auth flows.

---

## Why SHARP?

The [SHARP](https://www.sharponmcp.com/overview.html) (Standardised Healthcare
Agent Remote Protocol) spec describes a **headers-based context model** for MCP
servers in healthcare:

| Header                  | Purpose                                       |
| ----------------------- | --------------------------------------------- |
| `X-FHIR-Server-URL`     | Base URL of the patient's FHIR R4 endpoint    |
| `X-FHIR-Access-Token`   | Bearer token already minted by the agent host |
| `X-Patient-ID`          | Optional default `Patient` resource id        |

Per SHARP §3.2, **the MCP server never runs an OAuth dance itself**. The agent
host (e.g. a SMART-on-FHIR launch container) obtains the token and forwards it
on every call. This means a single deployment of this server works against
**Epic, Cerner, MEDITECH, athenahealth, eClinicalWorks, ConnectEHR, HAPI**, or
any other FHIR R4 endpoint — there's nothing vendor-specific.

The server advertises `capabilities.experimental.fhir_context_required = true`
on every initialise response so SHARP-aware clients know to forward those
headers automatically.

---

## What's included

### 🩺 Clinical FHIR tools

* `fhir_get_capability_statement` — discover the connected FHIR server
* `fhir_get_patient`, `fhir_search`, `fhir_read`, `fhir_patient_everything` — generic R4 access
* `clinical_search_patients`, `clinical_get_patient_summary`
* `clinical_get_appointments`, `clinical_get_encounters`
* `clinical_get_problems`, `clinical_get_medications`, `clinical_get_allergies`, `clinical_get_immunizations`
* `clinical_get_health_record` — one-shot consolidated record
* `clinical_get_context` — full visit context (demographics + allergies + meds + problems + labs + vitals + encounters + alerts) in parallel

### 🔬 Labs, vitals & imaging

* `lab_get_results`, `lab_get_vital_signs`, `lab_get_diagnostic_reports`
* `imaging_get_documents` — DocumentReference search

### 🧠 Optional persistent multimodal memory (OmniSimpleMem)

Backed by [OmniSimpleMem](https://github.com/aiming-lab/SimpleMem/tree/main/OmniSimpleMem)
— hybrid (FAISS + BM25) retrieval, pyramid expansion (preview → details →
evidence), cross-modal knowledge graph, and four-modality ingestion. The
shipped `docker-compose.yml` runs the OmniSimpleMem REST server alongside
this server.

Available when `OMNIMEM_API_URL` is set (defaults to `http://omnimem:8000`
under docker-compose):

**Text**
* `memory_store_encounter` — visit summary with diagnoses, plan, complaint
* `memory_store_alert` — persistent clinical flag (allergy, drug interaction, …)
* `memory_store_note` — free-text note

**Multimodal**
* `memory_store_image` — radiology film, dermatology photo, ECG strip image
* `memory_store_audio` — consult recording, dictation, heart sound
* `memory_store_video` — procedure video, ultrasound clip, gait recording

**Retrieval**
* `memory_search_history` — hybrid retrieval scoped to current patient
* `memory_get_patient_history` — recent memories for the patient
* `memory_answer_question` — RAG answer grounded in patient memory
* `memory_expand` — expand specific MAUs to evidence level
* `memory_stats` — system statistics

### 📊 MCP-UI visualisations

* `visualize_lab_trend` — Chart.js line chart of one lab over time
* `visualize_vitals` — multi-chart vitals dashboard
* `visualize_patient_dashboard` — full HTML clinical page (demographics, alerts, allergies, meds, problems, labs, encounters, immunisations + Chart.js trends)

All visual tools return MCP-UI `ui://` resources that the host renders in its inspector pane.

---

## Quickstart

### 1. Install

```bash
git clone https://github.com/your-org/sharp-fhir-mcp.git
cd sharp-fhir-mcp
pip install -e .
```

### 2. Run the server

```bash
sharp-fhir-mcp                     # http (streamable) on 0.0.0.0:8000
sharp-fhir-mcp --port 9000         # custom port
SHARP_STRICT_CONTEXT=1 sharp-fhir-mcp   # reject calls missing FHIR headers
```

The MCP endpoint is `http://localhost:8000/mcp/`.

For the full stack (FHIR MCP + OmniSimpleMem multimodal memory):

```bash
cp .env.example .env                 # set OPENAI_API_KEY (or OPENAI_API_BASE)
docker compose up --build            # both services
docker compose --profile no-memory up    # FHIR-only, no memory backend
```

> **Note:** `localhost` here refers to localhost of the machine where you are
> running the server. To access it remotely, deploy the server (see below) or
> port-forward to your local instance.

### 3. Connect from any SHARP-aware MCP client

Send these headers on every JSON-RPC request:

```http
X-FHIR-Server-URL: https://hapi.fhir.org/baseR4
X-FHIR-Access-Token: <bearer token from your SMART launch>
X-Patient-ID: 12345          # optional
```

### 4. Try a public sandbox without writing a SMART app

The HAPI public FHIR R4 sandbox is read-only and **does not require auth** —
useful for kicking the tires:

```bash
curl -X POST http://localhost:8000/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -H 'X-FHIR-Server-URL: https://hapi.fhir.org/baseR4' \
  -H 'X-FHIR-Access-Token: anonymous' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

---

## Deployment

### Vercel (Python serverless)

This server runs as a stateless Streamable-HTTP endpoint, which works on
Vercel out of the box. You can re-use an existing
[Next.js MCP scaffold](https://model-context-protocol-mcp-with-nex-indol-ten.vercel.app/)
by either:

1. **Adding the Python ASGI handler** — drop the `app` Starlette instance
   into `api/index.py`:

   ```python
   # api/index.py
   from sharp_fhir_mcp.server import app  # noqa: F401
   ```

   plus a minimal `vercel.json`:

   ```json
   {
     "builds": [{"src": "api/index.py", "use": "@vercel/python"}],
     "routes": [{"src": "/(.*)", "dest": "api/index.py"}]
   }
   ```

2. **Or running it as a sidecar** behind your existing Vercel front-end and
   reverse-proxying `/mcp` to a longer-lived host (Fly.io, Railway, Render).

The server respects the Vercel-injected `PORT` environment variable.

### Local development

```bash
cp .env.example .env             # set FHIR_SERVER_URL etc. for fallbacks
sharp-fhir-mcp                   # http://localhost:8000/mcp
```

### Docker / docker-compose

A two-service stack ships in this repo:

| Service          | Image / build                                | Purpose |
| ---------------- | -------------------------------------------- | ------- |
| `sharp-fhir-mcp` | `Dockerfile` (repo root)                     | The FHIR MCP server (this repo) |
| `omnimem`        | `docker/omnimem/Dockerfile`                  | OmniSimpleMem REST backend      |

```bash
cp .env.example .env
# At a minimum set OPENAI_API_KEY (or OPENAI_API_BASE for an OpenAI-compat
# provider — OpenRouter, Ollama OpenAI-mode, vLLM, LM Studio, Together).
docker compose up --build
```

* MCP endpoint: `http://localhost:8000/mcp/`
* OmniSimpleMem REST docs (debug): `http://localhost:8001/docs`
* Persistent memory volume: `omnimem_data`
* Shared media dir for ingestion: `./shared` → `/shared` in both containers

#### Local embeddings (no OpenAI for vectors)

The default `EMBEDDING_MODEL_NAME=all-MiniLM-L6-v2` runs locally via
`sentence-transformers` (no API call). Override with any HuggingFace model
plus matching `EMBEDDING_DIM`:

```bash
EMBEDDING_MODEL_NAME=BAAI/bge-base-en-v1.5
EMBEDDING_DIM=768
```

LLM operations (summary / answer / caption) still need an OpenAI-compat
endpoint — point `OPENAI_API_BASE` at Ollama (`http://host.docker.internal:11434/v1`)
to keep everything local.

> ⚠️ **Upstream caveat:** OmniSimpleMem's `omni_memory/core/config.py` was
> not committed to the public repo at the time of writing. The
> `docker/omnimem/Dockerfile` vendors a faithful shim
> (`docker/omnimem/config_shim.py`) when upstream is missing the file —
> remove the shim once the upstream package is complete.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  MCP Client / Agent / LLM host (Claude, Cursor, custom)     │
│  • Knows the patient's FHIR endpoint + access token         │
│  • Sends X-FHIR-Server-URL, X-FHIR-Access-Token headers     │
└────────────────────────┬────────────────────────────────────┘
                         │ Streamable HTTP (SHARP-on-MCP)
            POST /mcp + JSON-RPC + SHARP headers
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  sharp-fhir-mcp                                             │
│                                                             │
│  ┌────────────────────────────────────────────────────────┐ │
│  │ SharpContextMiddleware                                 │ │
│  │ • Parses X-FHIR-Server-URL / X-FHIR-Access-Token       │ │
│  │ • Stores in ContextVar for the request scope           │ │
│  └─────────────────────────┬──────────────────────────────┘ │
│                            ▼                                │
│  ┌────────────────────────────────────────────────────────┐ │
│  │ FastMCP tool registry                                  │ │
│  │ ├─ fhir_*           (generic R4 search/read)           │ │
│  │ ├─ clinical_*       (patient/encounter/medication/…)   │ │
│  │ ├─ lab_* / imaging_*(observations, reports, docs)      │ │
│  │ ├─ memory_*         (optional OmniSimpleMem multimodal)│ │
│  │ └─ visualize_*      (MCP-UI Chart.js dashboards)       │ │
│  └─────────────────────────┬──────────────────────────────┘ │
│                            ▼                                │
│  ┌────────────────────────────────────────────────────────┐ │
│  │ Vendor-neutral FHIR R4 client (httpx, async)           │ │
│  └─────────────────────────┬──────────────────────────────┘ │
└────────────────────────────┼────────────────────────────────┘
                             ▼
            FHIR R4 server (Epic / Cerner / HAPI / …)
```

See [`CLAUDE.md`](./CLAUDE.md) for detailed module-by-module notes and the
SHARP compliance check-list.

---

## SHARP compliance check-list

| Requirement                                                   | Status |
| ------------------------------------------------------------- | :----: |
| Streamable-HTTP transport (stdio not in scope)                | ✅ |
| Read FHIR endpoint from `X-FHIR-Server-URL` header            | ✅ |
| Read bearer token from `X-FHIR-Access-Token` header           | ✅ |
| Optional `X-Patient-ID` header for default patient context    | ✅ |
| Advertise `capabilities.experimental.fhir_context_required`   | ✅ |
| No server-side OAuth / token storage                          | ✅ |
| Vendor-neutral FHIR R4 client                                 | ✅ |
| Structured `fhir_context_required` errors when headers absent | ✅ |
| Optional strict context enforcement (`SHARP_STRICT_CONTEXT=1`) | ✅ |

---

## License

MIT — see `LICENSE`.
