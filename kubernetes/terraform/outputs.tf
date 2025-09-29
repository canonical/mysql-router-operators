output "app_name" {
  description = "Name of the MySQL Router K8s application"
  value       = juju_application.mysql_router.name
}

output "provides" {
  description = "Map of all the provided endpoints"
  value = {
    database          = "database"
    grafana-dashboard = "grafana-dashboard"
    metrics-endpoint  = "metrics-endpoint"
  }
}

output "requires" {
  description = "Map of all the required endpoints"
  value = {
    backend-database = "backend-database"
    certificates     = "certificates"
    logging          = "logging"
    tracing          = "tracing"
  }
}
