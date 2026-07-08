output "function_arns" {
  value = {
    ingest       = aws_lambda_function.ingest.arn
    screen_score = aws_lambda_function.screen_score.arn
    explain      = aws_lambda_function.explain.arn
    publish      = aws_lambda_function.publish.arn
  }
}

output "function_names" {
  value = {
    ingest       = aws_lambda_function.ingest.function_name
    screen_score = aws_lambda_function.screen_score.function_name
    explain      = aws_lambda_function.explain.function_name
    publish      = aws_lambda_function.publish.function_name
  }
}
