resource "azurerm_postgresql_flexible_server" "main" {
  name                          = local.postgresql_server_name
  resource_group_name           = azurerm_resource_group.main.name
  location                      = azurerm_resource_group.main.location
  version                       = var.postgresql_version
  delegated_subnet_id           = azurerm_subnet.postgresql.id
  private_dns_zone_id           = azurerm_private_dns_zone.postgresql.id
  public_network_access_enabled = false

  administrator_login               = var.postgresql_administrator_login
  administrator_password_wo         = var.postgresql_administrator_password
  administrator_password_wo_version = var.postgresql_administrator_password_required ? var.postgresql_administrator_password_version : null

  sku_name                     = var.postgresql_sku_name
  storage_mb                   = var.postgresql_storage_mb
  storage_tier                 = var.postgresql_storage_tier
  auto_grow_enabled            = false
  backup_retention_days        = var.postgresql_backup_retention_days
  geo_redundant_backup_enabled = var.postgresql_geo_redundant_backup_enabled
  zone                         = var.postgresql_zone

  authentication {
    active_directory_auth_enabled = false
    password_auth_enabled         = true
  }

  dynamic "high_availability" {
    for_each = var.postgresql_high_availability_enabled ? [1] : []

    content {
      mode = "ZoneRedundant"
    }
  }

  tags = local.common_tags

  # Azure selects the initial primary zone and can change it after failover.
  # The write-only password version is a creation-time state marker; normal
  # post-creation operations omit the password and must not treat that marker
  # as a rotation request.
  lifecycle {
    ignore_changes = [
      administrator_password_wo_version,
      zone,
    ]
  }

  depends_on = [azurerm_private_dns_zone_virtual_network_link.postgresql]
}

resource "azurerm_postgresql_flexible_server_configuration" "extensions" {
  name      = "azure.extensions"
  server_id = azurerm_postgresql_flexible_server.main.id
  value     = "vector"
}

resource "azurerm_postgresql_flexible_server_configuration" "minimum_tls" {
  name      = "ssl_min_protocol_version"
  server_id = azurerm_postgresql_flexible_server.main.id
  value     = var.postgresql_minimum_tls_version
}

resource "azurerm_postgresql_flexible_server_database" "application" {
  name      = var.postgresql_database_name
  server_id = azurerm_postgresql_flexible_server.main.id
  charset   = "UTF8"
  collation = "en_US.utf8"

  lifecycle {
    prevent_destroy = true
  }
}

# The migration-stage manual job explicitly creates vector, schemas, the runtime
# role/grants, checkpoints, and fictional portfolio records. Policy indexing is
# a later, separately privileged manual job. Terraform never executes either job.
