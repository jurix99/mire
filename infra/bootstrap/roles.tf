# Droits de l'identité de GitHub Actions (id-mire-github), au plus juste.
#
# - Contributor sur rg-mire-prod : crée et met à jour toutes les ressources de l'application, rien ailleurs.
# - Storage Blob Data Contributor sur le SEUL conteneur tfstate : lit et écrit le state (et son verrou).
# - Key Vault Secrets Officer sur rg-mire-prod : Terraform écrit les secrets qu'il génère (database-url,
#   keda-postgres-connection) et lit les métadonnées des autres. Le coffre est créé par infra/main, d'où une
#   portée au niveau du groupe de ressources (seul coffre du groupe).
# - Role Based Access Control Administrator sur rg-mire-prod, AVEC UNE CONDITION : infra/main doit créer une
#   attribution de rôle (Key Vault Secrets User pour l'identité runtime des Container Apps). Contributor ne
#   le permet pas, et Owner / User Access Administrator permettraient de s'attribuer n'importe quel rôle.
#   Choix : la condition (ABAC) limite les attributions que la CI peut créer OU supprimer aux deux rôles
#   Key Vault Secrets User et AcrPull. Même si le workflow était détourné, la CI ne pourrait pas se donner
#   Owner, ni donner un rôle d'écriture à quiconque. (AcrPull n'est pas utilisé aujourd'hui : les images sont
#   sur GHCR. Il est autorisé pour pouvoir passer à ACR sans refaire le bootstrap.)

locals {
  role_key_vault_secrets_user = "4633458b-17de-408a-b874-0445c86b69e6"
  role_acr_pull               = "7f951dda-4ed3-4680-a7ca-43fe172d538d"
  assignable_roles            = "${local.role_key_vault_secrets_user}, ${local.role_acr_pull}"
}

resource "azurerm_role_assignment" "github_contributor" {
  scope                = azurerm_resource_group.prod.id
  role_definition_name = "Contributor"
  principal_id         = azurerm_user_assigned_identity.github.principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "github_state" {
  scope                = azurerm_storage_container.tfstate.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_user_assigned_identity.github.principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "github_key_vault_secrets" {
  scope                = azurerm_resource_group.prod.id
  role_definition_name = "Key Vault Secrets Officer"
  principal_id         = azurerm_user_assigned_identity.github.principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "github_rbac_admin" {
  scope                = azurerm_resource_group.prod.id
  role_definition_name = "Role Based Access Control Administrator"
  principal_id         = azurerm_user_assigned_identity.github.principal_id
  principal_type       = "ServicePrincipal"
  description          = "CI Mire : attributions limitées à Key Vault Secrets User et AcrPull"
  condition_version    = "2.0"
  condition            = <<-EOT
    (
     (
      !(ActionMatches{'Microsoft.Authorization/roleAssignments/write'})
     )
     OR
     (
      @Request[Microsoft.Authorization/roleAssignments:RoleDefinitionId] ForAnyOfAnyValues:GuidEquals {${local.assignable_roles}}
     )
    )
    AND
    (
     (
      !(ActionMatches{'Microsoft.Authorization/roleAssignments/delete'})
     )
     OR
     (
      @Resource[Microsoft.Authorization/roleAssignments:RoleDefinitionId] ForAnyOfAnyValues:GuidEquals {${local.assignable_roles}}
     )
    )
  EOT
}

# La personne qui lance le bootstrap pose les secrets à la main (az keyvault secret set) : droit d'écriture
# sur les secrets des coffres de rg-mire-prod (plan de données ; Owner ne le donne pas en mode RBAC).
resource "azurerm_role_assignment" "operator_key_vault_secrets" {
  scope                = azurerm_resource_group.prod.id
  role_definition_name = "Key Vault Secrets Officer"
  principal_id         = data.azurerm_client_config.current.object_id
}
