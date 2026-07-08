terraform {
  backend "s3" {
    bucket         = "investment-tfstate-YOUR_ACCOUNT_ID"
    key            = "envs/dev/terraform.tfstate"
    region         = "ap-northeast-1"
    dynamodb_table = "investment-tflock"
    encrypt        = true
  }
}
