locals {
  prefix = "investment-${var.environment}"
}

# Lambda ロググループ（各関数）
resource "aws_cloudwatch_log_group" "lambda" {
  for_each          = var.function_names
  name              = "/aws/lambda/${each.value}"
  retention_in_days = 30
}

# エラーメトリクスフィルタ → SNSアラート
resource "aws_cloudwatch_metric_alarm" "lambda_errors" {
  for_each            = var.function_names
  alarm_name          = "${local.prefix}-${each.key}-errors"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 300
  statistic           = "Sum"
  threshold           = 0
  alarm_description   = "${each.value} でエラーが発生しました"
  alarm_actions       = [var.sns_topic_arn]

  dimensions = {
    FunctionName = each.value
  }
}
