locals {
  prefix = "investment-${var.environment}"
}

# -----------------------------------------------------------
# Securities（銘柄マスタ）
# -----------------------------------------------------------
resource "aws_dynamodb_table" "securities" {
  name         = "${local.prefix}-Securities"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "ticker"
  range_key    = "asset_class"

  attribute {
    name = "ticker"
    type = "S"
  }
  attribute {
    name = "asset_class"
    type = "S"
  }

  point_in_time_recovery { enabled = true }
  deletion_protection_enabled = var.environment == "prod"
}

# -----------------------------------------------------------
# PriceHistory（株価ヒストリカル）TTL=5年
# -----------------------------------------------------------
resource "aws_dynamodb_table" "price_history" {
  name         = "${local.prefix}-PriceHistory"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "ticker"
  range_key    = "date"

  attribute {
    name = "ticker"
    type = "S"
  }
  attribute {
    name = "date"
    type = "S"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  point_in_time_recovery { enabled = true }
  deletion_protection_enabled = var.environment == "prod"
}

# -----------------------------------------------------------
# Fundamentals（財務・配当データ）TTL=7年
# -----------------------------------------------------------
resource "aws_dynamodb_table" "fundamentals" {
  name         = "${local.prefix}-Fundamentals"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "ticker"
  range_key    = "disc_date"

  attribute {
    name = "ticker"
    type = "S"
  }
  attribute {
    name = "disc_date"
    type = "S"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  point_in_time_recovery { enabled = true }
  deletion_protection_enabled = var.environment == "prod"
}

# -----------------------------------------------------------
# Portfolio（保有銘柄）
# -----------------------------------------------------------
resource "aws_dynamodb_table" "portfolio" {
  name         = "${local.prefix}-Portfolio"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "user_id"
  range_key    = "ticker"

  attribute {
    name = "user_id"
    type = "S"
  }
  attribute {
    name = "ticker"
    type = "S"
  }

  point_in_time_recovery { enabled = true }
  deletion_protection_enabled = var.environment == "prod"
}

# -----------------------------------------------------------
# Candidates（候補生成履歴）GSI: run_date-score_total
# -----------------------------------------------------------
resource "aws_dynamodb_table" "candidates" {
  name         = "${local.prefix}-Candidates"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "run_id"
  range_key    = "ticker"

  attribute {
    name = "run_id"
    type = "S"
  }
  attribute {
    name = "ticker"
    type = "S"
  }
  attribute {
    name = "run_date"
    type = "S"
  }
  attribute {
    name = "score_total"
    type = "N"
  }

  global_secondary_index {
    name            = "run_date-score_total-index"
    hash_key        = "run_date"
    range_key       = "score_total"
    projection_type = "ALL"
  }

  point_in_time_recovery { enabled = true }
  deletion_protection_enabled = var.environment == "prod"
}

# -----------------------------------------------------------
# RunLogs（バッチ実行ログ）TTL=180日
# -----------------------------------------------------------
resource "aws_dynamodb_table" "run_logs" {
  name         = "${local.prefix}-RunLogs"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "run_id"
  range_key    = "stage"

  attribute {
    name = "run_id"
    type = "S"
  }
  attribute {
    name = "stage"
    type = "S"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  point_in_time_recovery { enabled = true }
  deletion_protection_enabled = var.environment == "prod"
}
