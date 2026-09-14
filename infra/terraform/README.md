# Terraform Azure OpenWeight implementation

This root is a source-level Azure implementation for the public portfolio
deployment. D19D did not authenticate to Azure, initialize or access a remote
backend, plan, apply, create a secret, push an image, or deploy an application.
Provider plugins were initialized locally with `-backend=false` only for schema
validation.

## Final architecture

Terraform defines a resource group, VNet, Container Apps infrastructure subnet,
private delegated PostgreSQL subnet and private DNS, Standard ACR, Container
Apps Consumption environment, PostgreSQL Flexible Server, Key Vault, bounded
Log Analytics, separate runtime/bootstrap/CI identities, narrow RBAC, optional
GitHub OIDC federation bound to an Environment, manual bootstrap jobs, and the public
HTTPS app. Federation is disabled by default until D19E/F deliberately enables
the reviewed trust.

The application container is CPU-only. It contains the React build and FastAPI
service, not GPT-OSS weights. Generation follows the existing tested route:

```text
Azure Container App -> Hugging Face Inference Providers -> Groq
                    -> openai/gpt-oss-20b
```

There is no Azure accelerator, model endpoint, model-weight storage, AKS, NAT
Gateway, Private Endpoint, Front Door, Application Gateway, or API Management
resource. Application Insights was removed because the application does not
wire an Azure exporter; privacy-safe structured stdout still reaches bounded
Log Analytics. Health checks never call an embedding model or GPT-OSS.

## Explicit lifecycle stages

`deployment_stage` prevents the circular first-deploy lifecycle:

1. `foundation` creates durable infrastructure, ACR, identities, database, Key
   Vault, and the Container Apps environment. OIDC federation remains disabled
   unless deliberately enabled after its GitHub trust is ready. Foundation
   creates neither a job nor an app and does not require an image or secret
   reference.
2. The protected Azure workflow can be manually dispatched in `build_only`
   mode. It builds the production image, pushes it to ACR, resolves the digest,
   and does not change a Container App.
3. Authorized operations create three separate Key Vault secrets: database
   administrator bootstrap password, runtime database password, and the Azure
   inference-only HF token. Terraform never manages their values.
4. `bootstrap` creates two manual Container Apps Jobs using that immutable
   image. Terraform never starts them. An operator starts the migration job,
   verifies success, then starts the policy-index job and verifies success.
5. `application` removes the temporary jobs/bootstrap identity and creates the
   digest-pinned app only after database and policy readiness are proven.
6. Steady-state `main` pushes deploy through CI and the independent Azure CD
   workflow. Infrastructure changes remain reviewed Terraform operations.

For a later schema release, the reviewed `maintenance` stage adds the manual
jobs and temporary bootstrap identity without removing the running app. After
the additive job is verified, return to `application` to remove that access.

Never jump directly from `foundation` to `application`. Never run migration as
an app entrypoint, startup hook, Terraform provisioner, or ordinary release
step. A failed additive migration can be rerun explicitly; no destructive SQL
is executed automatically.

## Database and pgvector bootstrap

The database defaults are `B_Standard_B1ms`, fixed 32 GiB/P4 with auto-grow
disabled, PostgreSQL 17, seven-day backup, no HA, no geo-redundant backup,
private connectivity, and TLS 1.2 or newer. Terraform allowlists `vector` with
`azure.extensions`.

The manual `migrate` job runs `scripts/migrate_database.py --seed-demo-data`.
It serializes executions with a PostgreSQL advisory lock and records migration
version 1. The additive migration:

- runs `CREATE EXTENSION IF NOT EXISTS vector`;
- creates the operations, security, approval-session, execution-ledger,
  LangGraph checkpoint, and pgvector policy structures;
- creates/rotates the runtime login from a Key Vault-provided password;
- grants only read access to demo records and policy evidence, column-scoped
  update access to request status, and the required checkpoint/approval ledger
  DML privileges;
- optionally loads only the repository's fictional operations records.

