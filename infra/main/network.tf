# Réseau : un VNet, deux sous-réseaux délégués, une zone DNS privée pour PostgreSQL. Pas de NAT Gateway
# (payante) : les sorties vers Internet (Gemini, ATS, LinkedIn) passent par l'environnement Container Apps.

resource "azurerm_virtual_network" "main" {
  name                = "vnet-mire-prod"
  resource_group_name = local.rg
  location            = local.location
  address_space       = [var.vnet_address_space]
  tags                = local.tags
}

resource "azurerm_subnet" "aca" {
  name                 = "snet-aca"
  resource_group_name  = local.rg
  virtual_network_name = azurerm_virtual_network.main.name
  address_prefixes     = [var.aca_subnet_prefix]

  delegation {
    name = "container-apps"
    service_delegation {
      name    = "Microsoft.App/environments"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}

resource "azurerm_subnet" "pg" {
  name                 = "snet-pg"
  resource_group_name  = local.rg
  virtual_network_name = azurerm_virtual_network.main.name
  address_prefixes     = [var.pg_subnet_prefix]

  delegation {
    name = "postgresql"
    service_delegation {
      name    = "Microsoft.DBforPostgreSQL/flexibleServers"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}

resource "azurerm_private_dns_zone" "pg" {
  name                = var.postgres_private_dns_zone
  resource_group_name = local.rg
  tags                = local.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "pg" {
  name                  = "vnet-mire-prod"
  resource_group_name   = local.rg
  private_dns_zone_name = azurerm_private_dns_zone.pg.name
  virtual_network_id    = azurerm_virtual_network.main.id
  registration_enabled  = false
  tags                  = local.tags
}
