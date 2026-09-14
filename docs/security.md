# Service security and identity

OpenWeight provides an application security boundary without requiring a live
Azure tenant. Local development remains usable with authentication disabled;
the Terraform cloud contract enables standards-based authentication and
disables the public metrics route.

## Product authentication and authorization

When `OPENWEIGHT_AUTH_ENABLED=true`, every product route under `/v1` validates
an RS256 bearer JWT. Validation checks the signature against the configured
JWKS, the exact issuer and audience, expiry, and `nbf` when present. A request
header is never accepted as an identity assertion by itself. `/healthz` and
`/readyz` remain public and sanitized so Container Apps probes work without a
token.

The bounded authorization policy accepts either the named application roles or
the corresponding delegated scopes:

| Role | Effective permissions |
| --- | --- |
| `OpenWeight.Reader` | `agent.query` |
| `OpenWeight.Approver` | `agent.query`, `approvals.resume` |
| `OpenWeight.Operator` | query, proposal creation, approval resume, metrics read |

A valid token with no required permission receives `403`; missing or invalid
authentication receives `401`. Authentication never widens the controlled
executor: an approver can only resume the proposal already persisted under the
approval ID and cannot substitute its action arguments.

Local tests inject a deterministic verifier/public key. Normal unit tests need
no Entra tenant or network. The configured production seam is compatible with
Microsoft Entra ID OAuth/OIDC but is provider-neutral at the application
boundary.

## Endpoint policy

- `/healthz` and `/readyz`: public, payload-minimized platform probes.
- `/v1/service-info` and `/v1/agent/query`: `agent.query`.
- proposal creation: `actions.propose`.
- approval resume: `approvals.resume`.
- `/metrics`: `public`, `protected`, or `disabled` by configuration. Local
  development defaults to `public`; the Azure Terraform contract sets
  `disabled` so OTLP/stdout telemetry is preferred over an Internet scrape.

## Configuration

| Variable | Safe local default | Cloud posture |
| --- | --- | --- |
| `OPENWEIGHT_AUTH_ENABLED` | `false` | `true` |
| `OPENWEIGHT_AUTH_ISSUER` | empty | tenant-specific HTTPS issuer |
| `OPENWEIGHT_AUTH_AUDIENCE` | empty | registered API audience |
| `OPENWEIGHT_AUTH_JWKS_URL` | empty | tenant-specific HTTPS JWKS |
| `OPENWEIGHT_METRICS_ACCESS_MODE` | `public` | `disabled` or protected |

Issuer and JWKS URLs reject embedded credentials. Tokens, authorization
headers, claims, passwords, connection URLs, prompts, responses, reasoning, and
document content are excluded from logs, metrics, spans, and ordinary errors.

## Azure identities and OIDC

Terraform defines separate user-assigned identities:

- runtime identity: `AcrPull` and `Key Vault Secrets User` only;
- migration identity: ACR pull plus secret-scoped read access to the database
  administrator and application credentials only;
- policy-index identity: ACR pull plus secret-scoped read access to only the
  application database credential;
- CI identity: `AcrPush` on the stack registry and `Container Apps Contributor`
  scoped to the one API Container App.

The optional Azure GitHub federated credential remains disabled until an Azure
deployment supplies reviewed repository, branch, or protected-environment trust
values. Its trust flow is GitHub OIDC to Microsoft Entra ID with audience
`api://AzureADTokenExchange`; no `AZURE_CLIENT_SECRET` is designed or stored.
The subject uses GitHub's immutable repository identity:
`repo:<owner>@<owner-id>/<repo>@<repo-id>:environment:<environment>`. Public
numeric owner/repository IDs keep trust stable across renames and prevent
namespace reuse; for this deployment the Environment remains
`azure-production` and is restricted to `main`.
The runtime identity receives no push/deployment permission, and the CI
identity receives no Key Vault secret-read or database-data permission.

## Key Vault lifecycle

Terraform creates no secret payload. An authorized bootstrap/operations
identity creates and rotates the least-privilege application database password,
inference credential, Tavily credential, or later integration credentials out
of band. Container Apps resolves versionless Key Vault references through its
runtime managed identity. Secret values do not belong in Git, workflow YAML,
Terraform variables, outputs, plans, or state. Password database auth is a
documented transition; Entra PostgreSQL authentication remains a future option
after driver/token lifecycle work is implemented and tested.

## Database transport and roles

PostgreSQL clients accept explicit `POSTGRES_SSLMODE` and
`POSTGRES_SSLROOTCERT`. `demo` and `production` configuration fails readiness
validation unless `sslmode=verify-full`; Terraform supplies `verify-full` with
the system trust roots. Compose keeps `prefer` for local ergonomics.

`scripts/setup_security.py` is an administrator/migration command. It creates
LangGraph checkpoint tables and the application security tables. If an existing
`POSTGRES_APPLICATION_ROLE` is supplied, it grants only connect/schema usage,
required reads, the one `approval_status` column update, security-ledger DML,
checkpoint DML, and narrowly scoped policy-vector DML needed by the later
indexing job. It removes database/schema default privileges and grants no
access to `openweight_admin`, role administration, schema creation, truncation,
or ownership. Role creation/password rotation remains an explicitly authorized
migration operation; the API and indexing job never use the server administrator
login. The shared application credential's policy-table DML is the narrow
concession needed for manual re-indexing; the public API exposes no arbitrary
SQL or indexing route.

## Remaining security work

No live enterprise tenant trust, Azure secret payload, WAF/private metrics
path, Entra PostgreSQL token authentication, or Azure deployment is configured.
The explicit public Hugging Face profile adds bounded inference concurrency and
per-client rate controls, but it intentionally uses simulated personas and
synthetic process-local state rather than production identity or durability.
The LLM remains a proposal component, never the security boundary.
