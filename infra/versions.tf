terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
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

# Bedrockはus-east-1のみ
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"

  default_tags {
    tags = {
      Project     = "InvestmentSystem"
      Environment = var.environment
      ManagedBy   = "Terraform"
    }
  }
}
