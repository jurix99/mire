# Variables passées par la CI (TF_VAR_… depuis les variables GitHub du dépôt). Aucune n'est un secret.

variable "resource_group_name" {
  description = "Groupe de ressources créé par le bootstrap"
  type        = string
  default     = "rg-mire-prod"
}

# --- Déploiement ---------------------------------------------------------------------------------

variable "apps_enabled" {
  description = <<-EOT
    Crée les Container Apps et les jobs. false au tout premier apply : le coffre est créé vide, les secrets
    y sont posés à la main, puis on passe à true (variable GitHub MIRE_APPS_ENABLED).
  EOT
  type        = bool
  default     = false
}

variable "ghcr_owner" {
  description = "Propriétaire des images sur GHCR (ghcr.io/<owner>/mire-api) et identifiant du PAT read:packages"
  type        = string
  default     = "jurix99"
}

variable "api_image_tag" {
  description = "Tag de l'image mire-api (SHA court du commit, passé par la CI)"
  type        = string
  default     = "latest"
}

variable "worker_image_tag" {
  description = "Tag de l'image mire-worker (SHA court du commit, passé par la CI)"
  type        = string
  default     = "latest"
}

variable "custom_domain" {
  description = "Domaine personnalisé de l'API (ex. mire.example.fr) ; vide = URL *.azurecontainerapps.io"
  type        = string
  default     = ""
}

# --- Planification -------------------------------------------------------------------------------

variable "daily_cron" {
  description = <<-EOT
    Cron du job mire-daily, en UTC (Container Apps ne gère pas les fuseaux horaires). « 30 5 * * * » =
    06:30 à Paris en hiver (UTC+1), 07:30 en été (UTC+2). Pour 06:30 toute l'année, changer à la main aux
    passages à l'heure d'été / d'hiver (« 30 4 * * * » en été).
  EOT
  type        = string
  default     = "30 5 * * *"
}

# --- Observabilité et budget ---------------------------------------------------------------------

variable "log_daily_quota_gb" {
  description = "Plafond d'ingestion quotidien de Log Analytics (Go) ; au-delà, les journaux du jour sont perdus"
  type        = number
  default     = 0.1
}

variable "budget_amount" {
  description = "Budget mensuel du groupe de ressources, dans la devise de facturation (EUR)"
  type        = number
  default     = 5
}

variable "budget_alert_email" {
  description = "Adresse des alertes de budget (variable GitHub BUDGET_ALERT_EMAIL, jamais dans le dépôt)"
  type        = string
  sensitive   = true # adresses personnelles : masquées dans les plans (commentaire de PR)
}

variable "budget_start_date" {
  description = "Premier jour du mois de départ du budget (ISO 8601) ; ignoré après la création"
  type        = string
  default     = "2026-11-01T00:00:00Z"
}

# --- Configuration de l'application (non secrète) -----------------------------------------------

variable "gemini_model" {
  type    = string
  default = "gemini-3.8-flash"
}

variable "gemini_fallback_model" {
  type    = string
  default = "gemini-3.5-flash-lite,gemini-3.1-flash-lite"
}

variable "gemini_eval_model" {
  type    = string
  default = "gemini-3.5-flash-lite,gemini-3.1-flash-lite"
}

variable "api_min_replicas" {
  description = <<-EOT
    Répliques de l'API toujours allumées. 0 (défaut) : l'API s'arrête quand personne ne s'en sert (quota gratuit),
    la première visite attend son démarrage (une dizaine de secondes). 1 : pas d'attente, mais une réplique
    allumée en permanence sort du quota gratuit de Container Apps (coût mensuel à vérifier dans le portail).
  EOT
  type        = number
  default     = 0
  validation {
    condition     = contains([0, 1], var.api_min_replicas)
    error_message = "api_min_replicas : 0 ou 1"
  }
}

variable "trial_days" {
  description = "Essai Pro offert à la création du compte, sans carte bancaire, en jours (0 = pas d'essai)"
  type        = number
  default     = 0
  validation {
    condition     = var.trial_days >= 0 && var.trial_days <= 60
    error_message = "trial_days : entre 0 et 60"
  }
}

