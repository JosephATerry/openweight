---
title: OpenWeight
emoji: 🛡️
colorFrom: green
colorTo: gray
sdk: docker
app_port: 7860
pinned: false
---

# OpenWeight

## Enterprise LLM Governance Platform

OpenWeight combines grounded internal policy assistance with governed
consequential actions. Deterministic controls and explicit human approval
separate what GPT-OSS proposes from what the platform may execute.

**Designed and built by Joseph A. Terry.**

**[Open the live application](https://josephaterry-openweight.hf.space)** ·
[View the GitHub engineering case study](https://github.com/JosephATerry/openweight) ·
[Hugging Face Space](https://huggingface.co/spaces/josephaterry/openweight)

![OpenWeight Governance Workspace showing policy, access-request, and approval workflows](https://raw.githubusercontent.com/JosephATerry/openweight/main/docs/assets/openweight-overview.png)

## What to try

### Policy & Evidence

Open **Policy & Evidence** and ask:

> What policy evidence is required before approving privileged access?

Review the streamed GPT-OSS answer, its validated citation IDs, and the
separate **Supporting Evidence** passages.

![OpenWeight Policy and Evidence workspace showing a GPT-OSS answer, validated citation IDs, and Supporting Evidence](https://raw.githubusercontent.com/JosephATerry/openweight/main/docs/assets/openweight-policy-evidence.png)

### Governed Actions

1. Switch **Demo Persona** to Operator.
2. Open **Access Requests**, review a request, and create a synthetic
   status-change proposal.
3. Open **Approvals** and observe that the proposal is still awaiting an
   explicit human decision before execution.

The pending approval is the important boundary; the walkthrough does not
require approving or executing the proposal.

## Why this is more than a chatbot

**Policy & Evidence**

```text
question
    -> semantic retrieval
    -> GPT-OSS 20B
    -> grounded answer
    -> citation IDs + Supporting Evidence
```

**Governed Actions**

```text
proposal
    -> deterministic validation / authorization
    -> explicit human approval
    -> fixed controlled executor
    -> audit / replay protection
```

> **The model is not the security boundary.**

Model output cannot authorize itself or directly execute a consequential
change. Backend controls remain authoritative.

## Technical highlights

- React 19 and TypeScript product interface over a typed FastAPI service
  boundary
- `openai/gpt-oss-20b` generation with final-answer-only streaming
- `Qwen/Qwen3-Embedding-0.6B` semantic retrieval over 12 synthetic policy
  documents, 49 portable-index chunks, and 1,024-dimensional embeddings
- LangGraph interruption and resume for explicit human approval
- Bounded MCP interoperability, without arbitrary SQL, shell, or write access
- Multi-stage non-root Docker delivery
- Model-free GitHub Actions CI/CD with GitHub OIDC and Hugging Face Trusted
  Publishing

The [GitHub engineering case study](https://github.com/JosephATerry/openweight)
covers the complete architecture and security model, PostgreSQL/pgvector
durability, observability, containers, MCP, CI/CD, and the Terraform/Azure
production deployment.

## Public demo boundary

This Hugging Face Space is a bounded recruiter demonstration:

- All visible policy and access-governance data is synthetic.
- GPT-OSS inference is remote through Hugging Face Inference Providers and
  Groq; retrieval uses a portable Qwen semantic index.
- Access-request, proposal, and approval state is synthetic, process-local,
  ephemeral, and resets when the Space restarts.
- **Demo Persona is a UI simulation, not production authentication.**
- The public policy flow abstains when internal evidence is insufficient. It
  does not silently fall back to Tavily, web search, or unsupported general
  model knowledge.
- Citation-ID validation verifies membership in the retrieved evidence set,
  not semantic entailment; review the cited passage.
- The public demo does not claim document-level ACL enforcement.
- Azure Container Apps is the primary production deployment; this Space is the
  alternate hosted demo.

The public Space does not run the private PostgreSQL/pgvector durability and
managed-identity posture used by the Azure production deployment.

## Source and engineering case study

The canonical engineering repository is
**[JosephATerry/openweight](https://github.com/JosephATerry/openweight)**. It
contains the full technical case study, architecture, security boundaries,
local/full deployment posture, CI/CD, containers, MCP, observability, and
Azure/Terraform production design.

## Author

**Designed and built by Joseph A. Terry.**

[GitHub](https://github.com/JosephATerry) ·
[Hugging Face](https://huggingface.co/josephaterry)

## Deployment notes

The deterministic deployment export installs this file as the Docker Space
card and serves the React/FastAPI application on port `7860`. The Space uses
CPU hardware for the application and portable Qwen retrieval; it requires no
dedicated GPU, hosted PostgreSQL, persistent storage, or dedicated inference
endpoint.

The runtime secret is named `HF_TOKEN` and is configured only in Space
settings. The exact public origin is
`https://josephaterry-openweight.hf.space`. Remote inference is metered, uses
a finite timeout, and has zero application retries or provider fallback.
Provider/account settings—not application code—govern monetary limits.
Publishing does not itself opt into pay-as-you-go.

See the
[Hugging Face deployment documentation](https://github.com/JosephATerry/openweight/blob/main/docs/huggingface.md)
for the complete configuration and operational details.

© 2026 Joseph A. Terry. All rights reserved.
