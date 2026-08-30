# The Lenny Growth Assistant

A full-stack, AI-powered conversational web app that answers product & growth questions
**grounded in Lenny's Podcast transcripts** (with citations), turns answers into
**Ship-30-style essays**, and generates **Markdown/HTML artifacts rendered safely beside the
chat** — with a one-env-var toggle between a **cloud** model (OpenAI) and a **local** model (Ollama).

> Built as a Forward-Deployed-Engineer take-home. See [`PRD.md`](PRD.md),
> [`design.md`](design.md), [`architecture.md`](architecture.md), and
> [`agent-transcripts/`](agent-transcripts/) for the discovery brief, design, architecture,
> and build log.

---

## What it does

- **Grounded RAG chat** over Lenny's transcripts — streams answers, cites sources, keeps
  per-session context, and honestly says when the transcripts don't cover a question.
- **Ship-30 essay skill** — `/essay <topic>` produces a ~1,250-word essay following the
  Ship 30 for 30 method (hook, skimmable subheads, Golden Intersection, concrete takeaway).
- **Artifact viewer** — `/artifact <request>` generates Markdown or self-contained HTML rendered
  in a **sandboxed iframe + strict CSP + DOMPurify**, with a "permits/blocks" security panel.
- **Model toggle** — `LLM_PROVIDER=cloud|local` switches provider with **no code change**.

## Architecture (one-glance)

```
React/Vite SPA ──REST + SSE──> FastAPI
                                 ├─ Agent layer (Claude Agent SDK ── via ──┐
                                 │   router · retrieve/essay/artifact       │
                                 ├─ LLM gateway (LiteLLM) ──────────────────┤
                                 ├─ RAG (chunk · embed · pgvector retrieve)  │
                                 └─ Postgres + pgvector                      │
                                                                            ▼
                                            LiteLLM proxy (Anthropic /v1/messages)
                                                     ├─ cloud → OpenAI
                                                     └─ local → Ollama (llama3.1:8b)
                              Embeddings: nomic-embed-text (Ollama) — one vector space
```

Full detail in [`architecture.md`](architecture.md).

---

## Prerequisites

- **Docker Desktop** (Compose v2) — the one-command path.
- ~6 GB free disk for the local model (`llama3.1:8b` + `nomic-embed-text`).
- Optional: an **OpenAI API key** to use the cloud provider (the local Ollama path needs no key).

Native (no-Docker) dev additionally needs: `uv`, Node 20+, a local Postgres with `pgvector`,
and a local Ollama.

## Quickstart (Docker)

```bash
git clone <this-repo> && cd oogway
cp .env.example .env            # defaults run fully local (Ollama); no secrets needed

docker compose up --build       # starts db, ollama, litellm, api, web
# first run pulls the models (several GB) — watch: docker compose logs -f ollama-pull

# Load the knowledge base (once). Wait until ollama-pull has finished first.
docker compose run --rm api python -m app.rag.ingest

# open the app
open http://localhost:8080      # API docs at http://localhost:8000/docs
```

`make` shortcuts: `make up`, `make ingest`, `make test`, `make logs`, `make down`.

## Run natively (without Docker)

Prefer the Docker path above for a clean one-command start. To run everything on the host
instead (faster iteration), here's the macOS / Homebrew path.

**Prerequisites:** `uv`, Node 20+, PostgreSQL 16 + pgvector, and Ollama.

**1. Postgres + pgvector (one time)**
```bash
brew install postgresql@16 pgvector
brew services start postgresql@16

# role + database the app expects (ignore "already exists")
psql postgres -c "CREATE ROLE lenny LOGIN SUPERUSER PASSWORD 'lenny';"
createdb -O lenny lenny
```
If `CREATE EXTENSION vector` later errors with *"extension 'vector' is not available"*, Homebrew
built pgvector for a different Postgres major version. Build it against PG16 explicitly:
```bash
git clone --branch v0.8.0 https://github.com/pgvector/pgvector.git /tmp/pgvector
make -C /tmp/pgvector install PG_CONFIG=/opt/homebrew/opt/postgresql@16/bin/pg_config
```

**2. Ollama + models (one time)**
```bash
brew install ollama
brew services start ollama
ollama pull llama3.1:8b        # local chat model
ollama pull nomic-embed-text   # embeddings — used for retrieval in BOTH cloud and local modes
```

**3. Backend config** — create `backend/.env` (git-ignored) with localhost hosts:
```dotenv
APP_ENV=local
LLM_PROVIDER=local            # or "cloud" (needs OPENAI_API_KEY below)
AGENT_BACKEND=litellm         # direct path; no LiteLLM proxy needed natively
OPENAI_API_KEY=               # only required for cloud
OPENAI_MODEL=gpt-4o-mini
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=llama3.1:8b
EMBED_MODEL=nomic-embed-text
EMBED_DIM=768
DATABASE_URL=postgresql://lenny:lenny@localhost:5432/lenny
```

**4. Load the knowledge base (once)**
```bash
cd backend
uv run --python 3.12 python -m app.rag.ingest   # clones transcripts, embeds ~50 episodes
```
> Run ingest **once, not concurrently** — it truncates + reloads the table, so two parallel runs
> clobber each other.

