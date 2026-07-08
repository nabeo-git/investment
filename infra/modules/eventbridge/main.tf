resource "aws_scheduler_schedule" "weekly" {
  name       = "investment-${var.environment}-weekly"
  group_name = "default"

  # 毎週土曜 06:00 JST = 金曜 21:00 UTC
  schedule_expression          = "cron(0 21 ? * FRI *)"
  schedule_expression_timezone = "UTC"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = var.state_machine_arn
    role_arn = var.scheduler_role_arn

    input = jsonencode({
      triggered_by = "EventBridge Scheduler"
    })
  }

  # dev環境はデフォルト無効（手動実行で検証）
  state = var.environment == "prod" ? "ENABLED" : "DISABLED"
}
