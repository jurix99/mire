# Infrastructure de production de Mire, appliquée par GitHub Actions (.github/workflows/deploy.yml) en OIDC.
# Prérequis : infra/bootstrap (state, identité de la CI, rg-mire-prod). Voir docs/deploiement-azure.md.
#
# Aucune valeur de secret ici ni dans le state, sauf le mot de passe administrateur PostgreSQL généré par
# Terraform (voir postgres.tf). Les autres secrets sont posés à la main dans Key Vault ; Terraform n'en connaît
# que les noms (références Key Vault des Container Apps, en ID sans version).

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

  # storage_account_name : passé par la CI (-backend-config, variable GitHub TFSTATE_STORAGE_ACCOUNT)
  backend "azurerm" {
    resource_group_name = "rg-mire-tfstate"
    container_name      = "tfstate"
    key                 = "mire-prod.tfstate"
    use_oidc            = true
    use_azuread_auth    = true # le compte de stockage n'accepte pas de clé partagée
  }
}

# Authentification par les variables d'environnement ARM_CLIENT_ID / ARM_TENANT_ID / ARM_SUBSCRIPTION_ID /
# ARM_USE_OIDC posées par la CI (en local : az login + ARM_SUBSCRIPTION_ID).
provider "azurerm" {
  features {
    key_vault {
      purge_soft_delete_on_destroy    = false # purge protection : jamais de purge
      recover_soft_deleted_key_vaults = true
    }
  }
  resource_provider_registrations = "none" # fait par le bootstrap (la CI n'a pas de droit sur l'abonnement)
  storage_use_azuread             = true
}

data "azurerm_client_config" "current" {}

data "azurerm_resource_group" "prod" {
  name = var.resource_group_name
}

# Noms uniques dans tout Azure (coffre, serveur PostgreSQL) : suffixe aléatoire stable
resource "random_string" "suffix" {
  length  = 5
  upper   = false
  special = false
}

locals {
  location = data.azurerm_resource_group.prod.location
  rg       = data.azurerm_resource_group.prod.name
  suffix   = random_string.suffix.result
  tags = {
    app          = "mire"
    env          = "prod"
    "managed-by" = "terraform"
  }
}
