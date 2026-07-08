# 投資支援システム インフラ設計書（Terraform）

| 項目 | 内容 |
|---|---|
| ドキュメントID | 04_infrastructure_design |
| バージョン | 1.1 |
| 最終更新 | 2026-06-24 |
| ステータス | ドラフト |

---

## 1. インフラ方針

### 1.1 IaCツール
- **Terraform**（HashiCorp Configuration Language）
- 状態管理：S3バックエンド + DynamoDBロック
- 環境分離：`envs/dev` と `envs/prod` の2系統（Phase1は `dev` のみで運用可）

### 1.2 リージョン戦略
| 用途 | リージョン |
|---|---|
| メイン全体 | `ap-northeast-1` |
| Bedrock | `us-east-1`（Amazon Nova Pro提供リージョン） |

### 1.3 採用しないもの
- EC2、ECS、API Gateway、CloudFront：要件に不要
- RDS：DynamoDB無料枠を採用
- VPC：Lambda（DynamoDB/S3/Bedrock/SNS等）はパブリックエンドポイント利用、VPC不要

---

## 2. AWSアーキテクチャ

### 2.1 構成図（テキスト）
```
                     ┌─────────────────────────┐
                     │ EventBridge Scheduler   │
                     │ (週次：土曜 06:00 JST)  │
                     └─────────────┬───────────┘
                                   ↓
                     ┌─────────────────────────┐
                     │  Step Functions          │
                     │  (state machine)         │
                     └─────────────┬───────────┘
                                   ↓
   ┌──────────────┬───────────────┼───────────────┬──────────────┐
   ↓              ↓               ↓               ↓              ↓
lambda-ingest  lambda-screen-  lambda-explain  lambda-publish   (各Lambda)
               score                                            CloudWatch Logs
   ↓              ↓               ↓               ↓
DynamoDB       DynamoDB        Bedrock          S3 reports
(6 tables)     (Candidates)    (us-east-1)      Google Drive
                                                SNS → Email
   ↑              ↑               ↑               ↑
   └──────────────┴───────────────┴───────────────┘
                       ↑
                S3 (config bucket)
                       ↑
                Secrets Manager
                (J-Quants / Slack Webhook URL)
```

### 2.2 利用サービス一覧

**AWSコスト**
| サービス | 用途 | 想定コスト/月 |
|---|---|---|
| Lambda × 4 | バッチ処理 | 無料枠内（0円） |
| Step Functions（標準WF） | オーケストレーション | <1円 |
| DynamoDB（6テーブル） | データ保管 | 無料枠内（0円） |
| S3（config・reports） | 設定/レポート保管 | <1円 |
| Bedrock（Amazon Nova Pro） | 説明生成 | 〜30円（週1回×10銘柄） |
| Secrets Manager | 認証情報（J-Quants / Slack Webhook） | 〜80円（2シークレット × $0.40） |
| EventBridge Scheduler | 週次起動 | 無料枠内 |
| SNS（メール通知） | 通知 | 無料枠内 |
| CloudWatch Logs | ログ | 無料枠内 |
| **AWSコスト合計** | | **〜110円/月** |

**データ取得コスト（AWSとは別枠）**
| サービス | 用途 | 月額 |
|---|---|---|
| J-Quants Light（本番推奨） | 日本株データ取得（最新・5年分） | **1,650円** |
| J-Quants Standard（任意） | 10年分・地合いデータ追加 | 3,300円（必要になった時点で移行） |
| EDINET API | 有価証券報告書・決算短信 | **0円（無料）** |
| **データコスト合計** | | **〜1,650円/月（Light採用時）** |

**月次総コスト目安**
| 構成 | 月額 |
|---|---|
| 開発中（J-Quants Free） | 〜110円（AWSのみ） |
| **本番最小（J-Quants Light）** | **〜1,760円** |
| 本番強化（J-Quants Standard） | 〜3,410円 |

※ Secrets Manager → SSM Parameter Store SecureStringに変更で約80円削減可。

---

## 3. Terraform構成

### 3.1 ディレクトリ構成
```
infra/
├── backend.tf                    # S3 backend, DynamoDB lock
├── versions.tf                   # provider versions
├── variables.tf                  # グローバル変数
│
├── envs/
│   ├── dev/
│   │   ├── main.tf               # dev環境のmodule呼び出し
│   │   ├── terraform.tfvars      # dev固有変数
│   │   └── outputs.tf
│   └── prod/
│       ├── main.tf
│       ├── terraform.tfvars
│       └── outputs.tf
│
├── modules/
│   ├── dynamodb/                 # 6テーブル定義
│   │   ├── main.tf
│   │   ├── variables.tf
│   │   └── outputs.tf
│   ├── s3/                       # config bucket / reports bucket
│   ├── lambda/                   # 4 Lambda関数（共通レイヤ含む）
│   ├── stepfunctions/            # state machine
│   ├── eventbridge/              # scheduler
│   ├── sns/                      # alerts topic + email subscription
│   ├── secrets/                  # Secrets Manager 2件
│   ├── iam/                      # 各Lambda用ロール
│   ├── bedrock/                  # cross-region invoke用policy
│   └── cloudwatch/               # log groups, metric filters
│
└── lambda_src/                   # Lambdaのソース（zip化対象）
    ├── ingest/
    ├── screen_score/
    ├── explain/
    ├── publish/
    └── layers/
        └── common/               # config loader, db client等
```

