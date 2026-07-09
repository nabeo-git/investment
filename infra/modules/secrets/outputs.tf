output "jquants_api_key_arn" {
  value = aws_secretsmanager_secret.jquants_api_key.arn
}

output "slack_webhook_url_arn" {
  value = aws_secretsmanager_secret.slack_webhook_url.arn
}

output "edinet_api_key_arn" {
  value = aws_secretsmanager_secret.edinet_api_key.arn
}
