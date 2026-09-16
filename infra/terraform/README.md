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
Log Analytics, separate runtime/migration/indexing/CI identities, narrow RBAC,
optional GitHub OIDC federation bound to an Environment, manual jobs, and the public
HTTPS app. Federation is disabled by default until D19E/F deliberately enables
the reviewed trust.

GitHub repositories created after July 15, 2026 use immutable default OIDC
subjects. The public, non-secret `github_repository_owner_id` and
`github_repository_id` inputs bind Azure trust to both names and stable GitHub
IDs. They must describe the repository being federated; Terraform never looks
them up at runtime. For this repository and its protected Environment, the
subject is
`repo:JosephATerry@204825811/openweight@1363713223:environment:azure-production`.

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
   Initial creation explicitly sets
   `postgresql_administrator_password_required=true` while supplying the
   sensitive ephemeral password. Later refreshes and stages leave the flag
   false and the nullable password unset; AzureRM receives no replacement value
   and does not rotate the write-only credential. The provider persists the
   creation-time write-only version marker but cannot read the password back;
   the server lifecycle therefore ignores that marker after creation. Do not
   increment it to rotate an existing server. Administrator-password rotation
   is a separately authorized out-of-band Azure operation paired with the Key
   Vault handoff, not an ordinary Terraform update.
2. The protected Azure workflow can be manually dispatched in `build_only`
   mode only after the separate `AZURE_BUILD_ENABLED=true` gate is deliberately
   configured. It builds a Linux/amd64 migration image with
   `OPENWEIGHT_PRELOAD_DEMO_EMBEDDINGS=false`, pushes a source-SHA tag, resolves
   the digest, and cannot apply Terraform, start a job, or change a database or
   Container App.
3. Authorized operations create three separate Key Vault secrets: database
   administrator bootstrap password, runtime database password, and the Azure
   inference-only HF token. Terraform never manages their values.
4. `migration` creates only the manual database migration job and its dedicated
   identity. Terraform never starts it. The identity can pull the immutable
   migration image and read only the administrator and application database
   secrets.
5. A separately reviewed `indexing` stage creates only the later policy-index
   job and its distinct identity. That identity receives the application
   database secret, never the administrator secret. A separate model-enabled
   immutable image is required for that later operation.
6. `application` removes the temporary jobs and identities and creates the
   digest-pinned app only after database and policy readiness are proven.
7. Steady-state `main` pushes deploy through CI and the independent Azure CD
   workflow. Infrastructure changes remain reviewed Terraform operations.

For a later schema release, `maintenance_migration` retains the running app and
adds only the migration identity/job. `maintenance_indexing` likewise adds only
the policy-index identity/job. There is no combined maintenance stage that
silently enables both privileged paths. Use the narrower `migration` or
`indexing` stage during initial setup; after a manual operation is verified,
return to `application` to remove that access.

Never jump directly from `foundation` to `application`. Never run migration as
an app entrypoint, startup hook, Terraform provisioner, or ordinary release
step. A failed additive migration can be rerun explicitly; no destructive SQL
is executed automatically.

Flexible Server automatically adds the `Microsoft.Storage` service endpoint to
its delegated subnet for required Storage-backed service operations. Terraform
models that endpoint explicitly so later refreshes cannot remove it. Azure also
returns a service-managed, zero-count `Consumption` workload profile for the
Container Apps environment even when configuration omits it. AzureRM 5.3.0
normalizes that default into state and otherwise produces a perpetual removal
diff, so the environment ignores only `workload_profile`; this stack declares
no dedicated or GPU profile.

Azure chooses the PostgreSQL primary zone because `postgresql_zone` defaults
to `null`. AzureRM reads the selected zone back and may also observe a new
primary after failover, so the server lifecycle ignores `zone` rather than
trying to move or fail back the server. The lifecycle also ignores only the
persisted creation-time `administrator_password_wo_version` marker. Initial
foundation creation still supplies both write-only fields; later Terraform
operations supply neither. AzureRM 5.3.0 requires the administrator password
for any actual update to a password-authenticated Flexible Server, so every
future server mutation requires a separate credential-aware review.

