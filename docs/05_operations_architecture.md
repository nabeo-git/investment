# 運用アーキテクチャ・構成管理ガイド

## 1. 全体構成マップ

```mermaid
flowchart TD
    subgraph repo["リポジトリ"]
        TF["infra/\n(Terraform)"]
        SRC["lambda_src/\n(Python)"]
        CFG["config.yaml"]
        SC["build_lambdas.ps1"]
    end

    subgraph pipeline["実行パイプライン"]
        EB["EventBridge\n毎週土曜 06:00 JST"]
        SFN["Step Functions\npipeline"]
        L1["Lambda\ningest"]
        L2["Lambda\nscreen-score"]
        L3["Lambda\nexplain"]
        L4["Lambda\npublish"]
        EB --> SFN --> L1 --> L2 --> L3 --> L4
    end

    subgraph data["データ層"]
        DDB["DynamoDB\n6テーブル"]
        S3["S3\nconfig / reports"]
        SM["Secrets Manager"]
    end

    subgraph notify["通知"]
        SNS["SNS\nEmail / Slack"]
        CW["CloudWatch\nAlarms"]
    end

    subgraph ext["外部 API"]
        BR["Bedrock\nclaude-3-5-sonnet"]
        ED["EDINET API"]
    end

    TF -->|terraform apply| pipeline
    TF -->|terraform apply| data
    TF -->|terraform apply| notify
    SRC -->|build_lambdas.ps1| L1
    SRC -->|build_lambdas.ps1| L2
    SRC -->|build_lambdas.ps1| L3
    SRC -->|build_lambdas.ps1| L4
    CFG -->|s3 cp| S3

    L1 & L2 & L3 & L4 --> DDB
    L1 & L3 --> SM
    L3 & L4 --> S3
    L3 --> BR
    L3 --> ED
    L4 --> SNS
    CW --> SNS
```

---

## 2. Terraform モジュール構成

```mermaid
flowchart TD
    ENTRY["infra/envs/dev/main.tf\nエントリポイント"]

    ENTRY --> M1["module: dynamodb\n→ DynamoDB 6テーブル"]
    ENTRY --> M2["module: s3\n→ config / reports バケット"]
    ENTRY --> M3["module: secrets\n→ Secrets Manager 2件"]
    ENTRY --> M4["module: sns\n→ アラート Topic"]
    ENTRY --> M5["module: iam\n→ IAM Role × 5"]
    ENTRY --> M6["module: lambda\n→ Lambda Function × 4"]
    ENTRY --> M7["module: stepfunctions\n→ State Machine"]
    ENTRY --> M8["module: eventbridge\n→ 週次スケジューラ"]
    ENTRY --> M9["module: cloudwatch\n→ Lambda エラーアラーム"]

    M1 -.->|テーブルARN| M5
    M2 -.->|バケットARN| M5
    M3 -.->|シークレットARN| M5
    M4 -.->|TopicARN| M5 & M7 & M9
    M5 -.->|RoleARN| M6 & M7 & M8
    M6 -.->|FuncARN| M7
    M7 -.->|StateMachineARN| M8
```

---

## 3. デプロイフロー

### 3-1. インフラ変更（Terraform）

```mermaid
flowchart TD
    A([インフラ変更]) --> B["infra/modules/ の .tf を編集"]
    B --> C["terraform init"]
    C --> D["terraform plan\n-var-file=terraform.tfvars"]
    D --> E{差分OK?}
    E -->|No| B
    E -->|Yes| F["terraform apply"]
    F --> G["S3 tfstate 更新\nDynamoDB ロック解放"]
    G --> H([完了])
```

> **現状注意**: Terraform 未インストールのため IAM 変更は  
> `aws iam put-role-policy` で直接適用。`iam/main.tf` には反映済みだが  
> **tfstate とドリフトあり**。Terraform 導入時は `terraform import` が必要。

### 3-2. Lambda コード変更

```mermaid
flowchart TD
    A([コード変更]) --> B["lambda_src/{name}/ を編集"]
    B --> C["git commit"]
    C --> D["build_lambdas.ps1\n-Only {name}"]
    D --> E["pip install\nmanylinux2014_x86_64"]
    E --> F["ZIP 作成\n__pycache__ 除外"]
    F --> G["S3 upload\nlambda-deploy/{name}.zip"]
    G --> H["aws lambda\nupdate-function-code"]
    H --> I([Lambda 更新完了])
```

### 3-3. 設定変更（config.yaml）

```mermaid
flowchart TD
    A([条件変更]) --> B["config.yaml を編集"]
    B --> C["aws s3 cp config.yaml\ns3://...-config/config.yaml"]
    C --> D([次回実行から反映\nLambda 再デプロイ不要])
```

---

## 4. 週次パイプライン実行フロー

