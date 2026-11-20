# Valeurs à recopier dans les VARIABLES du dépôt GitHub (Settings > Secrets and variables > Actions >
# Variables). Ce sont des identifiants, pas des secrets : l'authentification se fait par OIDC.

output "AZURE_CLIENT_ID" {
  description = "Variable GitHub AZURE_CLIENT_ID (identité id-mire-github)"
  value       = azurerm_user_assigned_identity.github.client_id
}

output "AZURE_TENANT_ID" {
  description = "Variable GitHub AZURE_TENANT_ID"
  value       = azurerm_user_assigned_identity.github.tenant_id
}

output "AZURE_SUBSCRIPTION_ID" {
  description = "Variable GitHub AZURE_SUBSCRIPTION_ID"
  value       = var.subscription_id
}

output "TFSTATE_STORAGE_ACCOUNT" {
  description = "Variable GitHub TFSTATE_STORAGE_ACCOUNT (backend du state de infra/main)"
  value       = azurerm_storage_account.tfstate.name
}