## Database and pgvector bootstrap

The database defaults are `B_Standard_B1ms`, fixed 32 GiB/P4 with auto-grow
disabled, PostgreSQL 17, seven-day backup, no HA, no geo-redundant backup,
private connectivity, and TLS 1.2 or newer. Terraform allowlists `vector` with
`azure.extensions`.

The private, no-ingress manual `migrate` job runs
`scripts/migrate_database.py --seed-demo-data` inside the VNet-integrated
Container Apps environment with `verify-full` TLS and the production image's
explicit distro-managed CA bundle. It uses versionless Key
Vault references; only secret URIs enter Terraform. The script serializes
executions with a PostgreSQL advisory lock and records migration
version 1. The additive migration:

- runs `CREATE EXTENSION IF NOT EXISTS vector`;
- creates the operations, security, approval-session, execution-ledger,
  LangGraph checkpoint, and pgvector policy structures;
- creates/rotates the runtime login from a Key Vault-provided password;
- grants only read access to demo records, column-scoped update access to
  request status, policy-table DML needed by the later indexing job, and the
  required checkpoint/approval ledger DML privileges;
- optionally loads only the repository's fictional operations records.

It then validates the extension and harmless 1,024-dimensional vector behavior,
migration uniqueness, schemas, tables, constraints, role attributes, exact
grants, and fictional row counts (6 employees, 4 contractors, 6 requests).
Output distinguishes an applied migration from an already-current migration and
redacts driver exceptions that could contain a DSN.

The separate `policy-index` job is the only later lifecycle step that runs Qwen
CPU embeddings. Its model is preloaded into the immutable image; it is never a
health check or normal deployment side effect. `/readyz` verifies the database,
checkpoint structures, vector extension, policy table, nonempty policy index,
and HF credential configuration without invoking either model.

The current small portfolio corpus deliberately uses exact pgvector scanning;
no HNSW or IVFFlat index is created. Introduce ANN only after corpus growth or
measured retrieval latency justifies a separately reviewed index migration.

## Secret boundary

No `azurerm_key_vault_secret` resource exists. Secret values are absent from
Terraform, tfvars, state, image layers, workflows, the frontend, and logs. The
three versionless URI variables point to secrets created out of band. Terraform
scopes `Key Vault Secrets User` to individual secret ARM resources:

- the short-lived migration identity can read the two database secrets;
- the separate indexing identity can read only the application database secret;
- the runtime identity can read the runtime database password and Azure HF
  inference token, but not the database administrator password;
- the CI identity cannot read any Key Vault secret.

The Azure HF token should be an inference-only token from the same HF account as
the Space token, but it is a different token for independent revocation. The
Hugging Face Space secret and deployment workflow are unchanged and independent.

If image build/push, ACR pull, identity, Key Vault reference, private DNS, TLS,
or authentication validation fails, do not start or automatically retry the
job. Preserve both secrets, inspect metadata-only status, and correct only the
failed layer before a new reviewed manual execution. A failed additive migration
retains its advisory/ledger state: version 1 is recorded only after schema work
succeeds, so an explicitly authorized rerun can complete it. A postcondition
failure blocks indexing and application deployment; it never enables public
PostgreSQL access or triggers destructive rollback SQL.

Because AzureRM 5.3.0 has shown an intermittent provider startup failure, every
future Terraform plan/apply begins with clean Git/state checks, a private
writable temporary `AZURE_CONFIG_DIR`, cleared Terraform debug/stale `TF_VAR_*`
variables, and one bounded `terraform providers schema -json` preflight. Require
exit code zero and a valid AzureRM schema; do not retry automatically on failure.

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
