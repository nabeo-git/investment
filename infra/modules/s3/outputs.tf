output "config_bucket_name" {
  value = aws_s3_bucket.config.id
}

output "config_bucket_arn" {
  value = aws_s3_bucket.config.arn
}

output "reports_bucket_name" {
  value = aws_s3_bucket.reports.id
}

output "reports_bucket_arn" {
  value = aws_s3_bucket.reports.arn
}
