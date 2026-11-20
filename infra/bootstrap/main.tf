# Bootstrap de Mire sur Azure : lancé UNE fois, en local, par un propriétaire de l'abonnement (az login).
#
# Pourquoi pas dans la CI : c'est lui qui crée l'identité OIDC de la CI et lui donne ses droits. La CI ne peut
# pas se créer elle-même, et lui donner de quoi le faire (Owner / User Access Administrator sur l'abonnement)
# irait contre le principe du moindre privilège. Tout le reste (infra/main) est appliqué par GitHub Actions.
#
# Crée :
# - rg-mire-tfstate : compte de stockage (LRS, sans clé partagée, sans accès public, versioning) + conteneur
#   tfstate pour le state de infra/main ;
# - rg-mire-prod : groupe de ressources de l'application (rempli par infra/main) ;
# - id-mire-github : identité managée de GitHub Actions, avec des identifiants fédérés (OIDC, aucun secret) ;
# - les droits de cette identité, au plus juste (voir roles.tf) ;
# - l'enregistrement des fournisseurs de ressources (la CI, limitée à rg-mire-prod, ne peut pas le faire).
#
# State LOCAL (terraform.tfstate dans ce dossier, ignoré par git, à conserver en lieu sûr) : il ne contient
# aucun secret (pas de clé de stockage : shared_access_key_enabled = false).
#
#   cd infra/bootstrap
#   az login
#   terraform init
#   terraform apply -var subscription_id=<id>
#   terraform output        # valeurs à recopier dans les variables GitHub (docs/deploiement-azure.md)

terraform {
  required_version = ">= 1.9"
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.50"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.7"
    }
  }
}

provider "azurerm" {
  features {}
  subscription_id = var.subscription_id
  # Fournisseurs utilisés par infra/main, enregistrés ici (au niveau de l'abonnement)
  resource_provider_registrations = "core"
  resource_providers_to_register = [
    "Microsoft.App",
    "Microsoft.DBforPostgreSQL",
    "Microsoft.KeyVault",
    "Microsoft.OperationalInsights",
    "Microsoft.Consumption",
  ]
  # Le compte de stockage n'accepte pas de clé partagée : accès au plan de données par Entra ID
  storage_use_azuread = true
}

data "azurerm_client_config" "current" {}

locals {
  tags = {
    app          = "mire"
    env          = "prod"
    "managed-by" = "terraform"
  }
}

# --- State de infra/main -------------------------------------------------------------------------

resource "azurerm_resource_group" "tfstate" {
  name     = "rg-mire-tfstate"
  location = var.location
  tags     = local.tags
}

# Les noms de comptes de stockage sont uniques dans tout Azure : suffixe aléatoire
resource "random_string" "state_suffix" {
  length  = 6
  upper   = false
  special = false
}

resource "azurerm_storage_account" "tfstate" {
  name                            = "stmiretfstate${random_string.state_suffix.result}"
  resource_group_name             = azurerm_resource_group.tfstate.name
  location                        = azurerm_resource_group.tfstate.location
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  min_tls_version                 = "TLS1_2"
  https_traffic_only_enabled      = true
  allow_nested_items_to_be_public = false # aucun blob public
  shared_access_key_enabled       = false # accès par Entra ID uniquement (OIDC en CI)
  default_to_oauth_authentication = true
  tags                            = local.tags

  blob_properties {
    versioning_enabled = true # chaque écriture du state garde la version précédente
    delete_retention_policy {
      days = 30
    }
    container_delete_retention_policy {
      days = 30
    }
  }
}

resource "azurerm_storage_container" "tfstate" {
  name                  = "tfstate"
  storage_account_id    = azurerm_storage_account.tfstate.id
  container_access_type = "private"
}

# --- Application ---------------------------------------------------------------------------------

resource "azurerm_resource_group" "prod" {
  name     = "rg-mire-prod"
  location = var.prod_location
  tags     = local.tags
}

# --- Identité de GitHub Actions (OIDC) ------------------------------------------------------------

# Dans rg-mire-tfstate, pas dans rg-mire-prod : la CI (Contributor sur rg-mire-prod) ne peut ni la supprimer
# ni modifier ses propres identifiants fédérés.
resource "azurerm_user_assigned_identity" "github" {
  name                = "id-mire-github"
  resource_group_name = azurerm_resource_group.tfstate.name
  location            = azurerm_resource_group.tfstate.location
  tags                = local.tags
}

locals {
  github_subjects = {
    main         = "repo:${var.github_oidc_repository}:ref:refs/heads/main"
    prod         = "repo:${var.github_oidc_repository}:environment:prod" # deploy.yml (environnement GitHub prod)
    pull-request = "repo:${var.github_oidc_repository}:pull_request"     # ci.yml : terraform plan des PR
  }
}

resource "azurerm_federated_identity_credential" "github" {
  for_each                  = local.github_subjects
  name                      = "github-${each.key}"
  user_assigned_identity_id = azurerm_user_assigned_identity.github.id
  issuer                    = "https://token.actions.githubusercontent.com"
  audience                  = ["api://AzureADTokenExchange"]
  subject                   = each.value
}
