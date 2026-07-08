variable "environment" {
  type = string
}

variable "sns_topic_arn" {
  type = string
}

variable "function_names" {
  type = map(string)
}
