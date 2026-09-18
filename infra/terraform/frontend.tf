resource "azurerm_storage_account" "frontend" {
  name                             = local.frontend_storage_name
  resource_group_name              = azurerm_resource_group.main.name
  location                         = azurerm_resource_group.main.location
  account_kind                     = "StorageV2"
  account_tier                     = "Standard"
  account_replication_type         = "LRS"
  min_tls_version                  = "TLS1_2"
  https_traffic_only_enabled       = true
  public_network_access_enabled    = true
  shared_access_key_enabled        = false
  default_to_oauth_authentication  = true
  local_user_enabled               = false
  allow_nested_items_to_be_public  = false
  cross_tenant_replication_enabled = false
  tags                             = local.common_tags
}

resource "azapi_resource" "frontend_static_website" {
  type      = "Microsoft.Storage/storageAccounts/blobServices@2025-08-01"
  name      = "default"
  parent_id = azurerm_storage_account.frontend.id

  body = {
    properties = {
      staticWebsite = {
        enabled              = true
        indexDocument        = "index.html"
        errorDocument404Path = "index.html"
      }
    }
  }
}

resource "azurerm_role_assignment" "ci_frontend_upload" {
  scope                = "${azurerm_storage_account.frontend.id}/blobServices/default/containers/$web"
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_user_assigned_identity.ci.principal_id
  principal_type       = "ServicePrincipal"
  description          = "Allow the isolated CI identity to publish only static frontend content."

  depends_on = [azapi_resource.frontend_static_website]
}