variable "billing_enabled" {
  description = "Offre Free / Pro avec Stripe : exige les secrets stripe-secret-key et stripe-webhook-secret"
  type        = bool
  default     = false
}

variable "stripe_price_monthly" {
  type    = string
  default = ""
}

variable "stripe_price_pass_3m" {
  type    = string
  default = ""
}

variable "pro_user_emails" {
  description = "Comptes toujours Pro (variable GitHub, jamais dans le dépôt)"
  type        = string
  sensitive   = true # adresses personnelles : masquées dans les plans (commentaire de PR)
  default     = ""
}

variable "admin_emails" {
  description = "Administrateurs du radar partagé (variable GitHub, jamais dans le dépôt)"
  type        = string
  sensitive   = true # adresses personnelles : masquées dans les plans (commentaire de PR)
  default     = ""
}

variable "match_top_k" {
  description = "Nombre d'offres présélectionnées par similarité et envoyées à Gemini, par utilisateur"
  type        = number
  default     = 50
}

# --- Agent de recherche --------------------------------------------------------------------------

variable "agent_enabled" {
  description = <<-EOT
    Crée les jobs planifiés de l'agent de recherche (Claude Code dans l'image mire-agent) : la découverte ne
    dépend plus d'un poste allumé. Exige le secret anthropic-api-key dans Key Vault (posé à la main, voir
    docs/deploiement-azure.md). false : l'agent ne tourne que depuis un poste (agent/run-agent.ps1).
  EOT
  type        = bool
  default     = false
}

variable "agent_image_tag" {
  description = "Tag de l'image mire-agent (SHA court du commit, passé par la CI)"
  type        = string
  default     = "latest"
}

variable "agent_model" {
  description = "Modèle de l'agent (ex. claude-sonnet-5-5) ; vide : modèle par défaut de Claude Code"
  type        = string
  default     = ""
}

variable "agent_max_budget_usd" {
  description = "Plafond de dépense d'UN passage de l'agent, en dollars (--max-budget-usd de Claude Code)"
  type        = number
  default     = 5
}

variable "agent_total_budget_usd" {
  description = "Plafond d'une exécution complète d'un job de l'agent (plusieurs passages en rattrapage), en dollars"
  type        = number
  default     = 20
}

variable "agent_schedules" {
  description = <<-EOT
    Jobs planifiés de l'agent : type de passage (decouverte | offres | recherche | rattrapage) -> cron, en UTC.
    Défaut (heure de Paris en hiver) : découverte par les levées le lundi 07:43, recherche partant des offres le
    mercredi 06:00, offres hors ATS le jeudi 06:00.
    Coût maximal par semaine : nombre de passages x agent_max_budget_usd.
  EOT
  type        = map(string)
  default = {
    decouverte = "43 6 * * 1"
    recherche  = "0 5 * * 3"
    offres     = "0 5 * * 4"
  }
  validation {
    condition     = alltrue([for k in keys(var.agent_schedules) : contains(["decouverte", "offres", "recherche", "rattrapage"], k)])
    error_message = "agent_schedules : clés possibles decouverte, offres, recherche, rattrapage"
  }
}

# --- Réseau --------------------------------------------------------------------------------------

variable "vnet_address_space" {
  type    = string
  default = "10.40.0.0/16"
}

variable "aca_subnet_prefix" {
  description = "Sous-réseau de l'environnement Container Apps (profils de charge : /27 au minimum)"
  type        = string
  default     = "10.40.0.0/26"
}

variable "pg_subnet_prefix" {
  type    = string
  default = "10.40.1.0/28"
}

variable "postgres_zone" {
  description = "Zone de disponibilité du serveur PostgreSQL (1, 2 ou 3) ; en changer si la création échoue (SkuNotAvailable)"
  type        = string
  default     = "1"
}

variable "postgres_private_dns_zone" {
  description = <<-EOT
    Zone DNS privée du serveur PostgreSQL (accès privé par sous-réseau délégué). Elle doit se terminer par
    .postgres.database.azure.com ; privatelink.postgres.database.azure.com est le nom prévu pour les private
    endpoints, d'où un nom propre à Mire.
  EOT
  type        = string
  default     = "mire.private.postgres.database.azure.com"
}
