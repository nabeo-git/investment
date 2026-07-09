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
        L1["Lambda: ingest"]
        L2["Lambda: screen-score"]
        L3["Lambda: explain"]
        L4["Lambda: publish"]
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

    style repo fill:#1e3a5f,stroke:#60a5fa,color:#e0f0ff
    style pipeline fill:#1a3a2a,stroke:#34d399,color:#d1fae5
    style data fill:#2d1f52,stroke:#a78bfa,color:#ede9fe
    style notify fill:#1c2f3a,stroke:#38bdf8,color:#e0f2fe
    style ext fill:#3b1f1f,stroke:#f87171,color:#fee2e2
    style TF fill:#1e3a5f,stroke:#60a5fa,color:#e0f0ff
    style SRC fill:#1e3a5f,stroke:#60a5fa,color:#e0f0ff
    style CFG fill:#1e3a5f,stroke:#60a5fa,color:#e0f0ff
    style SC fill:#1e3a5f,stroke:#60a5fa,color:#e0f0ff
    style EB fill:#4a2e00,stroke:#f59e0b,color:#fef3c7
    style SFN fill:#1a3a2a,stroke:#34d399,color:#d1fae5
    style L1 fill:#1a3a2a,stroke:#34d399,color:#d1fae5
    style L2 fill:#1a3a2a,stroke:#34d399,color:#d1fae5
    style L3 fill:#1a3a2a,stroke:#34d399,color:#d1fae5
    style L4 fill:#1a3a2a,stroke:#34d399,color:#d1fae5
    style DDB fill:#2d1f52,stroke:#a78bfa,color:#ede9fe
    style S3 fill:#2d1f52,stroke:#a78bfa,color:#ede9fe
    style SM fill:#2d1f52,stroke:#a78bfa,color:#ede9fe
    style SNS fill:#1c2f3a,stroke:#38bdf8,color:#e0f2fe
    style CW fill:#1c2f3a,stroke:#38bdf8,color:#e0f2fe
    style BR fill:#3b1f1f,stroke:#f87171,color:#fee2e2
    style ED fill:#3b1f1f,stroke:#f87171,color:#fee2e2
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
    M4 -.->|TopicARN| M5
    M4 -.->|TopicARN| M7
    M4 -.->|TopicARN| M9
    M5 -.->|RoleARN| M6
    M5 -.->|RoleARN| M7
    M5 -.->|RoleARN| M8
    M6 -.->|FuncARN| M7
    M7 -.->|StateMachineARN| M8

    style ENTRY fill:#1e3a5f,stroke:#60a5fa,color:#e0f0ff
    style M1 fill:#2d1f52,stroke:#a78bfa,color:#ede9fe
    style M2 fill:#2d1f52,stroke:#a78bfa,color:#ede9fe
    style M3 fill:#2d1f52,stroke:#a78bfa,color:#ede9fe
    style M4 fill:#1c2f3a,stroke:#38bdf8,color:#e0f2fe
    style M5 fill:#3b2500,stroke:#f59e0b,color:#fef3c7
    style M6 fill:#1a3a2a,stroke:#34d399,color:#d1fae5
    style M7 fill:#1a3a2a,stroke:#34d399,color:#d1fae5
    style M8 fill:#4a2e00,stroke:#f59e0b,color:#fef3c7
    style M9 fill:#1c2f3a,stroke:#38bdf8,color:#e0f2fe
```

---

## 3. デプロイフロー

### 3-1. インフラ変更（Terraform）

```mermaid
flowchart TD
    A(["インフラ変更"])
    B["infra/modules/ の .tf を編集"]
    C["terraform init"]
    D["terraform plan\n-var-file=terraform.tfvars"]
    E{"差分OK?"}
    F["terraform apply"]
    G["S3 tfstate 更新\nDynamoDB ロック解放"]
    H(["完了"])

    A --> B --> C --> D --> E
    E -->|No| B
    E -->|Yes| F --> G --> H

    style A fill:#1e3a5f,stroke:#60a5fa,color:#e0f0ff
    style B fill:#1e3a5f,stroke:#60a5fa,color:#e0f0ff
    style C fill:#1e3a5f,stroke:#60a5fa,color:#e0f0ff
    style D fill:#1e3a5f,stroke:#60a5fa,color:#e0f0ff
    style E fill:#3b2500,stroke:#f59e0b,color:#fef3c7
    style F fill:#1a3a2a,stroke:#34d399,color:#d1fae5
    style G fill:#2d1f52,stroke:#a78bfa,color:#ede9fe
    style H fill:#14362a,stroke:#4ade80,color:#dcfce7
```

> **現状注意**: Terraform 未インストールのため IAM 変更は `aws iam put-role-policy` で直接適用。  
> `iam/main.tf` には反映済みだが **tfstate とドリフトあり**。Terraform 導入時は `terraform import` が必要。

### 3-2. Lambda コード変更

```mermaid
flowchart TD
    A(["コード変更"])
    B["lambda_src/{name}/ を編集"]
    C["git commit"]
    D["build_lambdas.ps1\n-Only {name}"]
    E["pip install\nmanylinux2014_x86_64"]
    F["ZIP 作成\n__pycache__ 除外"]
    G["S3 upload\nlambda-deploy/{name}.zip"]
    H["aws lambda\nupdate-function-code"]
    I(["Lambda 更新完了"])

    A --> B --> C --> D --> E --> F --> G --> H --> I

    style A fill:#1e3a5f,stroke:#60a5fa,color:#e0f0ff
    style B fill:#1e3a5f,stroke:#60a5fa,color:#e0f0ff
    style C fill:#1e3a5f,stroke:#60a5fa,color:#e0f0ff
    style D fill:#3b2500,stroke:#f59e0b,color:#fef3c7
    style E fill:#1a3a2a,stroke:#34d399,color:#d1fae5
    style F fill:#1a3a2a,stroke:#34d399,color:#d1fae5
    style G fill:#2d1f52,stroke:#a78bfa,color:#ede9fe
    style H fill:#2d1f52,stroke:#a78bfa,color:#ede9fe
    style I fill:#14362a,stroke:#4ade80,color:#dcfce7
