output "lambda_role_arns" {
  value = {
    ingest       = aws_iam_role.ingest.arn
    screen_score = aws_iam_role.screen_score.arn
    explain      = aws_iam_role.explain.arn
    publish      = aws_iam_role.publish.arn
  }
}

output "sfn_role_arn" {
  value = aws_iam_role.sfn.arn
}

output "scheduler_role_arn" {
  value = aws_iam_role.scheduler.arn
}
