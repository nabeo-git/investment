terraform {
  backend "s3" {
    # バケット名・テーブル名はアカウント固有のため -backend-config で渡す
    # terraform init -backend-config=backend.hcl
    # backend.hcl は .gitignore 対象（infra/envs/dev/backend.hcl.example を参照）
    key     = "envs/dev/terraform.tfstate"
    region  = "ap-northeast-1"
    encrypt = true
  }
}
