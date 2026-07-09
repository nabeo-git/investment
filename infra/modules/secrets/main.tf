resource "aws_secretsmanager_secret" "jquants_api_key" {
  name                    = "investment/${var.environment}/jquants-api-key"
  description             = "J-Quants V2 APIキー"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret" "slack_webhook_url" {
  name                    = "investment/${var.environment}/slack-webhook-url"
  description             = "Slack Incoming Webhook URL"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret" "edinet_api_key" {
  name                    = "investment-${var.environment}/edinet-api-key"
  description             = "EDINET API サブスクリプションキー"
  recovery_window_in_days = 7
}
