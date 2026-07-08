# 投資支援システム

J-Quants API で取得した日本株データをもとに、ルールベースのスクリーニング・スコアリングで投資候補銘柄を週次抽出し、Claude（Bedrock）による選定理由レポートをメール通知するパイプラインです。

- **発注は行いません**。候補提示と説明生成のみ。
- スクリーニング条件・スコア重みは `config.yaml` で管理（Lambda 再デプロイ不要）。
- AWS 無料枠＋ Bedrock 費用のみ（月 100 円未満を目標）。

詳細設計は `docs/` を参照してください。

---

## アーキテクチャ概要

```
EventBridge Scheduler（毎週土曜 AM）
    └─ Step Functions
        ├─ lambda-ingest       J-Quants API → DynamoDB
        ├─ lambda-screen-score スクリーニング＋スコアリング → Candidates テーブル
        ├─ lambda-explain      Bedrock (Claude) → レポート Markdown 生成
        └─ lambda-publish      Google Drive PUT ＋ SNS メール通知
```

- **DynamoDB** 6 テーブル（Securities / PriceHistory / Fundamentals / Portfolio / Candidates / RunLogs）
- **S3** バケット：`config.yaml` 保管・Lambda デプロイ ZIP・レポートアーカイブ
- **Bedrock**：Claude Sonnet（`us-east-1`）
- **Secrets Manager**：J-Quants API キー / Google Drive サービスアカウント鍵

---

## 前提ツール

| ツール | バージョン | 用途 |
|---|---|---|
| Python | 3.12 以上 | スクリプト・Lambda ランタイム |
| pip | 最新 | パッケージ管理 |
| AWS CLI v2 | 最新 | AWS 操作 |
| Terraform | >= 1.5 | インフラプロビジョニング |
| PowerShell | 5.1 以上 | Lambda ビルドスクリプト |

---

## セットアップ手順

### 1. リポジトリクローン

```powershell
git clone <repo-url>
cd investment
```

### 2. Python 仮想環境・ローカル依存パッケージ

ローカルでのスクリプト実行（`full_history_load.py` 等）に必要です。

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1

pip install boto3 jquants-api-client pandas pyyaml pydantic
```

> Lambda 用パッケージ（`lambda_src/` 配下）は `scripts/build_lambdas.ps1` が自動インストールします。手動インストール不要。

### 3. AWS CLI 設定

```powershell
aws configure
# AWS Access Key ID     : <IAM ユーザーのキー>
# AWS Secret Access Key : <シークレットキー>
# Default region name   : ap-northeast-1
# Default output format : json
```

動作確認：

```powershell
aws sts get-caller-identity
```

### 4. Terraform バックエンド用リソースを手動作成

Terraform の state 管理に使う S3 バケットと DynamoDB テーブルは Terraform 管理外のため、先に手動で作成します。

```powershell
$ACCOUNT_ID = aws sts get-caller-identity --query Account --output text

# S3 バケット（tfstate 保管）
aws s3api create-bucket `
  --bucket "investment-tfstate-$ACCOUNT_ID" `
  --region ap-northeast-1 `
  --create-bucket-configuration LocationConstraint=ap-northeast-1

aws s3api put-bucket-versioning `
  --bucket "investment-tfstate-$ACCOUNT_ID" `
  --versioning-configuration Status=Enabled

# DynamoDB テーブル（tflock）
aws dynamodb create-table `
  --table-name investment-tflock `
  --attribute-definitions AttributeName=LockID,AttributeType=S `
  --key-schema AttributeName=LockID,KeyType=HASH `
  --billing-mode PAY_PER_REQUEST `
  --region ap-northeast-1
```

### 5. backend.tf のアカウント ID を更新

`infra/backend.tf` の `YOUR_ACCOUNT_ID` を実際の AWS アカウント ID に書き換えます。

```hcl
# infra/backend.tf
bucket = "investment-tfstate-<YOUR_ACCOUNT_ID>"
```

> **注意**：`backend.tf` は Terraform variable が使えないため、直接書き換えが必要です。アカウント ID はパブリックリポジトリに push しないよう注意してください（本リポジトリは private 運用を前提とします）。

### 6. terraform.tfvars の作成

`infra/envs/dev/terraform.tfvars` を作成します（gitignore 対象）。

```hcl
environment = "dev"
account_id  = "<YOUR_ACCOUNT_ID>"
aws_region  = "ap-northeast-1"
alert_email = "<通知先メールアドレス>"
```

### 7. Secrets Manager に認証情報を登録

```powershell
# J-Quants API キー
aws secretsmanager create-secret `
  --name "investment/dev/jquants-api-key" `
  --secret-string "<J-Quants APIキー>" `
  --region ap-northeast-1

# Google Drive サービスアカウント鍵（JSON ファイル）
aws secretsmanager create-secret `
  --name "investment/dev/google-drive-sa" `
  --secret-string (Get-Content "<service-account.json>" -Raw) `
  --region ap-northeast-1
