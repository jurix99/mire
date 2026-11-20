# Container Apps (profil Consumption uniquement, environnement « workload profiles » intégré au VNet) :
# - mire-api      API + front, mise à zéro quand personne ne s'en sert ;
# - mire-migrate  job manuel : alembic upgrade head, lancé par la CI avant chaque mise à jour de l'API ;
# - mire-daily    job planifié (cron, UTC) : collecte, embeddings, présélection, évaluation ;
# - mire-worker   job déclenché par KEDA (scaler postgresql) quand des tâches sont en file.
# Images sur GHCR (privées) : tirées avec un PAT read:packages lu dans Key Vault (secret ghcr-pat).

resource "azurerm_container_app_environment" "main" {
  name                           = "cae-mire-prod"
  resource_group_name            = local.rg
  location                       = local.location
  log_analytics_workspace_id     = azurerm_log_analytics_workspace.main.id
  logs_destination               = "log-analytics"
  infrastructure_subnet_id       = azurerm_subnet.aca.id
  internal_load_balancer_enabled = false # ingress externe
  tags                           = local.tags

  workload_profile {
    name                  = "Consumption"
    workload_profile_type = "Consumption"
  }

  lifecycle {
    ignore_changes = [infrastructure_resource_group_name] # groupe d'infrastructure nommé par Azure
  }
}

locals {
  api_image    = "ghcr.io/${var.ghcr_owner}/mire-api:${var.api_image_tag}"
  worker_image = "ghcr.io/${var.ghcr_owner}/mire-worker:${var.worker_image_tag}"
  public_url   = var.custom_domain != "" ? "https://${var.custom_domain}" : "https://mire-api.${azurerm_container_app_environment.main.default_domain}"

  # Configuration commune (non secrète) de l'API et des jobs
  common_env = {
    RUN_BACKGROUND_THREADS = "false"
    LOG_FORMAT             = "json"
    GEMINI_MODEL           = var.gemini_model
    GEMINI_FALLBACK_MODEL  = var.gemini_fallback_model
    GEMINI_EVAL_MODEL      = var.gemini_eval_model
    BILLING_ENABLED        = tostring(var.billing_enabled)
    MATCH_TOP_K            = tostring(var.match_top_k)
  }
  # Adresses (variables sensibles) : blocs env statiques, un for_each ne peut pas porter de valeur sensible
  api_env = merge(local.common_env, {
    PUBLIC_URL           = local.public_url
    DEV_LOGIN_BYPASS     = "false"
    SEED_DEMO            = "false"
    COLLECT_TIME         = "06:30" # affiché dans l'interface ; la planification réelle est var.daily_cron
    STRIPE_PRICE_MONTHLY = var.stripe_price_monthly
    STRIPE_PRICE_PASS_3M = var.stripe_price_pass_3m
  })

  # Variable d'environnement -> secret (référence Key Vault)
  api_secret_env = merge({
    SESSION_SECRET         = "session-secret"
    LINKEDIN_CLIENT_ID     = "linkedin-client-id"
    LINKEDIN_CLIENT_SECRET = "linkedin-client-secret"
    GEMINI_API_KEY         = "gemini-api-key"
    AGENT_TOKEN            = "agent-token"
    DATABASE_URL           = "database-url"
    }, var.billing_enabled ? {
    STRIPE_SECRET_KEY     = "stripe-secret-key"
    STRIPE_WEBHOOK_SECRET = "stripe-webhook-secret"
  } : {})
  job_secret_env = {
    DATABASE_URL   = "database-url"
    GEMINI_API_KEY = "gemini-api-key"
  }
  daily_secret_env = merge(local.job_secret_env, { HEALTHCHECK_DAILY_URL = "healthcheck-daily-url" })
}

# --- API ------------------------------------------------------------------------------------------

