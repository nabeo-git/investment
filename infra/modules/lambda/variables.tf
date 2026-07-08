variable "environment" {
  type = string
}

variable "aws_region" {
  type = string
}

variable "lambda_role_arns" {
  type = map(string)
}

variable "config_bucket_name" {
  type = string
}

variable "reports_bucket_name" {
  type = string
}

variable "sns_topic_arn" {
  type = string
}