```mermaid
flowchart TD
    EB["EventBridge\n毎週土曜 06:00 JST"]
    EB --> SFN["Step Functions 起動\nrun_id 生成"]

    SFN --> I1["ingest: J-Quants API キー取得\n(Secrets Manager)"]
    I1 --> I2["RunLogs から前回実行日取得\n(GetItem __state__)"]
    I2 --> I3["前回実行日翌日〜昨日の\n全営業日データ取得"]
    I3 --> I4["Securities / PriceHistory /\nFundamentals を DynamoDB 書き込み"]
    I4 --> I5["last_success_date 更新"]

    I5 --> S1["screen-score: config.yaml 読み込み"]
    S1 --> S2["Fundamentals から\n5年 FY データ取得"]
    S2 --> S3["バフェット定量フィルタ適用\nROE・EPS CAGR・CFO 品質 等"]
    S3 --> S4["スコアリング\n(定量 3 軸)"]
    S4 --> S5["Candidates テーブルへ書き込み"]

    S5 --> E1["explain: EDINET API キー取得\n(Secrets Manager)"]
    E1 --> E2["有価証券報告書テキスト取得\n(EDINET API)"]
    E2 --> E3["Bedrock で定性スコアリング\n(5 次元 × 0-10 点)"]
    E3 --> E4["最終スコア計算\n定量 × 0.4 + 定性 × 0.6"]
    E4 --> E5["HTML レポート生成\n→ S3 保存"]

    E5 --> P1["publish: 署名付き URL 生成"]
    P1 --> P2["SNS 通知\n(Email / Slack)"]
    P2 --> END([完了])

    SFN -->|エラー時| ERR["NotifyError\n(SNS 通知)"]
```

---

## 5. シークレット管理

| シークレット名 | 参照 Lambda |
|---|---|
| `investment/dev/jquants-api-key` | ingest |
| `investment/dev/slack-webhook-url` | publish |
| `investment-dev/edinet-api-key` | explain |

シークレット値はコード・設定ファイル・git 履歴に一切含めない。  
IAM ポリシーで各 Lambda から当該シークレットのみ `GetSecretValue` を許可。

---

## 6. 現状のドリフト（IaC 未適用分）

| 変更内容 | 適用方法 | .tf 反映 |
|---|---|---|
| explain IAM: BatchWriteItem/PutItem/UpdateItem on Candidates | aws iam put-role-policy | ✅ 反映済み |
| explain IAM: secretsmanager:GetSecretValue (EDINET key) | aws iam put-role-policy | ❌ 未反映 |
| ingest IAM: dynamodb:GetItem | aws iam put-role-policy | ✅ 反映済み |
| `investment-dev/edinet-api-key` シークレット作成 | aws secretsmanager create-secret | ❌ secrets/main.tf 未反映 |

---

## 7. ファイル構成

```
investment/
├── infra/                        # Terraform (IaC)
│   ├── backend.tf                # tfstate バックエンド設定 ※YOUR_ACCOUNT_ID 要置換
│   ├── envs/dev/main.tf          # モジュール呼び出しエントリポイント
│   └── modules/
│       ├── dynamodb/             # DynamoDB テーブル定義
│       ├── s3/                   # S3 バケット定義
│       ├── secrets/              # Secrets Manager (値はプレースホルダ)
│       ├── sns/                  # SNS トピック
│       ├── iam/                  # IAM ロール定義
│       ├── lambda/               # Lambda 関数定義
│       ├── stepfunctions/        # Step Functions 定義
│       ├── eventbridge/          # 週次スケジューラ (dev=DISABLED)
│       └── cloudwatch/           # Lambda エラーアラーム
│
├── lambda_src/                   # Lambda ソース (pip 生成物は .gitignore)
│   ├── ingest/                   # J-Quants データ取得
│   ├── screen_score/             # バフェット定量フィルタ + スコアリング
│   ├── explain/                  # EDINET + Bedrock 定性評価
│   └── publish/                  # レポート生成・SNS 通知
│
├── scripts/build_lambdas.ps1     # Lambda ビルド & デプロイ
├── config.yaml                   # スクリーニング条件 (再デプロイ不要で変更可)
└── docs/                         # 設計ドキュメント
```

---

## 8. Terraform 導入手順（未導入環境向け）

```powershell
# 1. インストール
winget install HashiCorp.Terraform

# 2. バックエンド用リソース作成（初回のみ）
$ACCOUNT = aws sts get-caller-identity --query Account --output text
aws s3 mb s3://investment-tfstate-$ACCOUNT --region ap-northeast-1
aws s3api put-bucket-versioning --bucket investment-tfstate-$ACCOUNT `
    --versioning-configuration Status=Enabled
aws dynamodb create-table --table-name investment-tflock `
    --attribute-definitions AttributeName=LockID,AttributeType=S `
    --key-schema AttributeName=LockID,KeyType=HASH `
    --billing-mode PAY_PER_REQUEST --region ap-northeast-1

# 3. backend.tf の YOUR_ACCOUNT_ID を実際のアカウント ID に置換

# 4. terraform.tfvars を作成（.gitignore 済み）
# infra/envs/dev/terraform.tfvars:
#   environment = "dev"
#   account_id  = "304513313801"
#   aws_region  = "ap-northeast-1"
#   alert_email = "your@email.com"

# 5. 初期化 & 適用
cd infra/envs/dev
terraform init
terraform plan -var-file=terraform.tfvars
terraform apply -var-file=terraform.tfvars
```
