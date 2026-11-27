# Key Vault (mode RBAC) : secrets de l'application, lus par les Container Apps via des références Key Vault
# (identité runtime id-mire-runtime, rôle Key Vault Secrets User).
#
# Secrets posés À LA MAIN (az keyvault secret set, voir docs/deploiement-azure.md) : Terraform n'en connaît
# que les noms, jamais les valeurs.
#   session-secret, linkedin-client-id, linkedin-client-secret, gemini-api-key, agent-token, ghcr-pat,
#   healthcheck-daily-url, et si billing_enabled : stripe-secret-key, stripe-webhook-secret ; si agent_enabled :
#   anthropic-api-key (clé de l'API Anthropic de l'agent de recherche, avec une limite de dépense mensuelle
#   posée dans la console Anthropic) ; si digest_enabled : smtp-password (fournisseur d'envoi des récapitulatifs).
# Secrets posés PAR TERRAFORM (dérivés du mot de passe PostgreSQL généré, voir postgres.tf) :
#   database-url, keda-postgres-connection.

resource "azurerm_key_vault" "main" {
  name                          = "kv-mire-${local.suffix}"
  resource_group_name           = local.rg
  location                      = local.location
  tenant_id                     = data.azurerm_client_config.current.tenant_id
  sku_name                      = "standard"
  rbac_authorization_enabled    = true
  purge_protection_enabled      = true
  soft_delete_retention_days    = 7
  public_network_access_enabled = true # CI (runners GitHub) et az CLI ; l'accès est contrôlé par RBAC
  tags                          = local.tags
}

locals {
  manual_secrets = concat(
    ["session-secret", "linkedin-client-id", "linkedin-client-secret", "gemini-api-key", "agent-token", "ghcr-pat",
    "healthcheck-daily-url"],
    var.billing_enabled ? ["stripe-secret-key", "stripe-webhook-secret"] : [],
    var.agent_enabled ? ["anthropic-api-key"] : [],
    var.digest_enabled ? ["smtp-password"] : [],
  )
  # ID SANS version : les Container Apps relisent la dernière version du secret (sous 30 min environ)
  secret_id = { for name in concat(local.manual_secrets, ["database-url", "keda-postgres-connection"]) :
  name => "${azurerm_key_vault.main.vault_uri}secrets/${name}" }
}

# URL SQLAlchemy de l'application (driver psycopg 3), TLS obligatoire
resource "azurerm_key_vault_secret" "database_url" {
  name         = "database-url"
  key_vault_id = azurerm_key_vault.main.id
  value        = "postgresql+psycopg://${local.pg_userinfo}@${local.pg_host}:5432/${azurerm_postgresql_flexible_server_database.mire.name}?sslmode=require"
  content_type = "url de connexion (généré par Terraform)"
  tags         = local.tags
}

# Même base au format libpq, pour le scaler KEDA « postgresql » du job mire-worker
resource "azurerm_key_vault_secret" "keda_postgres" {
  name         = "keda-postgres-connection"
  key_vault_id = azurerm_key_vault.main.id
  value        = "postgresql://${local.pg_userinfo}@${local.pg_host}:5432/${azurerm_postgresql_flexible_server_database.mire.name}?sslmode=require"
  content_type = "url de connexion (généré par Terraform)"
  tags         = local.tags
}

# --- Identité runtime des Container Apps ----------------------------------------------------------

resource "azurerm_user_assigned_identity" "runtime" {
  name                = "id-mire-runtime"
  resource_group_name = local.rg
  location            = local.location
  tags                = local.tags
}

resource "azurerm_role_assignment" "runtime_key_vault" {
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Secrets User" # autorisé par la condition du bootstrap
  principal_id         = azurerm_user_assigned_identity.runtime.principal_id
  principal_type       = "ServicePrincipal"
}