resource "azurerm_container_app" "api" {
  count                        = var.apps_enabled ? 1 : 0
  name                         = "mire-api"
  resource_group_name          = local.rg
  container_app_environment_id = azurerm_container_app_environment.main.id
  revision_mode                = "Single"
  workload_profile_name        = "Consumption"
  tags                         = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.runtime.id]
  }

  registry {
    server               = "ghcr.io"
    username             = var.ghcr_owner
    password_secret_name = "ghcr-pat"
  }

  dynamic "secret" {
    for_each = toset(concat(values(local.api_secret_env), ["ghcr-pat"]))
    content {
      name                = secret.value
      key_vault_secret_id = local.secret_id[secret.value]
      identity            = azurerm_user_assigned_identity.runtime.id
    }
  }

  ingress {
    external_enabled           = true
    target_port                = 8000
    transport                  = "auto"
    allow_insecure_connections = false # HTTPS seulement (HTTP redirigé)

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = 0 # mise à zéro : dans le quota gratuit tant que l'API est peu utilisée
    max_replicas = 2

    http_scale_rule {
      name                = "http"
      concurrent_requests = "30"
    }

    container {
      name   = "api"
      image  = local.api_image
      cpu    = 0.5
      memory = "1Gi"

      dynamic "env" {
        for_each = local.api_env
        content {
          name  = env.key
          value = env.value
        }
      }
      dynamic "env" {
        for_each = local.api_secret_env
        content {
          name        = env.key
          secret_name = env.value
        }
      }

      env {
        name  = "PRO_USER_EMAILS"
        value = var.pro_user_emails
      }
      env {
        name  = "ADMIN_EMAILS"
        value = var.admin_emails
      }

      liveness_probe {
        transport = "HTTP"
        port      = 8000
        path      = "/healthz"
      }
      readiness_probe {
        transport = "HTTP"
        port      = 8000
        path      = "/readyz"
      }
    }
  }

  # Dépendances explicites : les références Key Vault ne passent que par l'URI du coffre, et `-target` (CI)
  # doit aussi créer PostgreSQL et les secrets générés
  depends_on = [
    azurerm_role_assignment.runtime_key_vault,
    azurerm_key_vault_secret.database_url,
    azurerm_key_vault_secret.keda_postgres,
  ]
}

# Domaine personnalisé : enregistrements DNS (CNAME + TXT asuid) à créer AVANT de renseigner custom_domain.
# Le certificat managé (gratuit) est créé et lié une fois par `az containerapp hostname bind` (voir
# docs/deploiement-azure.md) : azurerm ne gère pas les certificats managés, d'où ignore_changes sur la liaison.
resource "azurerm_container_app_custom_domain" "api" {
  count            = var.apps_enabled && var.custom_domain != "" ? 1 : 0
  name             = var.custom_domain
  container_app_id = azurerm_container_app.api[0].id

  lifecycle {
    ignore_changes = [certificate_binding_type, container_app_environment_certificate_id]
  }
}

# --- Jobs -----------------------------------------------------------------------------------------

resource "azurerm_container_app_job" "migrate" {
  count                        = var.apps_enabled ? 1 : 0
  name                         = "mire-migrate"
  resource_group_name          = local.rg
  location                     = local.location
  container_app_environment_id = azurerm_container_app_environment.main.id
  workload_profile_name        = "Consumption"
  replica_timeout_in_seconds   = 900
  replica_retry_limit          = 0
  tags                         = local.tags

  manual_trigger_config {
    parallelism              = 1
    replica_completion_count = 1
  }

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.runtime.id]
  }

  registry {
    server               = "ghcr.io"
    username             = var.ghcr_owner
    password_secret_name = "ghcr-pat"
  }

  dynamic "secret" {
    for_each = toset(["database-url", "ghcr-pat"])
    content {
      name                = secret.value
      key_vault_secret_id = local.secret_id[secret.value]
      identity            = azurerm_user_assigned_identity.runtime.id
    }
  }

  template {
    container {
      name    = "migrate"
      image   = local.api_image # même image que l'API : mêmes migrations que le code déployé
      command = ["python", "-m", "app.jobs", "migrate"]
      cpu     = 0.5
      memory  = "1Gi"

      env {
        name        = "DATABASE_URL"
        secret_name = "database-url"
      }
      env {
        name  = "LOG_FORMAT"
        value = "json"
      }
    }
  }

  # Dépendances explicites : les références Key Vault ne passent que par l'URI du coffre, et `-target` (CI)
  # doit aussi créer PostgreSQL et les secrets générés
  depends_on = [
    azurerm_role_assignment.runtime_key_vault,
    azurerm_key_vault_secret.database_url,
    azurerm_key_vault_secret.keda_postgres,
  ]
}

