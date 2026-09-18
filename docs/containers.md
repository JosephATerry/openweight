# Local containers

Docker Compose runs FastAPI, the compiled React product application, and the
existing PostgreSQL/pgvector database. React assets are built into the API
image; no Node process or frontend service exists at runtime. The environment
does not bundle or launch a language model.

```text
host 127.0.0.1:8000 -> api (React assets + non-root FastAPI process)
                              |
                              v
                         postgres + pgvector
                              |
                         postgres_data volume
```

Compose also enables the mounted MCP Streamable HTTP endpoint at
`http://127.0.0.1:8000/mcp`; it uses the same `api` container and PostgreSQL
service and does not add an MCP-specific container.

## Prerequisites and configuration

Use Docker Engine with the Compose plugin. Create the local environment file
from the placeholder contract and replace the development password:

```bash
cp .env.example .env
```

`.env` is ignored by both Git and the Docker build context. Compose requires
`POSTGRES_DB`, `POSTGRES_USER`, and `POSTGRES_PASSWORD`; it does not bake them
into the image. `OPENWEIGHT_API_PORT` changes both the application port and
the loopback-published port. `POSTGRES_HOST_PORT` changes only the optional
loopback host port for PostgreSQL.

The image sets `OPENWEIGHT_API_HOST=0.0.0.0`, as required inside a container.
Native startup still defaults to `127.0.0.1`. A hosting platform can inject its
own host, port, database, backend, and build metadata without a Git checkout or
WSL-specific path.

## Build and run

```bash
docker compose build api
docker compose up -d postgres api
docker compose ps
docker compose logs --follow api
```

The multi-stage build uses pinned Node 22.23.2 only to run `npm ci` and build
the locked Vite application. The final image contains static assets, not the
Node runtime or `node_modules`. The API image installs
`requirements-container.txt`, which includes the normal
runtime requirements but pins PyTorch to its CPU wheel. It does not install
the isolated training environment or CUDA runtime. It copies application
source, policy documents, the two existing database setup scripts, and a
standard-library health probe. It does not copy model weights, Hugging Face
caches, adapters, checkpoints, MLflow state, training corpora, validation
data, or the Git repository.

The application runs as UID/GID 10001 (`openweight`), drops Linux
capabilities, enables `no-new-privileges`, and has no Docker socket, host home,
model cache, or source bind mount. PostgreSQL uses its official image defaults
and a named volume.

## Database initialization

On the first volume creation, the existing read-only init mount enables the
pgvector extension. Operations schema creation and fictional seed data remain
an explicit, repeatable project command:

```bash
docker compose run --rm api python scripts/setup_operations.py
docker compose run --rm api python scripts/setup_security.py
```

These scripts use idempotent table creation and fictional-data upserts. The
security bootstrap also runs the upstream LangGraph checkpoint migrations.
Keeping them explicit
prevents an API restart from silently resetting demonstration status changes.
It targets only the PostgreSQL instance described by the Compose environment.

Policy indexing is also explicit because it uses the existing Qwen embedding
model and may need Hugging Face network/cache access:

```bash
docker compose run --rm api python scripts/index_policy_corpus.py
```

It uses deterministic policy chunk IDs and upserts into the existing pgvector
store. Compose does not run this command, download the embedding model, or add
a model-cache mount. A clean environment supports health, readiness, service
metadata, and operations initialization without model execution; policy RAG
queries require the separate indexing step.

## Health and smoke checks

Docker uses `GET /healthz`, which checks only API process liveness. Compose
waits for PostgreSQL's `pg_isready` result before starting the API. After both
services are healthy, verify the dependency-aware endpoint and safe metadata:

```bash
curl --fail http://127.0.0.1:8000/healthz
curl --fail http://127.0.0.1:8000/readyz
curl --fail http://127.0.0.1:8000/v1/service-info
curl --fail http://127.0.0.1:8000/openapi.json >/dev/null
```

These checks do not call `/v1/agent/query`, load a model, or perform a write.
The product UI is available from `http://127.0.0.1:8000/`; explicit SPA routes
such as `/access-requests` can be refreshed directly without shadowing API or
MCP routes.

## Shutdown, persistence, and reset

Normal shutdown preserves the named PostgreSQL volume:

```bash
docker compose down
```

To deliberately reset only this Compose project's local database, first stop
the project and then explicitly remove its named volume:

```bash
docker compose down --volumes
```

The volume command is destructive and is not part of ordinary shutdown. Do
not use global Docker prune commands for this project.

## Backend and deployment portability

The image contains no GPT-OSS or Muse weights. Backend selection remains an
environment-driven application concern. A real agent query therefore requires
a separately available backend and, for policy RAG, indexed embeddings. The
explicit Hugging Face profile selects the public remote provider separately
from this local Compose posture.

The HTTP layer is otherwise stateless and does not require a durable container
filesystem. PostgreSQL is already addressed by hostname and can later become
an external managed database. The configurable bind and port support the live
Hugging Face Docker Space and the primary Azure Container Apps deployment,
subject to their different networking, secrets, state, and inference profiles.

Local Compose defaults to the process-local memory checkpoint backend. Set up
the security schema with `scripts/setup_security.py` and explicitly select
`OPENWEIGHT_CHECKPOINT_BACKEND=postgres` to exercise restart-durable LangGraph
checkpoints, approval sessions, and transactional effect idempotency. There is
no silent fallback if PostgreSQL durability is selected but unavailable.

## Troubleshooting

- `docker compose config` reports a missing variable: create `.env` and set
  the three required PostgreSQL values.
- API remains `starting`: inspect `docker compose logs api` and verify
  PostgreSQL is healthy with `docker compose ps`.
- `/readyz` returns 503: inspect its sanitized dependency states, then check
  database configuration/connectivity without printing secrets.
- An agent query reports an unavailable backend: configure an appropriate
  external/local backend; health and metadata endpoints intentionally do not
  load one.

Local Compose does not provision Azure or enterprise identity. The primary
Azure deployment is live with managed identity and private PostgreSQL; the
public Hugging Face profile remains an alternate deployment with synthetic
process-local state and bounded inference-specific rate/concurrency controls.
Those demo controls are not a substitute for production identity, durable
storage, or an edge protection strategy.
