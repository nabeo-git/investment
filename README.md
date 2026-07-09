# 投資候補抽出システム

J-Quants API で取得した日本株データをもとに、バフェット流の定量フィルタ・Bedrock（Claude）による定性評価を組み合わせて投資候補銘柄を週次抽出し、レポートをメール通知するパイプラインです。

- **発注は行いません**。候補提示と説明レポート生成のみ。
- スクリーニング条件・スコア重みは `config.yaml` で管理（Lambda 再デプロイ不要）。

---

## 利用料金

このシステムは複数の**有料 API** を使用します。稼働させると以下の費用が継続的に発生します。

### ランニングコスト（月額目安）

| サービス | プラン / 内容 | 月額目安 |
|---|---|---|
| **J-Quants API** | Light プラン以上必須（5年分の財務データ取得に必要） | 約 1,650 円〜 |
| **AWS Bedrock** | 週1回 × 10〜20 銘柄の説明・評価生成（claude-3-5-sonnet） | 約 50〜200 円 |
| **AWS Secrets Manager** | シークレット 3 件（J-Quants / Slack / EDINET） | 約 180 円 |
| **AWS その他** | Lambda / DynamoDB / S3 / Step Functions / SNS など | 約 50〜100 円 |
| **EDINET API** | 定性評価用の有価証券報告書取得（金融庁提供） | 無料（要登録） |
| **合計** | | **約 1,900〜2,200 円 / 月** |

> J-Quants の Light プランは5年分の Fundamentals（財務データ）を取得できる最低プランです。  
> 無料プランでは過去データが限られるため、バフェット流 5 年フィルタが機能しません。

### イニシャルコスト

| 項目 | 費用 |
|---|---|
| AWS インフラ構築（Terraform apply） | 無料 |
| 初期データ投入（5年分一括取得、所要 約 2 時間） | J-Quants の月額料金内 |
| その他セットアップ | 無料 |
| **合計** | **実質 0 円** |

---

## アーキテクチャ概要

```
EventBridge Scheduler（毎週土曜 06:00 JST）
    └─ Step Functions
        ├─ lambda-ingest       J-Quants API → DynamoDB（前回実行日以降を全日取得）
        ├─ lambda-screen-score バフェット定量フィルタ → Candidates テーブル
        ├─ lambda-explain      EDINET有報 + Bedrock 定性スコアリング → HTML レポート → S3
        └─ lambda-publish      署名付きURL生成 + SNS メール通知
```

**スクリーニング基準（バフェット流）**

| 基準 | 閾値 |
|---|---|
| ROE | 5年中 3年以上 ≥ 15% |
| EPS CAGR | ≥ 0%（連続赤字なし） |
| CFO / 純利益（5年平均） | ≥ 0.60 |
| (総資産 - 純資産) / 純利益 | ≤ 5.0 倍 |
| 営業利益率トレンド | 年 -3% 以内の低下 |
| 単元購入額 | ≤ 100,000 円 |
| 日次出来高 | ≥ 1,000 株 |

**最終スコア** ＝ 定量スコア × 0.4 ＋ 定性スコア × 0.6  
**定性評価**（Bedrock）＝ 経済的なお堀 / 能力の輪 / 経営者の誠実さ / 資本配分 / 成長への熱意

詳細設計 → [`docs/`](docs/)　運用アーキテクチャ → [`docs/05_operations_architecture.md`](docs/05_operations_architecture.md)

---

## 前提ツール

| ツール | バージョン | 用途 |
|---|---|---|
| Python | 3.12 以上 | Lambda ランタイム・初期投入スクリプト |
| AWS CLI v2 | 最新 | AWS 操作 |
| Terraform | >= 1.5 | インフラプロビジョニング |
| PowerShell | 5.1 以上 | Lambda ビルドスクリプト（Windows） |

---

## セットアップ手順

### 1. リポジトリクローン

```powershell
git clone <repo-url>
cd investment
```

### 2. AWS CLI 設定

```powershell
aws configure
# Default region: ap-northeast-1

aws sts get-caller-identity  # 動作確認
```

### 3. Terraform バックエンド用リソースを作成

tfstate を保管する S3 バケットとロック用 DynamoDB テーブルは Terraform 管理外のため先に手動作成します。

```powershell
$ACCOUNT_ID = aws sts get-caller-identity --query Account --output text

aws s3api create-bucket `
  --bucket "investment-tfstate-$ACCOUNT_ID" `
  --region ap-northeast-1 `
  --create-bucket-configuration LocationConstraint=ap-northeast-1

aws s3api put-bucket-versioning `
  --bucket "investment-tfstate-$ACCOUNT_ID" `
  --versioning-configuration Status=Enabled

aws dynamodb create-table `
  --table-name investment-tflock `
  --attribute-definitions AttributeName=LockID,AttributeType=S `
  --key-schema AttributeName=LockID,KeyType=HASH `
  --billing-mode PAY_PER_REQUEST `
  --region ap-northeast-1
```

### 4. backend.hcl を作成

`infra/envs/dev/backend.hcl.example` をコピーして `backend.hcl` を作成し、アカウント ID を記入します（`.gitignore` 対象）。

```powershell
cd infra/envs/dev
cp backend.hcl.example backend.hcl
# backend.hcl の <YOUR_ACCOUNT_ID> を実際のアカウント ID に書き換え
```

### 5. terraform.tfvars を作成

`infra/envs/dev/terraform.tfvars` を作成します（`.gitignore` 対象）。