```

### 8. Terraform apply

```powershell
cd infra/envs/dev

terraform init
terraform plan -var-file="terraform.tfvars"
terraform apply -var-file="terraform.tfvars"
```

### 9. config.yaml を S3 にアップロード

Terraform apply 後、Lambda が読み込む設定ファイルを S3 に配置します。

```powershell
$ACCOUNT_ID = aws sts get-caller-identity --query Account --output text

aws s3 cp config.yaml "s3://investment-dev-config-$ACCOUNT_ID/config.yaml" `
  --region ap-northeast-1
```

### 10. 初期データ投入（全期間ヒストリカル）

DynamoDB に 2021-07 〜 現在の全期間データを一括投入します（所要時間：約 2 時間）。

```powershell
$env:PYTHONUTF8 = "1"
python scripts/full_history_load.py
```

オプション：

```powershell
python scripts/full_history_load.py --skip-prices    # 財務データのみ
python scripts/full_history_load.py --skip-fins      # 株価データのみ
python scripts/full_history_load.py --skip-securities  # 銘柄マスタをスキップ
```

> 初期投入済みデータ（2026-07 時点）：Securities 4,437 件 / Fundamentals 91,137 件 / PriceHistory 5,261,006 件

---

## Lambda ビルドと手動デプロイ

```powershell
# 全 Lambda をビルドして S3 経由でデプロイ
.\scripts\build_lambdas.ps1 -Deploy

# 特定の Lambda のみ
.\scripts\build_lambdas.ps1 -Only ingest
.\scripts\build_lambdas.ps1 -Only screen_score,explain
```

> `build_lambdas.ps1` は Linux 向け（`manylinux2014_x86_64`）のバイナリを `pip install` するため、Lambda で動作します。

---

## スクリーニング設定の変更

`config.yaml` を編集後、S3 に再アップロードするだけで反映されます（Lambda 再デプロイ不要）。

```powershell
aws s3 cp config.yaml "s3://investment-dev-config-$ACCOUNT_ID/config.yaml" --region ap-northeast-1
```

主要パラメータ（`config.yaml`）：

| パラメータ | デフォルト | 説明 |
|---|---|---|
| `screening.max_unit_price_jpy` | 100,000 | 単元購入額上限（円） |
| `screening.min_dividend_yield` | 0.03 | 配当利回り下限（3%） |
| `screening.min_equity_ratio` | 0.40 | 自己資本比率下限（40%） |
| `screening.max_per` | 20.0 | PER 上限 |
| `scoring.weights.valuation` | 0.30 | 割安度スコア重み |
| `candidates.sector_mode` | true | 業種別ランキング |
| `candidates.per_sector` | 2 | 業種ごとの選出数 |

---

## パイプラインの手動実行

AWS コンソール または CLI から Step Functions を手動トリガーできます。

```powershell
aws stepfunctions start-execution `
  --state-machine-arn "arn:aws:states:ap-northeast-1:<ACCOUNT_ID>:stateMachine:investment-dev-pipeline" `
  --region ap-northeast-1
```

---

## ディレクトリ構成

```
investment/
├── config.yaml                  # スクリーニング・スコア設定（S3にも配置）
├── docs/                        # 設計書
│   ├── 01_requirements.md
│   ├── 02_basic_design.md
│   ├── 03_database_design.md
│   └── 04_infrastructure_design.md
├── infra/                       # Terraform
│   ├── backend.tf               # tfstate バックエンド設定
│   ├── versions.tf
│   ├── variables.tf
│   ├── envs/dev/                # dev 環境設定
│   └── modules/                 # 再利用可能モジュール
├── lambda_src/                  # Lambda ソースコード
│   ├── ingest/                  # J-Quants → DynamoDB
│   ├── screen_score/            # スクリーニング＋スコアリング
│   ├── explain/                 # Bedrock レポート生成
│   └── publish/                 # Drive PUT ＋ SNS 通知
├── scripts/
│   ├── full_history_load.py     # 全期間ヒストリカルデータ投入
│   └── build_lambdas.ps1        # Lambda ビルド＆デプロイ
└── bk/                          # 退避ファイル（git 管理外）
```

---

## 関連ドキュメント

- [要件定義書](docs/01_requirements.md)
- [基本設計書](docs/02_basic_design.md)
- [DB 設計書](docs/03_database_design.md)
- [インフラ設計書](docs/04_infrastructure_design.md)
