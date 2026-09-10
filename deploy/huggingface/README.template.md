---
title: OpenWeight
emoji: 🛡️
colorFrom: green
colorTo: gray
sdk: docker
app_port: 7860
pinned: false
---

# OpenWeight recruiter demo

This template is intended to become the root `README.md` only in a future,
explicitly authorized Hugging Face Docker Space. D17A does not create or publish
a Space.

Configure the runtime secret named `HF_TOKEN` in Space settings. Never commit
its value. Configure these public variables:

```text
OPENWEIGHT_DEPLOYMENT_PROFILE=huggingface
OPENWEIGHT_ENVIRONMENT=demo
OPENWEIGHT_API_HOST=0.0.0.0
OPENWEIGHT_API_PORT=7860
OPENWEIGHT_BACKEND=gpt-oss
OPENWEIGHT_GPT_OSS_MODEL_ID=openai/gpt-oss-20b
OPENWEIGHT_HF_PROVIDER=groq
OPENWEIGHT_HF_TIMEOUT_SECONDS=60
OPENWEIGHT_HF_MAX_RETRIES=0
OPENWEIGHT_HF_INFERENCE_CONCURRENCY_LIMIT=2
OPENWEIGHT_HF_RATE_LIMIT_REQUESTS=5
OPENWEIGHT_HF_RATE_LIMIT_WINDOW_SECONDS=60
OPENWEIGHT_HF_RATE_LIMIT_MAX_CLIENTS=1024
OPENWEIGHT_DATABASE_REQUIRED=false
OPENWEIGHT_CHECKPOINT_BACKEND=memory
OPENWEIGHT_WEB_ENABLED=false
OPENWEIGHT_FRONTEND_ENABLED=true
OPENWEIGHT_FRONTEND_DIST_DIR=/app/frontend/dist
OPENWEIGHT_METRICS_ENABLED=false
OPENWEIGHT_METRICS_ACCESS_MODE=disabled
OPENWEIGHT_AUTH_ENABLED=false
OPENWEIGHT_MCP_ENABLED=true
OPENWEIGHT_MCP_ALLOWED_HOSTS=localhost:*,127.0.0.1:*
OPENWEIGHT_MCP_ALLOWED_ORIGINS=http://localhost:*,http://127.0.0.1:*
OPENWEIGHT_DEMO_POLICY_INDEX=/app/deploy/huggingface/policy_index.npz
OPENWEIGHT_DEMO_EMBEDDING_MODEL=/app/models/qwen3-embedding-0.6b
VITE_DEMO_MODE=true
OPENWEIGHT_PRELOAD_DEMO_EMBEDDINGS=true
```

`VITE_DEMO_MODE` and `OPENWEIGHT_PRELOAD_DEMO_EMBEDDINGS` are build variables;
the others are runtime variables. The exact Space-settings workflow must be
reviewed during D17B before anything is published.

After the Space hostname exists, set `OPENWEIGHT_HF_PUBLIC_ORIGIN` to that one
exact HTTPS origin. The application then derives an exact whole-app Host rule
and exact MCP Host/Origin rules from it. Wildcard or credential-bearing origins
are rejected.

The intended Space uses CPU hardware and Hugging Face Inference Providers. It
does not need a dedicated GPU, hosted PostgreSQL, or dedicated inference
endpoint. Remote GPT-OSS requests are metered by the selected provider. This
application sets a finite timeout and performs no application retries or
fallbacks, but monetary limits must be configured in the Hugging Face/provider
account settings. Publishing does not itself opt into pay-as-you-go.
