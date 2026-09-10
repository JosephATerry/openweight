# Terraform Azure reference implementation

This directory translates the approved D12 architecture into direct AzureRM
resources. D13 created and statically reviewed the configuration only. It did
not authenticate to Azure, initialize remote state, plan, apply, or create any
resource.

## Scope

The root defines:

- one resource group and common non-sensitive tags;
- one VNet with dedicated Container Apps and PostgreSQL subnets;
- PostgreSQL private DNS and VNet linkage;
- Basic ACR with the admin and anonymous users disabled;
- one user-assigned runtime identity with only `AcrPull` and
  `Key Vault Secrets User` assignments;
- a separate CI identity with ACR push and single-Container-App deployment
  rights, plus optional parameterized GitHub OIDC federation;
- an RBAC-enabled Key Vault foundation without secret payload resources;
- Log Analytics and workspace-based Application Insights;
- PostgreSQL Flexible Server, a database, and the `vector` allowlist setting;
- one externally ingressed FastAPI Container App with managed-identity ACR and
  Key Vault integration, `/healthz` and `/readyz` probes, and one replica.

It deliberately does not define AKS, a GPU/model service, Redis, a frontend,
MCP, secret payloads, policy indexing, database schema execution, fictional
seed data, or a public Prometheus service. GitHub federation remains disabled
until real repository trust values are supplied.

## File layout

| File | Responsibility |
|---|---|
| `versions.tf`, `providers.tf`, `backend.tf` | Terraform/AzureRM contract and empty remote backend declaration |
| `variables.tf`, `locals.tf` | Validated inputs, deterministic names, and common tags |
| `resource_group.tf`, `networking.tf` | Resource boundary and preferred private database network |
| `registry.tf`, `identities.tf`, `key_vault.tf` | Image registry, runtime identity/RBAC, and secret-store foundation |
| `observability.tf` | Log Analytics and workspace-based Application Insights |
| `postgres.tf` | Private Flexible Server, database, and pgvector allowlist |
| `container_apps.tf` | Container Apps environment and the single API app |
| `outputs.tf` | Non-secret deployment identifiers and hostnames |
| `terraform.tfvars.example` | Non-secret illustrative values only |

## Version and provider policy

Terraform 1.11 or newer is required because the PostgreSQL bootstrap password
uses an ephemeral input and AzureRM's write-only
`administrator_password_wo` argument. AzureRM is constrained to the compatible
5.x release line. Backend-disabled D13 initialization with a checksum-verified
temporary Terraform 1.16.0 binary resolved AzureRM 5.3.0. The generated
`.terraform.lock.hcl` records that provider selection and official checksums;
review and commit it with the configuration.

## Remote state

`backend.tf` declares an empty `azurerm` backend. A later authorized bootstrap
must create a dedicated state resource group, storage account, private
container, locking/access policy, and least-privilege identity. Backend values
belong in a local ignored `*.backend.hcl` file or trusted CI configuration, not
in source. Never use a storage key, SAS token, or client secret in this tree.

Terraform state contains infrastructure identifiers and provider-computed
sensitive attributes. Protect it with encryption, RBAC, versioning, retention,
and restricted logs even though the administrator password uses a write-only
argument. Local `.tfstate`, plan, private tfvars, override, crash, and plugin
files are ignored by both Git and the Docker build context.

## Variables and naming

Copy `terraform.tfvars.example` to an ignored private tfvars file only after
choosing a location based on service availability, cost, residency, and
inference latency. Replace the illustrative globally unique suffix and immutable
image digest. Replace the RFC 5737 documentation-only ingress CIDR with the
approved caller or edge egress range; unrestricted IPv4 and IPv6 ranges are
rejected. Set `container_app_external_ingress_enabled=false` for an internal-only
deployment. Names derive from `project_name`, `environment`, `region_code`, and
`unique_suffix` while respecting the stricter ACR/Key Vault/PostgreSQL name forms.
Common tags identify project, environment, Terraform ownership, purpose, and
optional non-sensitive component metadata.

Cost-conscious defaults use Basic ACR, 0.5 vCPU/1 GiB API compute, one replica,
a burstable PostgreSQL SKU with 32 GiB storage, seven-day backup retention,
30-day Log Analytics retention, a 1 GiB daily ingestion cap, 25% Application
Insights sampling, no database HA/geo-backup, and no Azure GPU. Review current
regional availability, quota, supported CPU/memory combinations, recovery
objectives, and prices before any plan.

