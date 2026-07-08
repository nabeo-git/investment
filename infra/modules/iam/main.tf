locals {
  prefix = "investment-${var.environment}"
}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

# -----------------------------------------------------------
# lambda-ingest ロール
# -----------------------------------------------------------
resource "aws_iam_role" "ingest" {
  name               = "${local.prefix}-lambda-ingest"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy" "ingest" {
  role = aws_iam_role.ingest.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["dynamodb:PutItem", "dynamodb:BatchWriteItem", "dynamodb:Query", "dynamodb:UpdateItem"]
        Resource = [
          var.dynamodb_table_arns["securities"],
          var.dynamodb_table_arns["price_history"],
          var.dynamodb_table_arns["fundamentals"],
          var.dynamodb_table_arns["run_logs"],
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "${var.config_bucket_arn}/*"
      },
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = var.jquants_secret_arn
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:${var.aws_region}:${var.account_id}:log-group:/aws/lambda/${local.prefix}-ingest:*"
      },
    ]
  })
}

# -----------------------------------------------------------
# lambda-screen-score ロール
# -----------------------------------------------------------
resource "aws_iam_role" "screen_score" {
  name               = "${local.prefix}-lambda-screen-score"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy" "screen_score" {
  role = aws_iam_role.screen_score.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["dynamodb:Query", "dynamodb:GetItem", "dynamodb:Scan"]
        Resource = [
          var.dynamodb_table_arns["securities"],
          var.dynamodb_table_arns["price_history"],
          var.dynamodb_table_arns["fundamentals"],
          var.dynamodb_table_arns["portfolio"],
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:PutItem", "dynamodb:BatchWriteItem", "dynamodb:UpdateItem"]
        Resource = [var.dynamodb_table_arns["candidates"], var.dynamodb_table_arns["run_logs"]]
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "${var.config_bucket_arn}/*"
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:${var.aws_region}:${var.account_id}:log-group:/aws/lambda/${local.prefix}-screen-score:*"
      },
    ]
  })
}

# -----------------------------------------------------------
# lambda-explain ロール
# -----------------------------------------------------------
resource "aws_iam_role" "explain" {
  name               = "${local.prefix}-lambda-explain"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy" "explain" {
  role = aws_iam_role.explain.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["dynamodb:Query", "dynamodb:GetItem"]
        Resource = [var.dynamodb_table_arns["candidates"], var.dynamodb_table_arns["fundamentals"]]
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:BatchWriteItem", "dynamodb:PutItem", "dynamodb:UpdateItem"]
        Resource = [var.dynamodb_table_arns["candidates"]]
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem"]
        Resource = [var.dynamodb_table_arns["securities"]]
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:PutItem", "dynamodb:UpdateItem"]
        Resource = [var.dynamodb_table_arns["run_logs"]]
      },
      {
        Effect   = "Allow"
        Action   = ["bedrock:InvokeModel"]
        Resource = "arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-pro-v1:0"
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "${var.config_bucket_arn}/*"
      },
      {
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:GetObject"]
        Resource = "${var.reports_bucket_arn}/*"
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:${var.aws_region}:${var.account_id}:log-group:/aws/lambda/${local.prefix}-explain:*"
      },
    ]
  })
}

# -----------------------------------------------------------
# lambda-publish ロール
# -----------------------------------------------------------
resource "aws_iam_role" "publish" {
  name               = "${local.prefix}-lambda-publish"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy" "publish" {
  role = aws_iam_role.publish.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "${var.reports_bucket_arn}/*"
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "${var.config_bucket_arn}/*"
      },
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = var.slack_secret_arn
      },
      {
        Effect   = "Allow"
        Action   = ["sns:Publish"]
        Resource = var.sns_topic_arn
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:PutItem", "dynamodb:UpdateItem"]
        Resource = var.dynamodb_table_arns["run_logs"]
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:${var.aws_region}:${var.account_id}:log-group:/aws/lambda/${local.prefix}-publish:*"
      },
    ]
  })
}

# -----------------------------------------------------------
# Step Functions 実行ロール
# -----------------------------------------------------------
resource "aws_iam_role" "sfn" {
  name               = "${local.prefix}-sfn-execution"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "states.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "sfn" {
  role = aws_iam_role.sfn.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["lambda:InvokeFunction"]
        Resource = [
          "arn:aws:lambda:${var.aws_region}:${var.account_id}:function:${local.prefix}-ingest",
          "arn:aws:lambda:${var.aws_region}:${var.account_id}:function:${local.prefix}-screen-score",
          "arn:aws:lambda:${var.aws_region}:${var.account_id}:function:${local.prefix}-explain",
          "arn:aws:lambda:${var.aws_region}:${var.account_id}:function:${local.prefix}-publish",
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["sns:Publish"]
        Resource = var.sns_topic_arn
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogDelivery", "logs:PutLogEvents", "logs:DescribeLogGroups", "logs:DescribeResourcePolicies"]
        Resource = "*"
      },
    ]
  })
}

# -----------------------------------------------------------
# EventBridge Scheduler 実行ロール
# -----------------------------------------------------------
resource "aws_iam_role" "scheduler" {
  name               = "${local.prefix}-scheduler-execution"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "scheduler.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "scheduler" {
  role = aws_iam_role.scheduler.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["states:StartExecution"]
      Resource = "arn:aws:states:${var.aws_region}:${var.account_id}:stateMachine:${local.prefix}-pipeline"
    }]
  })
}
