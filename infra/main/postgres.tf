# PostgreSQL Flexible Server : B_Standard_B1ms + 32 Go (offre gratuite 12 mois), PostgreSQL 17, extension
# pgvector, accès privé uniquement (sous-réseau délégué + zone DNS privée), sauvegardes 7 jours, sans
# redondance géographique ni haute disponibilité.

# SEULE EXCEPTION à la règle « aucun secret dans Terraform » : le mot de passe administrateur est généré ici
# et se trouve donc dans le state. Le state est privé (compte de stockage sans accès public ni clé partagée,
# accès Entra ID réservé à la CI) et chiffré au repos par Azure Storage. Il est écrit directement dans Key
# Vault (database-url, keda-postgres-connection) ; personne n'a besoin de le connaître.
# special = false : 32 caractères alphanumériques (~190 bits), utilisables tels quels dans une URL de connexion.
resource "random_password" "pg_admin" {
  length  = 32
  special = false
}

resource "azurerm_postgresql_flexible_server" "main" {
  name                          = "psql-mire-prod-${local.suffix}"
  resource_group_name           = local.rg
  location                      = local.location
  version                       = "17"
  sku_name                      = "B_Standard_B1ms"
  storage_mb                    = 32768
  storage_tier                  = "P4"
  auto_grow_enabled             = false
  backup_retention_days         = 7
  geo_redundant_backup_enabled  = false
  delegated_subnet_id           = azurerm_subnet.pg.id
  private_dns_zone_id           = azurerm_private_dns_zone.pg.id
  public_network_access_enabled = false
  administrator_login           = "mireadmin"
  administrator_password        = random_password.pg_admin.result
  tags                          = local.tags

  authentication {
    password_auth_enabled         = true
    active_directory_auth_enabled = false
  }

  # La zone de disponibilité est choisie par Azure à la création
  lifecycle {
    ignore_changes  = [zone]
    prevent_destroy = true
  }

  depends_on = [azurerm_private_dns_zone_virtual_network_link.pg]
}

# Extensions autorisées (liste blanche Azure) : pgvector ; CREATE EXTENSION est fait par la migration Alembic
resource "azurerm_postgresql_flexible_server_configuration" "extensions" {
  name      = "azure.extensions"
  server_id = azurerm_postgresql_flexible_server.main.id
  value     = "VECTOR"
}

resource "azurerm_postgresql_flexible_server_database" "mire" {
  name      = "mire"
  server_id = azurerm_postgresql_flexible_server.main.id
  charset   = "UTF8"
  collation = "en_US.utf8"

  lifecycle {
    prevent_destroy = true
  }
}

locals {
  pg_host     = azurerm_postgresql_flexible_server.main.fqdn
  pg_userinfo = "${azurerm_postgresql_flexible_server.main.administrator_login}:${random_password.pg_admin.result}"
}