The shipped subnet defaults are non-overlapping children of the default VNet
and are covered by repository tests. Terraform validates CIDR syntax and the
Container Apps `/23` minimum, while AzureRM/Azure validates caller-supplied
VNet/subnet relationships during a future plan. Do not approve a plan until
those relationships and service delegations have been reviewed.

## Secret and PostgreSQL bootstrap boundary

No secret value or `azurerm_key_vault_secret` resource exists in D13 or D14.

Flexible Server currently requires a password-authenticated administrative
bootstrap because the application does not yet implement PostgreSQL Entra token
acquisition. Supply `postgresql_administrator_password` only out of band. It is
an ephemeral Terraform variable passed to AzureRM's write-only password field,
so it is not written to plan or state; its version variable drives rotation.
The server minimum TLS version is explicitly configured (TLS 1.2 by default;
TLS 1.3 is selectable after client compatibility testing). D14 cloud
configuration uses certificate-verifying `sslmode=verify-full` with system
trust roots.

The API must not use that administrator. The D14 bootstrap contract now:

1. expects an administrator to create the least-privilege
   `postgresql_application_login` role out of band;
2. applies narrow grants using `scripts/setup_security.py`;
3. places its password in Key Vault through authorized operations and supplies
   the versionless secret URI as
   `postgresql_application_password_secret_id`;
4. enforces PostgreSQL client TLS verification in cloud configuration.

Replacing password auth with Entra workload authentication remains a future
driver/token-lifecycle improvement rather than a fabricated completed feature.

The Container App is structurally defined now, but a future apply is not
operationally complete until that D14 role and referenced secret exist. This is
an intentional fail-closed boundary, not a placeholder password.

Terraform allowlists `vector` through `azure.extensions`; it does not run
`CREATE EXTENSION vector`. A controlled migration must create the extension in
the application database, create schemas/roles, and then optionally run the
fictional seed. Model-based policy indexing is a separate explicit lifecycle
and is never a Terraform provisioner or startup probe. The database resource
uses `prevent_destroy`; intentional disposable-demo teardown requires a reviewed
configuration change.

## Approval durability and replicas

PostgreSQL mode uses LangGraph `PostgresSaver`, durable row-locked approval
sessions, and a transactional execution ledger. The effect claim, controlled
status update, and terminal result commit atomically. Local/test mode still
offers `InMemorySaver` explicitly.

The reference deployment remains at exactly one replica and does not permit
scale-to-zero until the actual Azure bootstrap, connection capacity, failure
injection, and multi-replica load behavior are verified. Application durability
is implemented; production rollout evidence is deliberately not invented.

## Observability and metrics exposure

The Container Apps environment forwards structured stdout logs to Log
Analytics. Application Insights is workspace-based and remains the future D10
OpenTelemetry target. Tracing stays disabled until a later deployment supplies
and validates a cloud exporter configuration; Azure-specific calls do not
enter business code.

`OPENWEIGHT_METRICS_ENABLED=false` is set for the externally ingressed app
because the current ingress cannot privately isolate `/metrics` by route. The
application also supports an authenticated metrics mode for non-public
environments. Prompts, responses, reasoning, credentials, raw documents, and
sensitive payloads remain outside telemetry.

## External model boundary

The Container App is CPU-only and receives an HTTPS inference endpoint and
model alias as non-secret configuration. D13 provisions no GPU resources,
model weights, model container, or provider credential. If a future external
endpoint needs a credential, operations must add it through Key Vault and the existing
backend abstraction without coupling the API lifecycle to model hosting.

## Static validation

On a workstation with Terraform installed:

```bash
cd infra/terraform
terraform fmt -recursive
terraform init -backend=false
terraform validate
terraform fmt -check -recursive
```

`init -backend=false` may download the public AzureRM provider but does not
authenticate or provision. Commit `.terraform.lock.hcl` after reviewing its
provider selection and checksums; never commit `.terraform/`.

Repository invariants are checked without Azure or Terraform credentials:

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_terraform_configuration.py
.venv/bin/python -m pip check
git diff --check
```

## Future reviewed deployment sequence

Only after a real repository/tenant, secret bootstrap, remote state, and
deployment authorization are approved:

```bash
terraform init -backend-config=demo.backend.hcl
terraform fmt -check -recursive
terraform validate
terraform plan -out=reviewed.tfplan
# Human review of resources, replacements, cost, RBAC, networking, and secrets.
terraform apply reviewed.tfplan
```

The plan file is sensitive and ignored. A real deployment should use GitHub
OIDC federation and short-lived Azure authorization once a repository and trust
policy exist. D13 neither changes the existing CI workflow nor invents a GitHub
subject.
