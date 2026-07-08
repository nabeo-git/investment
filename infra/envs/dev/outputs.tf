output "dynamodb_table_names" {
  value = module.dynamodb.table_names
}

output "config_bucket" {
  value = module.s3.config_bucket_name
}

output "reports_bucket" {
  value = module.s3.reports_bucket_name
}

output "state_machine_arn" {
  value = module.stepfunctions.state_machine_arn
}