### 3.2 ステートバックエンド
```hcl
# backend.tf
terraform {
  backend "s3" {
    bucket         = "investment-tfstate-{account_id}"
    key            = "envs/${env}/terraform.tfstate"
    region         = "ap-northeast-1"
    dynamodb_table = "investment-tflock"
    encrypt        = true
  }
}
```

### 3.3 主要モジュールの責務

| モジュール | 出力リソース |
|---|---|
| `dynamodb` | 6テーブル（PITR・暗号化・TTL設定込） |
| `s3` | configバケット（バージョニング有効）／reportsバケット |
| `lambda` | 4関数 + 共通Layer。zip化はarchive_fileで実施 |
| `stepfunctions` | state machine定義（リトライ・Catch込） |
| `eventbridge` | Schedulerで週次起動 |
| `sns` | alerts/notifications Topic、email subscription |
| `secrets` | J-Quants認証、Slack Webhook URL（値は手動投入想定） |
| `iam` | Lambdaごとの最小権限ロール |

### 3.4 AWSリソースタグ設計

全リソースに以下の共通タグを付与する。**コスト配分タグとして `Project` を有効化することで、AWS Cost Explorerでシステム単位のコスト集計が可能になる。**

| タグキー | 値 | 目的 |
|---|---|---|
| `Project` | `InvestmentSystem` | コスト集計・リソース識別 |
| `Environment` | `dev` / `prod` | 環境別コスト分離 |
| `ManagedBy` | `Terraform` | 管理方法の明示 |

#### Terraformでのデフォルトタグ設定

```hcl
# versions.tf（またはprovider設定）
provider "aws" {
  region = "ap-northeast-1"

  default_tags {
    tags = {
      Project     = "InvestmentSystem"
      Environment = var.environment   # "dev" or "prod"
      ManagedBy   = "Terraform"
    }
  }
}
```

`default_tags` を使うと、このプロバイダ経由で作成した全リソースに自動でタグが付与される。個別リソースに追加タグを付ける場合は `tags` ブロックで上書き・追加可能。

> **Cost Explorerでの集計手順（初回のみ）**：AWS コンソール → Billing → Cost Allocation Tags → `Project` タグをアクティベート（反映まで24時間）。

---

## 4. IAM設計（最小権限）

### 4.1 `lambda-ingest` ロール
```
- dynamodb:PutItem, BatchWriteItem, Query
  Resource: Securities, PriceHistory, Fundamentals, RunLogs
- s3:GetObject
  Resource: config bucket/config.yaml
- secretsmanager:GetSecretValue
  Resource: jquants-api-key
- logs:CreateLogStream, PutLogEvents
```

### 4.2 `lambda-screen-score` ロール
```
- dynamodb:Query, GetItem (read)
  Resource: Securities, PriceHistory, Fundamentals, Portfolio
- dynamodb:PutItem, BatchWriteItem (write)
  Resource: Candidates, RunLogs
- s3:GetObject (config)
- logs:*
```

### 4.3 `lambda-explain` ロール
```
- dynamodb:Query
  Resource: Candidates, Fundamentals
- bedrock:InvokeModel
  Resource: arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-pro-v1:0
- s3:GetObject (config)
- s3:PutObject (reports bucket)
- logs:*
```

### 4.4 `lambda-publish` ロール
```
- s3:GetObject (reports bucket)
- s3:GetObject (config bucket)
- secretsmanager:GetSecretValue
  Resource: slack-webhook-url
- sns:Publish
  Resource: investment-notifications topic
- dynamodb:UpdateItem
  Resource: RunLogs
- logs:*
```

### 4.5 Step Functions実行ロール
```
- lambda:InvokeFunction
  Resource: 4 Lambda functions
- sns:Publish (error notification)
- logs:*
```

### 4.6 EventBridge Schedulerロール
```
- states:StartExecution
  Resource: state machine
```

---

## 5. Secrets / Config 管理

| Secret名 | 種別 | 内容 | 投入方法 |
|---|---|---|---|
| `investment/jquants-api-key` | String | J-Quants V2 APIキー（ダッシュボードで発行） | 手動（AWS CLI） |
| `investment/slack-webhook-url` | String | Slack Incoming Webhook URL | 手動（AWS CLI） |

> **V2 API注意**：2025年12月以降の新規登録はV2（APIキー方式）のみ。旧方式の `{mail, password, refresh_token}` JSON形式は使用しないこと。

### 5.1 投入手順例
```powershell
# J-Quants V2 APIキー（ダッシュボードの「APIキー」欄からコピー）
aws secretsmanager create-secret `
  --name investment/jquants-api-key `
  --secret-string "your-api-key-string-here"
```

