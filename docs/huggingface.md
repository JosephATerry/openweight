# Hugging Face recruiter-demo profile

D17A prepared the zero-infrastructure Docker Space profile while preserving the
full local and production designs. The current public recruiter demo is
`josephaterry/openweight` at
`https://josephaterry-openweight.hf.space`.

## Three deployment postures

| Posture | Inference | Retrieval and state | Identity |
| --- | --- | --- | --- |
| Local full-fidelity demo | Local GPT-OSS 20B on the RTX 3090 | Qwen embeddings, PostgreSQL/pgvector, durable approval and execution records | Configurable JWT/RBAC or explicit local demo mode |
| Public recruiter demo | `openai/gpt-oss-20b` through one explicit Hugging Face Inference Provider | Verified portable Qwen vector index over `data/policies/*.md`; synthetic process-local access/approval sandbox that resets on restart | Explicit public-demo profile with simulated UI personas |
| Production reference | External/managed inference behind a configured provider boundary | Managed PostgreSQL/pgvector with durable checkpoints and transactional ledger | OIDC/JWT, managed identities, Key Vault, and Azure observability |

The Space is a product walkthrough, not the production durability reference.
Its access-request fixtures are synthetic. Proposals still require a separate
approval/resume call and use the same fixed action shape, but state and the
in-process idempotency record reset whenever the Space restarts.

## Inference and cost boundary

The `huggingface` deployment profile selects the official
`huggingface_hub.InferenceClient`, the fixed model identity
`openai/gpt-oss-20b`, and an explicit provider (`groq` by default). `HF_TOKEN`
is read only from the runtime environment and is never returned by service
metadata. Client construction is lazy and does not issue a request. Health,
readiness, startup, MCP discovery, and page loads make zero inference calls.

Each policy question makes at most one application-level provider request. The
timeout is finite (60 seconds by default), retries are fixed to zero, and there
is no provider/model/Tavily fallback. Quota, timeout, and provider errors become
the existing sanitized dependency-unavailable response while the UI and
non-inference workflows stay available.

The public profile also rejects rather than queues beyond two simultaneous
inference requests and permits five attempts per ASGI client address per
60-second window by default. All limits are configurable. Client addresses are
represented only by process-salted hashes, never logged or persisted, and the
tracker is capped at 1,024 entries with stale expiry and a shared overflow
bucket. These controls reduce casual abuse; they are not a monetary budget.

The application does not parse forwarding headers. It uses the client address
already established by the ASGI server, so the final Space deployment must rely
on trusted-proxy processing at the serving boundary rather than accepting
arbitrary client-supplied forwarding values.
If the platform does not supply a trusted original client address, all visitors
share the proxy address's conservative window; the application does not weaken
the boundary by reading a spoofable header directly.

These controls limit request behavior; they cannot enforce a dollar budget.
Spending limits, pay-as-you-go status, and provider quota must be set in the
Hugging Face/provider account. D17A does not enable billing, buy credits,
allocate GPU hardware, or create an endpoint.

## Portable retrieval

`scripts/build_demo_policy_index.py` uses the canonical document loader,
chunker, Qwen query instruction, and `Qwen/Qwen3-Embedding-0.6B` to produce:

- `deploy/huggingface/policy_index.npz`: normalized 1,024-dimensional vectors;
- `deploy/huggingface/policy_index.json`: model/chunker metadata plus a digest
  for every canonical chunk.

At startup/readiness, the adapter verifies the 49-record manifest against the
current synthetic policy corpus without loading Qwen or calling inference. The
Qwen encoder is loaded on CPU only when a user submits a policy question. A
Space build can explicitly preload that public model using
`OPENWEIGHT_PRELOAD_DEMO_EMBEDDINGS=true`; this avoids a model download during
the first visitor request. No historical evaluation data participates in the
artifact or its validation.

The artifact and optional container preload pin Qwen revision
`97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3` so regeneration is not affected by
a later movement of the model repository's default branch.

Regenerate and validate locally without using the GPU:

```bash
CUDA_VISIBLE_DEVICES='' HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  PYTHONPATH=src .venv/bin/python scripts/build_demo_policy_index.py

PYTHONPATH=src .venv/bin/python -c \
  "from openweight_platform.rag.portable import PortablePolicyStore; PortablePolicyStore('deploy/huggingface/policy_index.npz').validate()"
```

## Container and configuration

The existing multi-stage Dockerfile still builds the React SPA and runs the
FastAPI process as a non-root user. A Space sets `OPENWEIGHT_API_PORT=7860`, so
the SPA, `/v1/*`, `/mcp`, `/healthz`, and `/readyz` share one origin. Normal
Compose continues to use port 8000 and PostgreSQL.

The Space metadata and variable names are in
`deploy/huggingface/README.template.md`; the credential-free runtime example is
`deploy/huggingface/env.example`.

The deployed profile sets `OPENWEIGHT_HF_PUBLIC_ORIGIN` to the exact canonical
origin `https://josephaterry-openweight.hf.space`. That setting rejects
wildcards, localhost, credentials, ports, paths, queries, and fragments, and
narrows both whole-application Host validation and MCP transport Host/Origin
validation. It is intentionally blank for the loopback-only local smoke
profile.

With no `HF_TOKEN`, the container remains alive and ready for retrieval,
navigation, and the synthetic governance workflow. Service metadata reports
inference as unconfigured, and a policy generation attempt fails safely without
fabricating an answer.

The isolated, credential-free Space-style smoke profile is:

```bash
docker compose -p openweight-d17a \
  -f deploy/huggingface/compose.yaml up --build --detach
```

It enables the same pinned Qwen build-time preload intended for the public
Space. The measured D17B image is approximately 1.4 GB, including about 1.2 GB
of model/tokenizer/config files. Startup remains lazy: the encoder is present on
disk but is loaded on CPU only for a policy query. No GPT-OSS provider request
is made during build, startup, or health checks.
