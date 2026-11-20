output "public_url" {
  description = "URL publique de Mire (PUBLIC_URL de l'API)"
  value       = local.public_url
}

output "api_fqdn" {
  description = "Nom d'hôte *.azurecontainerapps.io de l'API (cible du CNAME du domaine personnalisé)"
  value       = var.apps_enabled ? azurerm_container_app.api[0].ingress[0].fqdn : null
}

output "custom_domain_verification_id" {
  description = "Valeur de l'enregistrement TXT asuid.<domaine> (vérification du domaine personnalisé)"
  value       = azurerm_container_app_environment.main.custom_domain_verification_id
}

output "key_vault_name" {
  description = "Coffre où poser les secrets (az keyvault secret set --vault-name …)"
  value       = azurerm_key_vault.main.name
}

output "postgres_fqdn" {
  description = "Nom d'hôte privé de PostgreSQL (joignable seulement depuis le VNet)"
  value       = azurerm_postgresql_flexible_server.main.fqdn
}

output "container_app_environment" {
  value = azurerm_container_app_environment.main.name
}

output "resource_group" {
  value = local.rg
}