resource "azurerm_container_app_job" "daily" {
  count                        = var.apps_enabled ? 1 : 0
  name                         = "mire-daily"
  resource_group_name          = local.rg
  location                     = local.location
  container_app_environment_id = azurerm_container_app_environment.main.id
  workload_profile_name        = "Consumption"
  replica_timeout_in_seconds   = 3600
  replica_retry_limit          = 1
  tags                         = local.tags

  schedule_trigger_config {
    cron_expression          = var.daily_cron # UTC : voir la description de la variable
    parallelism              = 1
    replica_completion_count = 1
  }

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.runtime.id]
  }

  registry {
    server               = "ghcr.io"
    username             = var.ghcr_owner
    password_secret_name = "ghcr-pat"
  }

  dynamic "secret" {
    for_each = toset(concat(values(local.daily_secret_env), ["ghcr-pat"]))
    content {
      name                = secret.value
      key_vault_secret_id = local.secret_id[secret.value]
      identity            = azurerm_user_assigned_identity.runtime.id
    }
  }

  template {
    container {
      name    = "daily"
      image   = local.worker_image
      command = ["python", "-m", "app.jobs", "daily"]
      cpu     = 1
      memory  = "2Gi"

      dynamic "env" {
        for_each = local.common_env
        content {
          name  = env.key
          value = env.value
        }
      }
      dynamic "env" {
        for_each = local.daily_secret_env
        content {
          name        = env.key
          secret_name = env.value
        }
      }
      env {
        name  = "PRO_USER_EMAILS"
        value = var.pro_user_emails
      }
      env {
        name  = "ADMIN_EMAILS"
        value = var.admin_emails
      }
    }
  }

  # Dépendances explicites : les références Key Vault ne passent que par l'URI du coffre, et `-target` (CI)
  # doit aussi créer PostgreSQL et les secrets générés
  depends_on = [
    azurerm_role_assignment.runtime_key_vault,
    azurerm_key_vault_secret.database_url,
    azurerm_key_vault_secret.keda_postgres,
  ]
}

resource "azurerm_container_app_job" "worker" {
  count                        = var.apps_enabled ? 1 : 0
  name                         = "mire-worker"
  resource_group_name          = local.rg
  location                     = local.location
  container_app_environment_id = azurerm_container_app_environment.main.id
  workload_profile_name        = "Consumption"
  replica_timeout_in_seconds   = 1200 # > --max-seconds 900 : le worker finit sa tâche en cours
  replica_retry_limit          = 0    # les tâches en échec sont réessayées par la file elle-même
  tags                         = local.tags

  # KEDA (scaler postgresql) : une exécution quand des tâches sont dues, deux au plus en parallèle
  # (SELECT … FOR UPDATE SKIP LOCKED : jamais la même tâche deux fois)
  event_trigger_config {
    parallelism              = 1
    replica_completion_count = 1

    scale {
      min_executions              = 0
      max_executions              = 2
      polling_interval_in_seconds = 30

      rules {
        name             = "tasks-queue"
        custom_rule_type = "postgresql"
        metadata = {
          query            = "SELECT count(*) FROM tasks WHERE status = 'queued' AND run_after <= now()"
          targetQueryValue = "1"
        }
        authentication {
          secret_name       = "keda-postgres-connection"
          trigger_parameter = "connection"
        }
      }
    }
  }

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.runtime.id]
  }

  registry {
    server               = "ghcr.io"
    username             = var.ghcr_owner
    password_secret_name = "ghcr-pat"
  }

  dynamic "secret" {
    for_each = toset(concat(values(local.job_secret_env), ["ghcr-pat", "keda-postgres-connection"]))
    content {
      name                = secret.value
      key_vault_secret_id = local.secret_id[secret.value]
      identity            = azurerm_user_assigned_identity.runtime.id
    }
  }

  template {
    container {
      name    = "worker"
      image   = local.worker_image
      command = ["python", "-m", "app.jobs", "worker", "--max-seconds", "900"]
      cpu     = 1
      memory  = "2Gi"

      dynamic "env" {
        for_each = local.common_env
        content {
          name  = env.key
          value = env.value
        }
      }
      dynamic "env" {
        for_each = local.job_secret_env
        content {
          name        = env.key
          secret_name = env.value
        }
      }
      env {
        name  = "PRO_USER_EMAILS"
        value = var.pro_user_emails
      }
      env {
        name  = "ADMIN_EMAILS"
        value = var.admin_emails
      }
    }
  }

  # Dépendances explicites : les références Key Vault ne passent que par l'URI du coffre, et `-target` (CI)
  # doit aussi créer PostgreSQL et les secrets générés
  depends_on = [
    azurerm_role_assignment.runtime_key_vault,
    azurerm_key_vault_secret.database_url,
    azurerm_key_vault_secret.keda_postgres,
  ]
}
