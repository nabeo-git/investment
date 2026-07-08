terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  backend "s3" {
    bucket         = "investment-tfstate-YOUR_ACCOUNT_ID"
    key            = "envs/dev/terraform.tfstate"
    region         = "ap-northeast-1"
    dynamodb_table = "investment-tflock"
    encrypt        = true
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "InvestmentSystem"
      Environment = var.environment
      ManagedBy   = "Terraform"
    }
  }
}

module "dynamodb" {
  source      = "../../modules/dynamodb"
  environment = var.environment
}

module "s3" {
  source      = "../../modules/s3"
  environment = var.environment
  account_id  = var.account_id
}

module "secrets" {
  source      = "../../modules/secrets"
  environment = var.environment
}

module "sns" {
  source      = "../../modules/sns"
  environment = var.environment
  alert_email = var.alert_email
}

module "iam" {
  source              = "../../modules/iam"
  environment         = var.environment
  account_id          = var.account_id
  aws_region          = var.aws_region
  dynamodb_table_arns = module.dynamodb.table_arns
  config_bucket_arn   = module.s3.config_bucket_arn
  reports_bucket_arn  = module.s3.reports_bucket_arn
  jquants_secret_arn  = module.secrets.jquants_api_key_arn
  slack_secret_arn    = module.secrets.slack_webhook_url_arn
  sns_topic_arn       = module.sns.alerts_topic_arn
}

module "lambda" {
  source              = "../../modules/lambda"
  environment         = var.environment
  aws_region          = var.aws_region
  lambda_role_arns    = module.iam.lambda_role_arns
  config_bucket_name  = module.s3.config_bucket_name
  reports_bucket_name = module.s3.reports_bucket_name
  sns_topic_arn       = module.sns.alerts_topic_arn
}

module "stepfunctions" {
  source            = "../../modules/stepfunctions"
  environment       = var.environment
  sfn_role_arn      = module.iam.sfn_role_arn
  function_arns     = module.lambda.function_arns
  sns_topic_arn     = module.sns.alerts_topic_arn
}

module "eventbridge" {
  source             = "../../modules/eventbridge"
  environment        = var.environment
  state_machine_arn  = module.stepfunctions.state_machine_arn
  scheduler_role_arn = module.iam.scheduler_role_arn
}

module "cloudwatch" {
  source         = "../../modules/cloudwatch"
  environment    = var.environment
  sns_topic_arn  = module.sns.alerts_topic_arn
  function_names = module.lambda.function_names
}
