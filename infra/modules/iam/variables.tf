variable "environment" {
  type = string
}

variable "account_id" {
  type = string
}

variable "aws_region" {
  type = string
}

variable "dynamodb_table_arns" {
  type = map(string)
}

variable "config_bucket_arn" {
  type = string
}

variable "reports_bucket_arn" {
  type = string
}

variable "jquants_secret_arn" {
  type = string
}

variable "slack_secret_arn" {
  type = string
}

variable "edinet_secret_arn" {
  type = string
}

variable "sns_topic_arn" {
  type = string
}
