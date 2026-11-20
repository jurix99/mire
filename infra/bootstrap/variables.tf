variable "subscription_id" {
  description = "Abonnement Azure (az account show --query id -o tsv)"
  type        = string
}

variable "location" {
  description = "Région Azure"
  type        = string
  default     = "francecentral"
}

variable "github_repository" {
  description = "Dépôt GitHub autorisé à s'authentifier (propriétaire/nom)"
  type        = string
  default     = "jurix99/mire"
}
