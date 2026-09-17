# Terraform state bootstrap

This isolated root designs the one-time remote-state foundation. It uses the
lowest-cost appropriate general-purpose `Standard_LRS` storage account, a
private blob container, Microsoft Entra/RBAC data-plane access, TLS-only
transport, shared-key authentication disabled, blob versioning/change feed,
14-day blob and container soft deletion, and `prevent_destroy` guards.

It intentionally has local state because an Azure backend cannot store the
state that creates itself. The production remote-state foundation was created
through a reviewed one-time bootstrap with a dedicated infrastructure operator
object ID. Keep `infra/terraform/state-bootstrap/terraform.tfstate` local,
ignored, mode `0600`, and separately secured. Do not migrate that bootstrap
state into the storage account it manages or reapply the root merely to
recreate an existing foundation.

Supply the main application's partial `azurerm` backend with reviewed values
outside Git, for example in `/tmp/openweight-demo.backend.hcl`:

```hcl
resource_group_name  = "rg-owp-tfstate"
storage_account_name = "<output>"
container_name       = "tfstate"
key                  = "openweight/demo.tfstate"
use_azuread_auth      = true
```

For the first main-root initialization, first verify that neither
`infra/terraform/terraform.tfstate` nor its backup exists. There is no state to
migrate in that case, so initialize from the repository root with:

```bash
terraform -chdir=infra/terraform init \
  -reconfigure \
  -input=false \
  -lockfile=readonly \
  -backend-config=/tmp/openweight-demo.backend.hcl
```

Use `-migrate-state` only if the main root already has an existing local state
that must be transferred to Azure Blob Storage. Treat that as an explicit,
reviewed state-migration operation; preserve a secure backup, verify the source
state first, and do not combine `-migrate-state` with `-reconfigure`:

```bash
terraform -chdir=infra/terraform init \
  -migrate-state \
  -lockfile=readonly \
  -backend-config=/tmp/openweight-demo.backend.hcl
```

This conditional migration applies only to existing main-root state. It never
applies to the separately managed state-bootstrap root.

Do not use an account key, SAS token, client secret, public blob access, GRS,
or a premium storage tier. Public network reachability is retained only so a
reviewed operator or GitHub-hosted runner can reach the data plane; Entra RBAC
still gates every state operation. A private endpoint would add fixed cost and
hosted-runner connectivity complexity to this free-account portfolio posture.

For future maintenance, validate this root locally with the backend disabled.
Any change to the existing state foundation requires a separate reviewed and
authorized infrastructure operation.