```hcl
environment = "dev"
account_id  = "123456789012"
aws_region  = "ap-northeast-1"
alert_email = "your@example.com"
```

### 6. Secrets Manager に認証情報を登録

```powershell
# J-Quants API キー（必須）
aws secretsmanager create-secret `
  --name "investment/dev/jquants-api-key" `
  --secret-string "<J-Quants V2 APIキー>" `
  --region ap-northeast-1

# Slack Webhook URL（任意：Slack 通知を使う場合）
aws secretsmanager create-secret `
  --name "investment/dev/slack-webhook-url" `
  --secret-string "https://hooks.slack.com/services/..." `
  --region ap-northeast-1

# EDINET API キー（任意：定性評価に有価証券報告書を使う場合）
# 取得先: https://api.edinet-fsa.go.jp
aws secretsmanager create-secret `
  --name "investment-dev/edinet-api-key" `
  --secret-string "<EDINET Ocp-Apim-Subscription-Key>" `
  --region ap-northeast-1
```

### 7. Terraform apply

```powershell
cd infra/envs/dev
terraform init -backend-config=backend.hcl
terraform plan  -var-file=terraform.tfvars
terraform apply -var-file=terraform.tfvars
```

### 8. config.yaml を S3 にアップロード

```powershell
$ACCOUNT_ID = aws sts get-caller-identity --query Account --output text
aws s3 cp config.yaml "s3://investment-dev-config-$ACCOUNT_ID/config.yaml" --region ap-northeast-1
```

### 9. Lambda ビルド & デプロイ

```powershell
.\scripts\build_lambdas.ps1
```

### 10. 初期データ投入（全期間ヒストリカル）

DynamoDB に 2021-07〜現在の全データを一括投入します（所要時間：約 2 時間）。

```powershell
$env:PYTHONUTF8 = "1"
pip install boto3 jquants-api-client pandas
python scripts/full_history_load.py
```

オプション：

```powershell
python scripts/full_history_load.py --skip-prices      # 財務データのみ
python scripts/full_history_load.py --skip-fins        # 株価データのみ
python scripts/full_history_load.py --skip-securities  # 銘柄マスタをスキップ
```

---

## パイプラインの手動実行

```powershell
$ACCOUNT_ID = aws sts get-caller-identity --query Account --output text
aws stepfunctions start-execution `
  --state-machine-arn "arn:aws:states:ap-northeast-1:${ACCOUNT_ID}:stateMachine:investment-dev-pipeline" `
  --region ap-northeast-1
```

週次自動実行は EventBridge Scheduler（毎週土曜 06:00 JST）が担います。  
dev 環境はデフォルト `DISABLED`。有効化する場合は `infra/modules/eventbridge/main.tf` の `state` を変更して `terraform apply`。

---

## スクリーニング条件の変更

`config.yaml` を編集して S3 に再アップロードするだけで次回実行から反映されます（Lambda 再デプロイ不要）。

```powershell
# 編集後
aws s3 cp config.yaml "s3://investment-dev-config-$ACCOUNT_ID/config.yaml" --region ap-northeast-1
```

主要パラメータ：

| パラメータ | デフォルト | 説明 |
|---|---|---|
| `screening.min_roe` | 0.15 | ROE 下限（15%） |
| `screening.min_roe_years` | 3 | 5年中 ROE 基準を満たす年数 |
| `screening.min_cfo_quality` | 0.60 | CFO / 純利益 5年平均の下限 |
| `screening.max_debt_to_earnings` | 5.0 | (総資産-純資産) / 純利益 上限 |
| `screening.max_margin_decline` | -0.03 | 営業利益率トレンドの下限（年次） |
| `scoring.qualitative_weight` | 0.60 | 最終スコアにおける定性スコアの比重 |
| `candidates.top_n` | 10 | 最終候補数 |
| `candidates.per_sector` | 2 | 業種ごとの選出数 |
| `valuation.margin_of_safety_threshold` | 0.25 | 安全域（25% 以上で「買い候補」） |

---

## Lambda のみ更新する場合

インフラ変更なしでコードだけ更新する場合は Terraform 不要です。

```powershell
.\scripts\build_lambdas.ps1 -Only ingest
.\scripts\build_lambdas.ps1 -Only screen_score,explain,publish
```

---

## ディレクトリ構成

```
investment/
├── config.yaml                    # スクリーニング・スコア設定
├── docs/                          # 設計ドキュメント
│   ├── 01_requirements.md
│   ├── 02_basic_design.md
│   ├── 03_database_design.md
│   ├── 04_infrastructure_design.md
│   └── 05_operations_architecture.md
├── infra/                         # Terraform
│   ├── backend.tf                 # tfstate バックエンド（bucket は backend.hcl で渡す）
│   ├── envs/dev/
│   │   ├── main.tf                # モジュール呼び出しエントリポイント
│   │   ├── variables.tf
│   │   ├── backend.hcl.example    # ← コピーして backend.hcl を作成（.gitignore 対象）
│   │   └── terraform.tfvars       # ← 自分で作成（.gitignore 対象）
│   └── modules/                   # dynamodb / s3 / secrets / sns / iam / lambda / stepfunctions / eventbridge / cloudwatch
├── lambda_src/                    # Lambda ソースコード（pip 生成物は .gitignore）
│   ├── ingest/
│   ├── screen_score/
│   ├── explain/
│   └── publish/
└── scripts/
    ├── build_lambdas.ps1          # Lambda ビルド & S3 デプロイ
    └── full_history_load.py       # 初回一括データ投入
```
