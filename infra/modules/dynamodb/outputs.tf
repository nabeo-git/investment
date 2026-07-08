output "table_names" {
  value = {
    securities    = aws_dynamodb_table.securities.name
    price_history = aws_dynamodb_table.price_history.name
    fundamentals  = aws_dynamodb_table.fundamentals.name
    portfolio     = aws_dynamodb_table.portfolio.name
    candidates    = aws_dynamodb_table.candidates.name
    run_logs      = aws_dynamodb_table.run_logs.name
  }
}

output "table_arns" {
  value = {
    securities    = aws_dynamodb_table.securities.arn
    price_history = aws_dynamodb_table.price_history.arn
    fundamentals  = aws_dynamodb_table.fundamentals.arn
    portfolio     = aws_dynamodb_table.portfolio.arn
    candidates    = aws_dynamodb_table.candidates.arn
    run_logs      = aws_dynamodb_table.run_logs.arn
  }
}
