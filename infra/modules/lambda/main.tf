locals {
  prefix   = "investment-${var.environment}"
  src_root = "${path.root}/../../../lambda_src"
}

# -----------------------------------------------------------
# Lambda ソースのzip化
# -----------------------------------------------------------
data "archive_file" "ingest" {
  type        = "zip"
  source_dir  = "${local.src_root}/ingest"
  output_path = "${path.module}/.build/ingest.zip"
}

data "archive_file" "screen_score" {
  type        = "zip"
  source_dir  = "${local.src_root}/screen_score"
  output_path = "${path.module}/.build/screen_score.zip"
}

data "archive_file" "explain" {
  type        = "zip"
  source_dir  = "${local.src_root}/explain"
  output_path = "${path.module}/.build/explain.zip"
}

data "archive_file" "publish" {
  type        = "zip"
  source_dir  = "${local.src_root}/publish"
  output_path = "${path.module}/.build/publish.zip"
}

# -----------------------------------------------------------
# 共通環境変数
# -----------------------------------------------------------
locals {
  common_env = {
    ENVIRONMENT         = var.environment
    CONFIG_BUCKET       = var.config_bucket_name
    REPORTS_BUCKET      = var.reports_bucket_name
    AWS_REGION_MAIN     = var.aws_region
    AWS_REGION_BEDROCK  = "us-east-1"
    SNS_TOPIC_ARN       = var.sns_topic_arn
  }
}

# -----------------------------------------------------------
# lambda-ingest
# -----------------------------------------------------------
resource "aws_lambda_function" "ingest" {
  function_name    = "${local.prefix}-ingest"
  role             = var.lambda_role_arns["ingest"]
  filename         = data.archive_file.ingest.output_path
  source_code_hash = data.archive_file.ingest.output_base64sha256
  handler          = "handler.handler"
  runtime          = "python3.12"
  timeout          = 900
  memory_size      = 512

  environment {
    variables = local.common_env
  }
}

# -----------------------------------------------------------
# lambda-screen-score
# -----------------------------------------------------------
resource "aws_lambda_function" "screen_score" {
  function_name    = "${local.prefix}-screen-score"
  role             = var.lambda_role_arns["screen_score"]
  filename         = data.archive_file.screen_score.output_path
  source_code_hash = data.archive_file.screen_score.output_base64sha256
  handler          = "handler.handler"
  runtime          = "python3.12"
  timeout          = 300
  memory_size      = 512

  environment {
    variables = local.common_env
  }
}

# -----------------------------------------------------------
# lambda-explain
# -----------------------------------------------------------
resource "aws_lambda_function" "explain" {
  function_name    = "${local.prefix}-explain"
  role             = var.lambda_role_arns["explain"]
  filename         = data.archive_file.explain.output_path
  source_code_hash = data.archive_file.explain.output_base64sha256
  handler          = "handler.handler"
  runtime          = "python3.12"
  timeout          = 900
  memory_size      = 512

  environment {
    variables = local.common_env
  }
}

# -----------------------------------------------------------
# lambda-publish
# -----------------------------------------------------------
resource "aws_lambda_function" "publish" {
  function_name    = "${local.prefix}-publish"
  role             = var.lambda_role_arns["publish"]
  filename         = data.archive_file.publish.output_path
  source_code_hash = data.archive_file.publish.output_base64sha256
  handler          = "handler.handler"
  runtime          = "python3.12"
  timeout          = 300
  memory_size      = 256

  environment {
    variables = local.common_env
  }
}
