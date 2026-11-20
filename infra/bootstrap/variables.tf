variable "subscription_id" {
  description = "Abonnement Azure (az account show --query id -o tsv)"
  type        = string
}

variable "location" {
  description = "Région du state Terraform (rg-mire-tfstate)"
  type        = string
  default     = "francecentral"
}

variable "prod_location" {
  description = <<-EOT
    Région de l'application (rg-mire-prod ; infra/main y crée toutes ses ressources). northeurope : PostgreSQL
    B_Standard_B1ms refusé à cet abonnement en francecentral (SkuNotAvailable) et westeurope fermé aux
    nouveaux abonnements. Changer de région = recréer rg-mire-prod (docs/deploiement-azure.md).
  EOT
  type        = string
  default     = "northeurope"
}

variable "github_oidc_repository" {
  description = <<-EOT
    Dépôt GitHub autorisé à s'authentifier, tel qu'il apparaît dans le « subject » du jeton OIDC. GitHub y
    met les identifiants immuables du propriétaire et du dépôt (<propriétaire>@<id>/<dépôt>@<id>) : un dépôt
    supprimé puis recréé sous le même nom n'obtient pas l'accès. Voir l'erreur AADSTS700213 du login pour
    la valeur exacte.
  EOT
  type        = string
  default     = "jurix99@47568170/mire@1463048112"
}