**5. Run the two servers**
```bash
# Terminal 1 — API
cd backend && uv run --python 3.12 uvicorn app.main:app --reload --port 8000

# Terminal 2 — web (Vite may pick 5173/5174/… — CORS allows any localhost port in local dev)
cd frontend && npm install && npm run dev
```
Open the printed URL (e.g. http://localhost:5173). Verify the API:
```bash
curl -s localhost:8000/health   # "status":"ok" with checks.db.chunks > 0
```

**Notes**
- `AGENT_BACKEND=litellm` is the simplest native path (no proxy). To exercise the Claude Agent SDK
  path, set `AGENT_BACKEND=agent_sdk` and run the proxy in a third terminal:
  `uv run --python 3.12 litellm --config ../litellm/config.yaml --port 4000`.
- **Keep Ollama running even in cloud mode** — embeddings always use `nomic-embed-text` locally, so
  retrieval needs it regardless of the chat provider.
- Switch models live from the **header dropdown** (no restart), or set `LLM_PROVIDER` in
  `backend/.env` and restart the API.
- `uv` will fetch Python 3.12 automatically; if not, run `uv python install 3.12`.

## Switching to the cloud model (OpenAI)

In `.env`:

```dotenv
LLM_PROVIDER=cloud
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini      # or gpt-4.1
```

Restart the stack (`docker compose up -d`). The UI badge flips to green/OpenAI. Embeddings
stay on `nomic-embed-text` (Ollama) in both modes so the vector index never needs rebuilding.

## Agent backend (the mandated Claude Agent SDK)

`AGENT_BACKEND` selects how the agent loop runs:

- `agent_sdk` (default) — the **Claude Agent SDK** drives the loop and calls a retrieval MCP
  tool; it's pointed at the LiteLLM proxy (`ANTHROPIC_BASE_URL`) so it runs on OpenAI/Ollama.
  If the SDK path errors, the app **automatically falls back** to the direct path.
- `litellm` — the direct, provider-agnostic path (same skills/retrieval). Set this for the most
  predictable demo.

## Environment variables

| Var | Default | Purpose |
|---|---|---|
| `LLM_PROVIDER` | `local` | `cloud` (OpenAI) or `local` (Ollama) |
| `AGENT_BACKEND` | `agent_sdk` | `agent_sdk` (Claude Agent SDK) or `litellm` (direct) |
| `OPENAI_API_KEY` | — | required only for `cloud` |
| `OPENAI_MODEL` | `gpt-4o-mini` | cloud chat model |
| `OLLAMA_URL` / `OLLAMA_MODEL` | `http://ollama:11434` / `llama3.1:8b` | local chat model |
| `EMBED_MODEL` / `EMBED_DIM` | `nomic-embed-text` / `768` | embeddings (fixed across providers) |
| `LITELLM_PROXY_URL` / `LITELLM_MASTER_KEY` | `http://litellm:4000` / `sk-lenny-local-dev` | Agent-SDK proxy |
| `DATABASE_URL` | `postgresql://lenny:lenny@db:5432/lenny` | Postgres + pgvector |
| `RETRIEVAL_TOP_K` / `RETRIEVAL_MIN_SCORE` | `6` / `0.25` | retrieval + grounding floor |
| `INGEST_MAX_EPISODES` | `50` | cap for a fast demo ingest (`0` = all) |

Never commit `.env`. `.env.example` documents every variable with safe defaults.

## Tests

See [`TESTING.md`](TESTING.md). Quick version:

```bash
make test     # full suite in Docker (Postgres + all deps)
# or, dependency-free unit tests locally:
cd backend && uvx --with pytest-asyncio pytest tests/test_chunking.py \
  tests/test_router.py tests/test_sanitize.py tests/test_artifact_parse.py -q
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| Chat errors / empty answers on first run | `ollama-pull` hasn't finished pulling models — `docker compose logs -f ollama-pull`, then retry. |
| "transcripts don't cover it" for everything | You haven't ingested — run `make ingest` (after models are pulled). |
| `/health` shows `db down` | Postgres still starting; it has a healthcheck — wait a few seconds and refresh. |
| Cloud mode fails | Check `OPENAI_API_KEY`; if missing, the app logs a warning and falls back to local. |
| Agent-SDK path misbehaves | Set `AGENT_BACKEND=litellm` for the direct path (identical skills). |
| Ports busy | Change host ports in `docker-compose.yml` (`8080`, `8000`, `5432`, `11434`, `4000`). |

## Project structure

```
backend/    FastAPI app, agent layer, RAG, LiteLLM gateway, tests, .claude/skills
frontend/   React/Vite SPA (chat + sandboxed artifact viewer)
litellm/    proxy config (Anthropic passthrough → OpenAI/Ollama)
db/         pgvector init.sql
docker-compose.yml, Makefile, .env.example
PRD.md · design.md · architecture.md · TESTING.md · BUILD_PLAN.md
agent-transcripts/   build log incl. failed attempts + fixes
```

## Deliverables map

| Deliverable | Where |
|---|---|
| Source (no secrets) | this repo |
| README | this file |
| PRD | [`PRD.md`](PRD.md) |
| Design | [`design.md`](design.md) |
| Architecture | [`architecture.md`](architecture.md) |
| Agent transcripts | [`agent-transcripts/`](agent-transcripts/) |
| Tests | `backend/tests/` + [`TESTING.md`](TESTING.md) |
| Demo video | link in submission form |

## Data & licensing

Knowledge base: [`ChatPRD/lennys-podcast-transcripts`](https://github.com/ChatPRD/lennys-podcast-transcripts)
(Markdown + YAML frontmatter). The transcripts have no formal license and are used here for an
**internal/educational demo only** — content belongs to Lenny's Podcast and the guests; do not
redistribute the raw files or use commercially.
