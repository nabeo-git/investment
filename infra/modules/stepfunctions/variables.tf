variable "environment" {
  type = string
}

variable "sfn_role_arn" {
  type = string
}

variable "function_arns" {
  type = map(string)
}

variable "sns_topic_arn" {
  type = string
}
