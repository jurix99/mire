# Journaux des Container Apps (sortie standard, JSON) : Log Analytics plafonné, 30 jours. Pas d'Application
# Insights.

resource "azurerm_log_analytics_workspace" "main" {
  name                = "log-mire-prod"
  resource_group_name = local.rg
  location            = local.location
  sku                 = "PerGB2018"
  retention_in_days   = 30
  daily_quota_gb      = var.log_daily_quota_gb
  tags                = local.tags
}

# Budget mensuel du groupe de ressources : alertes par e-mail à 50 % et 100 % (coûts réels)
resource "azurerm_consumption_budget_resource_group" "main" {
  name              = "budget-mire-prod"
  resource_group_id = data.azurerm_resource_group.prod.id
  amount            = var.budget_amount
  time_grain        = "Monthly"

  time_period {
    start_date = var.budget_start_date
  }

  notification {
    enabled        = true
    threshold      = 50
    operator       = "GreaterThanOrEqualTo"
    threshold_type = "Actual"
    contact_emails = [var.budget_alert_email]
  }

  notification {
    enabled        = true
    threshold      = 100
    operator       = "GreaterThanOrEqualTo"
    threshold_type = "Actual"
    contact_emails = [var.budget_alert_email]
  }

  lifecycle {
    ignore_changes = [time_period] # la date de départ ne peut pas changer après la création
  }
}
