variable "aws_region" {
  description = "メインリージョン"
  type        = string
  default     = "ap-northeast-1"
}

variable "environment" {
  description = "環境名 (dev / prod)"
  type        = string
}

variable "account_id" {
  description = "AWSアカウントID"
  type        = string
}

variable "alert_email" {
  description = "SNSアラート通知先メールアドレス"
  type        = string
}
