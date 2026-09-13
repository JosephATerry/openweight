resource "azurerm_log_analytics_workspace" "main" {
  name                            = local.log_analytics_name
  location                        = azurerm_resource_group.main.location
  resource_group_name             = azurerm_resource_group.main.name
  sku                             = "PerGB2018"
  retention_in_days               = var.log_analytics_retention_days
  daily_quota_gb                  = var.log_analytics_daily_quota_gb
  allow_resource_only_permissions = true
  tags                            = local.common_tags
}
