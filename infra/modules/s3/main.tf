locals {
  prefix = "investment-${var.environment}"
}

# -----------------------------------------------------------
# configバケット（設定ファイル・バージョニング有効）
# -----------------------------------------------------------
resource "aws_s3_bucket" "config" {
  bucket = "${local.prefix}-config-${var.account_id}"
}

resource "aws_s3_bucket_versioning" "config" {
  bucket = aws_s3_bucket.config.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "config" {
  bucket                  = aws_s3_bucket.config.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# -----------------------------------------------------------
# reportsバケット（レポートMarkdown保管）
# -----------------------------------------------------------
resource "aws_s3_bucket" "reports" {
  bucket = "${local.prefix}-reports-${var.account_id}"
}

resource "aws_s3_bucket_public_access_block" "reports" {
  bucket                  = aws_s3_bucket.reports.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# レポートは1年後に自動削除（S3コスト管理）
resource "aws_s3_bucket_lifecycle_configuration" "reports" {
  bucket = aws_s3_bucket.reports.id

  rule {
    id     = "expire-old-reports"
    status = "Enabled"

    filter {}

    expiration {
      days = 365
    }
  }
}