The separate `policy-index` job is the only bootstrap step that runs Qwen CPU
embeddings. Its model is preloaded into the immutable image; it is never a
health check or normal deployment side effect. `/readyz` verifies the database,
checkpoint structures, vector extension, policy table, nonempty policy index,
and HF credential configuration without invoking either model.

## Secret boundary

No `azurerm_key_vault_secret` resource exists. Secret values are absent from
Terraform, tfvars, state, image layers, workflows, the frontend, and logs. The
three versionless URI variables point to secrets created out of band. Terraform
scopes `Key Vault Secrets User` to individual secret ARM resources:

- the short-lived bootstrap identity can read the two database secrets;
- the runtime identity can read the runtime database password and Azure HF
  inference token, but not the database administrator password;
- the CI identity cannot read any Key Vault secret.

The Azure HF token should be an inference-only token from the same HF account as
the Space token, but it is a different token for independent revocation. The
Hugging Face Space secret and deployment workflow are unchanged and independent.

## Recruiter-facing security posture

An empty `container_app_allowed_ingress_cidrs` list exposes Container Apps'
managed HTTPS hostname publicly. Optional restricted CIDRs remain supported.
The compiled React frontend is enabled.

The Azure profile requires Entra authentication configuration and separately
enables anonymous read/query access. Anonymous recruiters may view service
metadata, synthetic access records, and ask metered Policy & Evidence questions.
They receive only the backend `agent.query` permission. Proposal and approval
routes still require a valid Entra token with `actions.propose` or
`approvals.resume`; selecting an Operator/Approver persona in React grants
nothing. A 401 on an anonymous attempted action is the intentional portfolio
demonstration of the boundary. Production operators can exercise the complete
durable governed path with real Entra assignments.

The invariant remains: model proposal -> deterministic validation,
authorization, and policy controls -> explicit approval -> fixed executor ->
durable audit state. The LLM is not the security boundary.

## Cost posture

Defaults align with the new-customer allowances: Standard ACR, one B1ms server
with 32 GiB, Container Apps Consumption, and one 1-vCPU/2-GiB app that can scale
to zero. Durable approval state makes replica shutdown safe; cold-start latency
is the accepted portfolio tradeoff. The app stays at max one replica until live
load testing. Log Analytics retains 30 days with a 1-GiB daily cap. No always-on
application replica or decorative Application Insights resource is created.
The app permits exactly one replica initially, uses `OPENWEIGHT_METRICS_ENABLED=false`,
and deploys no GPU. Durable workflow state uses `PostgresSaver` plus the
transactional execution ledger.

Free allowances and service availability are offer-, subscription-, region-,
and time-dependent. Review the portal cost estimate and the existing $70 budget
alerts before every apply. Spending protection remains enabled; this design does
not require conversion to Pay-As-You-Go.

## Active deployment region

The reviewed recovery target is Central US: use `location = "centralus"` and
`region_code = "cus"`. Globally scoped ACR, Key Vault, and PostgreSQL names
include that region code, so a Central US deployment does not depend on reusing
an East US name after cleanup or on purging a soft-deleted Key Vault. For the
established `jat060015f` suffix, the foundation names are
`acrowpdemocusjat060015f`, `kv-jat060015f-cus-owp`, and
`psql-jat060015f-cus-owp-demo`.

## Remote state

[`state-bootstrap/README.md`](state-bootstrap/README.md) defines the separate
one-time Standard LRS state foundation. It uses a private container, Entra data
plane RBAC, shared keys disabled, TLS, versioning/change feed, soft deletion,
and destroy guards. D19D did not create it. Backend values remain outside Git;
never use a storage key, SAS token, or client secret.

## Static validation

Formatting requires no provider or backend access:

```bash
terraform fmt -check -recursive infra/terraform
PYTHONPATH=src python -m pytest -q tests/test_terraform_configuration.py
git diff --check
```

D19D validation ran `terraform init -backend=false -lockfile=readonly` and
`terraform validate` for both roots against the pinned provider. Do not run
backend initialization, `plan`, or `apply` until the D19E remote-state/bootstrap
procedure is explicitly authorized.
