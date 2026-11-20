variable "subscription_id" {
  description = "Abonnement Azure (az account show --query id -o tsv)"
  type        = string
}

variable "location" {
  description = "Région Azure"
  type        = string
  default     = "francecentral"
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
