# Plan de infra/main avec des fournisseurs simulés (aucun accès à Azure) : vérifie les expressions, les
# for_each, les conditions et quelques règles importantes. Lancé par la CI : terraform test (dans infra/main).

mock_provider "azurerm" {
  override_during = plan # valeurs simulées connues dès le plan
  mock_data "azurerm_resource_group" {
    defaults = {
      id       = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-mire-prod"
      location = "francecentral"
    }
  }
  mock_data "azurerm_client_config" {
    defaults = {
      tenant_id = "00000000-0000-0000-0000-000000000000"
    }
  }
  mock_resource "azurerm_key_vault" {
    defaults = {
      id        = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-mire-prod/providers/Microsoft.KeyVault/vaults/kv-mire-test"
      vault_uri = "https://kv-mire-test.vault.azure.net/"
    }
  }
  mock_resource "azurerm_container_app_environment" {
    defaults = {
      id             = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-mire-prod/providers/Microsoft.App/managedEnvironments/cae-mire-prod"
      default_domain = "test.francecentral.azurecontainerapps.io"
    }
  }
  mock_resource "azurerm_container_app" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-mire-prod/providers/Microsoft.App/containerApps/mire-api"
    }
  }
  mock_resource "azurerm_log_analytics_workspace" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-mire-prod/providers/Microsoft.OperationalInsights/workspaces/log-mire-prod"
    }
  }
  mock_resource "azurerm_virtual_network" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-mire-prod/providers/Microsoft.Network/virtualNetworks/vnet-mire-prod"
    }
  }
  mock_resource "azurerm_subnet" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-mire-prod/providers/Microsoft.Network/virtualNetworks/vnet-mire-prod/subnets/snet"
    }
  }
  mock_resource "azurerm_private_dns_zone" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-mire-prod/providers/Microsoft.Network/privateDnsZones/mire.private.postgres.database.azure.com"
    }
  }
  mock_resource "azurerm_postgresql_flexible_server" {
    defaults = {
      id   = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-mire-prod/providers/Microsoft.DBforPostgreSQL/flexibleServers/psql-mire-prod"
      fqdn = "psql-mire-prod.mire.private.postgres.database.azure.com"
    }
  }
  mock_resource "azurerm_postgresql_flexible_server_database" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-mire-prod/providers/Microsoft.DBforPostgreSQL/flexibleServers/psql-mire-prod/databases/mire"
    }
  }
  mock_resource "azurerm_user_assigned_identity" {
    defaults = {
      id           = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-mire-prod/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-mire-runtime"
      principal_id = "00000000-0000-0000-0000-000000000001"
    }
  }
}

mock_provider "random" {
  override_during = plan
}

variables {
  budget_alert_email = "alertes@example.invalid"
  api_image_tag      = "abc1234"
  worker_image_tag   = "abc1234"
}

run "premier_apply_sans_applications" {
  command = plan

  assert {
    condition     = length(azurerm_container_app.api) == 0 && length(azurerm_container_app_job.worker) == 0
    error_message = "apply initial : aucune Container App tant que les secrets ne sont pas posés"
  }
  assert {
    condition     = azurerm_postgresql_flexible_server.main.public_network_access_enabled == false
    error_message = "PostgreSQL doit être en accès privé"
  }
}

run "applications" {
  command = plan

  variables {
    apps_enabled    = true
    billing_enabled = true
    pro_user_emails = "pro@example.invalid"
  }

  assert {
    condition     = azurerm_container_app.api[0].template[0].container[0].image == "ghcr.io/jurix99/mire-api:abc1234"
    error_message = "image de l'API : tag passé par la CI"
  }
  assert {
    condition     = azurerm_container_app.api[0].template[0].min_replicas == 0
    error_message = "l'API doit pouvoir passer à zéro"
  }
  assert {
    condition = alltrue([for s in azurerm_container_app.api[0].secret :
    startswith(s.key_vault_secret_id, "https://kv-mire-test.vault.azure.net/secrets/") && s.value == null])
    error_message = "secrets : uniquement des références Key Vault, sans valeur"
  }
  assert {
    condition     = contains([for s in azurerm_container_app.api[0].secret : s.name], "stripe-webhook-secret")
    error_message = "billing_enabled : secrets Stripe attendus"
  }
  assert {
    condition     = azurerm_container_app_job.worker[0].event_trigger_config[0].scale[0].rules[0].custom_rule_type == "postgresql"
    error_message = "mire-worker : règle KEDA postgresql"
  }
  assert {
    condition     = azurerm_container_app_job.daily[0].schedule_trigger_config[0].cron_expression == "30 5 * * *"
    error_message = "mire-daily : cron par défaut"
  }
  assert {
    condition = contains([for e in azurerm_container_app.api[0].template[0].container[0].env : e.name if e.name == "PUBLIC_URL" && e.value == "https://mire-api.test.francecentral.azurecontainerapps.io"],
    "PUBLIC_URL")
    error_message = "PUBLIC_URL sans domaine personnalisé : URL azurecontainerapps.io"
  }
}

run "domaine_personnalise" {
  command = plan

  variables {
    apps_enabled  = true
    custom_domain = "mire.example.fr"
  }

  assert {
    condition     = azurerm_container_app_custom_domain.api[0].name == "mire.example.fr"
    error_message = "domaine personnalisé"
  }
  assert {
    condition     = output.public_url == "https://mire.example.fr"
    error_message = "PUBLIC_URL avec domaine personnalisé"
  }
}
