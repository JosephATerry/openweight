# Terraform state bootstrap

This isolated root designs the one-time remote-state foundation. It uses the
lowest-cost appropriate general-purpose `Standard_LRS` storage account, a
private blob container, Microsoft Entra/RBAC data-plane access, TLS-only
transport, shared-key authentication disabled, blob versioning/change feed,
14-day blob and container soft deletion, and `prevent_destroy` guards.

It intentionally has local state because an Azure backend cannot store the
state that creates itself. D19D initialized only local provider plugins with
the backend disabled and did not apply this root. During a reviewed D19E
bootstrap, apply it once with a dedicated infrastructure operator object ID,
secure the small local bootstrap state, and migrate the application root with
`terraform init -migrate-state` using:

```hcl
resource_group_name  = "rg-owp-tfstate"
storage_account_name = "<output>"
container_name       = "tfstate"
key                  = "openweight/demo.tfstate"
use_azuread_auth      = true
```

Do not use an account key, SAS token, client secret, public blob access, GRS,
or a premium storage tier. Public network reachability is retained only so a
reviewed operator or GitHub-hosted runner can reach the data plane; Entra RBAC
still gates every state operation. A private endpoint would add fixed cost and
hosted-runner connectivity complexity to this free-account portfolio posture.

D19D validated this root locally. It did not initialize a state backend,
authenticate to Azure, or create any resource.