### 5.2 config.yaml アップロード
```powershell
aws s3 cp config.yaml s3://investment-config-dev/config.yaml
```

---

## 6. ネットワーク

- VPC利用なし：全LambdaはAWSパブリックエンドポイント経由でDynamoDB/S3/Bedrock/SNSにアクセス
- 外部API（J-Quants、Google Drive）はインターネット経由
- 受信エンドポイント無し（APIサーバ無し）

---

## 7. コスト見積（詳細）

### 7.1 月額試算（Phase1運用時）
| サービス | 内訳 | 月額 |
|---|---|---|
| Lambda | 週4実行×4関数×平均60秒×512MB ≒ 数千GB-秒 | 0円（無料枠40万GB-秒） |
| Step Functions | 週52回×標準WF×8遷移 ≒ 32遷移/月 | <1円 |
| DynamoDB | 〜800MB＋オンデマンドread/write数千件 | 0円（無料枠内） |
| S3 | 数十MB＋数百PUT/GET | <1円 |
| Bedrock Sonnet 4.6 | 週1×10銘柄×Input 2k+Output 1k tokens | 〜30円 |
| Secrets Manager | 2シークレット × $0.40 | 〜80円 |
| SNS | 数件/月 | 0円 |
| CloudWatch Logs | <1GB | 0円（無料枠5GB） |
| EventBridge Scheduler | 週1回 | 0円（無料枠内） |
| **合計** | | **〜110円/月** |

### 7.2 コスト最適化オプション
- Secrets Manager → SSM Parameter Store SecureString に変更：**約80円削減**
- Bedrock Amazon Nova Pro → Amazon Nova Lite：**約20円削減**（精度トレードオフ）
- 上記2つ実施で **月10〜20円程度** まで圧縮可能

---

## 8. デプロイ手順

### 8.1 初回セットアップ
```powershell
# 1. tfstateバケット・ロックテーブルを手動作成（chicken-and-egg解消）
aws s3api create-bucket --bucket investment-tfstate-{account_id} --region ap-northeast-1 `
  --create-bucket-configuration LocationConstraint=ap-northeast-1
aws dynamodb create-table --table-name investment-tflock `
  --attribute-definitions AttributeName=LockID,AttributeType=S `
  --key-schema AttributeName=LockID,KeyType=HASH `
  --billing-mode PAY_PER_REQUEST

# 2. Terraform初期化
cd infra/envs/dev
terraform init

# 3. plan & apply
terraform plan -out=tfplan
terraform apply tfplan

# 4. Secrets / configを手動投入
aws secretsmanager create-secret --name investment/jquants-api-key --secret-string "your-v2-api-key"
aws secretsmanager create-secret --name investment/slack-webhook-url --secret-string "https://hooks.slack.com/services/..."
aws s3 cp config.yaml s3://investment-config-dev/config.yaml

# 5. SNSメール購読の確認メールに承認
```

### 8.2 更新時
```powershell
cd infra/envs/dev
terraform plan -out=tfplan
terraform apply tfplan
```

### 8.3 Lambda更新（コードのみ）
- `lambda_src/` 配下を更新 → `terraform apply` でzip再生成・デプロイ
- パラメータ調整のみなら **terraform不要**：`config.yaml` をS3上書きするだけ

---

## 9. 検証手順

| # | 検証項目 | 方法 |
|---|---|---|
| 1 | Terraform applyが成功 | `terraform apply` の戻り値 |
| 2 | DynamoDBテーブル6個が作成 | `aws dynamodb list-tables` |
| 3 | Lambda 4関数がデプロイ | `aws lambda list-functions` |
| 4 | Step Functions手動起動 | コンソールから`StartExecution` |
| 5 | 各Lambdaのログ確認 | CloudWatch Logs |
| 6 | DynamoDB `Candidates` に候補レコード生成 | `aws dynamodb scan` |
| 7 | S3 reportsバケットにMarkdown出力 | `aws s3 ls` |
| 8 | Google Drive `/InvestmentReports/` に保存 | Drive上で目視確認 |
| 9 | SNS通知メール受信 | 登録メールアドレスで受信 |
| 10 | EventBridge次回起動時刻が翌週末 | コンソール確認 |

---

## 10. 削除・ロールバック

| 操作 | 手順 |
|---|---|
| 環境全削除 | `terraform destroy`（PITR・S3バージョニングが残る場合は手動削除） |
| 設定ロールバック | S3 `config.yaml` の以前バージョンをrestore |
| Lambdaロールバック | `terraform apply` を以前のgit commitで再実行 |

---

## 11. Phase2拡張時のインフラ追加

| 機能 | 追加リソース |
|---|---|
| 米国株対応 | Secrets Manager（EDGAR用、不要かも）、IAM拡張 |
| マクロ・為替 | `MacroIndicators` DynamoDBテーブル追加 |
| 実購入INPUT | （任意）API Gateway + Lambda追加、または手動S3アップロード方式 |

---

## 12. 関連ドキュメント
- `01_requirements.md`
- `02_basic_design.md`
- `03_database_design.md`