```

### 3-3. 設定変更（config.yaml）

```mermaid
flowchart TD
    A(["条件変更"])
    B["config.yaml を編集"]
    C["aws s3 cp config.yaml\ns3://...-config/config.yaml"]
    D(["次回実行から反映\nLambda 再デプロイ不要"])

    A --> B --> C --> D

    style A fill:#1e3a5f,stroke:#60a5fa,color:#e0f0ff
    style B fill:#1e3a5f,stroke:#60a5fa,color:#e0f0ff
    style C fill:#2d1f52,stroke:#a78bfa,color:#ede9fe
    style D fill:#14362a,stroke:#4ade80,color:#dcfce7
```

---

## 4. 週次パイプライン実行フロー

```mermaid
flowchart TD
    EB["EventBridge\n毎週土曜 06:00 JST"]
    SFN["Step Functions 起動\nrun_id 生成"]

    I1["ingest: J-Quants API キー取得\n(Secrets Manager)"]
    I2["RunLogs から前回実行日取得"]
    I3["前回実行日翌日〜昨日の\n全営業日データ取得"]
    I4["Securities / PriceHistory /\nFundamentals を DynamoDB 書き込み"]
    I5["last_success_date 更新"]

    S1["screen-score: config.yaml 読み込み"]
    S2["Fundamentals から 5年 FY データ取得"]
    S3["バフェット定量フィルタ適用\nROE・EPS CAGR・CFO 品質 等"]
    S4["スコアリング (定量 3 軸)\n→ Candidates 書き込み"]

    E1["explain: EDINET API キー取得"]
    E2["有価証券報告書テキスト取得\n(EDINET API)"]
    E3["Bedrock 定性スコアリング\n(5 次元 × 0-10 点)"]
    E4["最終スコア算出\n定量 × 0.4 + 定性 × 0.6"]
    E5["HTML レポート生成 → S3 保存"]

    P1["publish: 署名付き URL 生成"]
    P2["SNS 通知 (Email / Slack)"]
    END(["完了"])
    ERR["NotifyError\n(SNS 通知)"]

    EB --> SFN --> I1 --> I2 --> I3 --> I4 --> I5
    I5 --> S1 --> S2 --> S3 --> S4
    S4 --> E1 --> E2 --> E3 --> E4 --> E5
    E5 --> P1 --> P2 --> END
    SFN -->|エラー時| ERR

    style EB fill:#4a2e00,stroke:#f59e0b,color:#fef3c7
    style SFN fill:#1a3a2a,stroke:#34d399,color:#d1fae5
    style I1 fill:#1a3a2a,stroke:#34d399,color:#d1fae5
    style I2 fill:#1a3a2a,stroke:#34d399,color:#d1fae5
    style I3 fill:#1a3a2a,stroke:#34d399,color:#d1fae5
    style I4 fill:#2d1f52,stroke:#a78bfa,color:#ede9fe
    style I5 fill:#2d1f52,stroke:#a78bfa,color:#ede9fe
    style S1 fill:#1a3a2a,stroke:#34d399,color:#d1fae5
    style S2 fill:#2d1f52,stroke:#a78bfa,color:#ede9fe
    style S3 fill:#1a3a2a,stroke:#34d399,color:#d1fae5
    style S4 fill:#2d1f52,stroke:#a78bfa,color:#ede9fe
    style E1 fill:#1a3a2a,stroke:#34d399,color:#d1fae5
    style E2 fill:#3b1f1f,stroke:#f87171,color:#fee2e2
    style E3 fill:#3b1f1f,stroke:#f87171,color:#fee2e2
    style E4 fill:#1a3a2a,stroke:#34d399,color:#d1fae5
    style E5 fill:#2d1f52,stroke:#a78bfa,color:#ede9fe
    style P1 fill:#1c2f3a,stroke:#38bdf8,color:#e0f2fe
    style P2 fill:#1c2f3a,stroke:#38bdf8,color:#e0f2fe
    style END fill:#14362a,stroke:#4ade80,color:#dcfce7
    style ERR fill:#3b1f1f,stroke:#f87171,color:#fee2e2
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

以下はすべて `.tf` に反映済み。Terraform 導入後に `terraform apply` で収束させること。  
シークレットの**値**は Terraform 管理外（CLI または AWS Console で設定）。**定義（リソースシェル）**は `.tf` で管理する。

| 変更内容 | 適用済み手段 | .tf 反映 |
|---|---|---|
| explain IAM: BatchWriteItem/PutItem/UpdateItem on Candidates | aws iam put-role-policy | ✅ iam/main.tf |
| explain IAM: secretsmanager:GetSecretValue (EDINET key) | aws iam put-role-policy | ✅ iam/main.tf |
| ingest IAM: dynamodb:GetItem | aws iam put-role-policy | ✅ iam/main.tf |
| `investment-dev/edinet-api-key` シークレット定義 | aws secretsmanager create-secret | ✅ secrets/main.tf |

---

## 7. ファイル構成

```
investment/
├── infra/                        # Terraform (IaC)
│   ├── backend.tf                # tfstate バックエンド ※YOUR_ACCOUNT_ID 要置換
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
