# 運用アーキテクチャ・構成管理ガイド

## 1. 全体構成マップ

```mermaid
flowchart TD
    subgraph repo["GitHub リポジトリ"]
        tf["infra/ (Terraform)"]
        src["lambda_src/ (Python)"]
        cfg["config.yaml"]
        sc["scripts/build_lambdas.ps1"]
    end

    subgraph iac["IaC 管理レイヤー (Terraform)"]
        TF_STATE["S3 tfstate\ninvestment-tfstate-{account}"]
        TF_LOCK["DynamoDB tflock\n(ロック制御)"]
    end

    subgraph aws["AWS ap-northeast-1"]
        subgraph compute["コンピュート"]
            EB["EventBridge Scheduler\n毎週土曜 06:00 JST"]
            SFN["Step Functions\ninvestment-dev-pipeline"]
            L1["Lambda: ingest"]
            L2["Lambda: screen-score"]
            L3["Lambda: explain"]
            L4["Lambda: publish"]
        end

        subgraph storage["ストレージ"]
            DDB_SEC["DynamoDB: Securities"]
            DDB_PH["DynamoDB: PriceHistory"]
            DDB_FU["DynamoDB: Fundamentals"]
            DDB_CAN["DynamoDB: Candidates"]
            DDB_PO["DynamoDB: Portfolio"]
            DDB_RL["DynamoDB: RunLogs"]
            S3_CFG["S3: config bucket"]
            S3_REP["S3: reports bucket"]
        end

        subgraph security["セキュリティ・通知"]
            SM["Secrets Manager"]
            SNS["SNS: alerts"]
            CW["CloudWatch Alarms"]
            IAM["IAM Roles x5"]
        end

        subgraph ai["AI"]
            BR["Bedrock\nclaude-3-5-sonnet"]
            ED["EDINET API\n(外部)"]
        end
    end

    tf -->|terraform apply| aws
    src -->|build_lambdas.ps1| L1 & L2 & L3 & L4
    cfg -->|S3 upload| S3_CFG
    tf -->|state| TF_STATE
    tf -->|lock| TF_LOCK

    EB -->|週次トリガー| SFN
    SFN --> L1 --> L2 --> L3 --> L4
    L1 --> DDB_SEC & DDB_PH & DDB_FU & DDB_RL
    L2 --> DDB_FU & DDB_CAN & DDB_RL
    L3 --> DDB_CAN & DDB_RL
    L3 --> BR & ED
    L4 --> S3_REP & SNS & DDB_RL
    L1 & L2 & L3 & L4 --> S3_CFG
    L1 & L2 & L3 & L4 --> SM
    CW --> SNS
```

---

## 2. Terraform モジュール構成

```mermaid
flowchart LR
    subgraph entry["infra/envs/dev/main.tf (エントリポイント)"]
        direction TB
        M_DDB["module: dynamodb"]
        M_S3["module: s3"]
        M_SEC["module: secrets"]
        M_SNS["module: sns"]
        M_IAM["module: iam"]
        M_LAM["module: lambda"]
        M_SFN["module: stepfunctions"]
        M_EB["module: eventbridge"]
        M_CW["module: cloudwatch"]
    end

    subgraph resources["作成されるAWSリソース"]
        direction TB
        R1["DynamoDB 6テーブル\n(Securities/PriceHistory/Fundamentals\nCandidates/Portfolio/RunLogs)"]
        R2["S3 2バケット\n(config / reports)"]
        R3["Secrets Manager 2シークレット\n(jquants-api-key / slack-webhook)"]
        R4["SNS Topic\ninvestment-dev-alerts"]
        R5["IAM Role x5\n(ingest/screen_score/explain\npublish/stepfunctions)"]
        R6["Lambda Function x4\n(ingest/screen-score\nexplain/publish)"]
        R7["Step Functions\nState Machine"]
        R8["EventBridge Scheduler\n(dev=DISABLED)"]
        R9["CloudWatch Alarms\n(Lambda Errors x4)"]
    end

    M_DDB --> R1
    M_S3 --> R2
    M_SEC --> R3
    M_SNS --> R4
    M_IAM --> R5
    M_LAM --> R6
    M_SFN --> R7
    M_EB --> R8
    M_CW --> R9

    M_IAM -.->|ARN参照| M_LAM & M_SFN & M_EB
    M_DDB -.->|ARN参照| M_IAM
    M_S3 -.->|ARN参照| M_IAM
    M_SNS -.->|ARN参照| M_IAM & M_SFN & M_CW
    M_LAM -.->|ARN参照| M_SFN
    M_SFN -.->|ARN参照| M_EB
```

---

## 3. デプロイフロー

### 3-1. インフラ変更（Terraform）

```mermaid
flowchart TD
    A([インフラ変更が必要]) --> B["infra/modules/ の .tf を編集"]
    B --> C["terraform init\n-backend-config=backend.tfvars"]
    C --> D["terraform plan\n-var-file=terraform.tfvars"]
    D --> E{差分確認OK?}
    E -->|No| B
    E -->|Yes| F["terraform apply\n-var-file=terraform.tfvars"]
    F --> G["S3 tfstate 更新\nDynamoDB lock 解放"]
    G --> H([完了])

    style F fill:#d4edda
```

> **注意（現状）** : Terraform は未インストール環境のため、IAM ポリシー変更は  
> `aws iam put-role-policy` で直接適用している。`infra/modules/iam/main.tf` には  
> 変更内容を反映済みだが、**tfstate との乖離（ドリフト）が発生している**。  
> Terraform 導入後は `terraform import` でインポートするか、リソースを再作成する必要がある。

### 3-2. Lambda コード変更

```mermaid
flowchart TD
    A([コード変更]) --> B["lambda_src/{name}/ を編集"]
    B --> C["git commit -m '...'"]
    C --> D["./scripts/build_lambdas.ps1\n[-Only ingest|screen_score|explain|publish]"]
    D --> E["pip install --platform manylinux2014_x86_64\n(Linux向けバイナリ)"]
    E --> F["ZIP 作成\n(__pycache__ 除外)"]
    F --> G["S3 upload\ninvestment-dev-config-{account}/lambda-deploy/"]
    G --> H["aws lambda update-function-code\n--s3-bucket / --s3-key"]
    H --> I([Lambda 更新完了])

    style D fill:#cce5ff
    style I fill:#d4edda
```

### 3-3. 設定変更（config.yaml）

```mermaid
flowchart TD
    A([スクリーニング条件変更]) --> B["config.yaml を編集"]
    B --> C["aws s3 cp config.yaml\ns3://investment-dev-config-{account}/config.yaml"]
    C --> D([次回パイプライン実行から反映\nLambda 再デプロイ不要])

    style C fill:#cce5ff
    style D fill:#d4edda
```

---

## 4. 週次パイプライン実行フロー

```mermaid
sequenceDiagram
    participant EB as EventBridge<br/>毎週土曜 06:00 JST
    participant SFN as Step Functions
    participant L1 as Lambda: ingest
    participant L2 as Lambda: screen-score
    participant L3 as Lambda: explain
    participant L4 as Lambda: publish
    participant DDB as DynamoDB
    participant SM as Secrets Manager
    participant BR as Bedrock
    participant ED as EDINET API
    participant S3 as S3 (reports)
    participant SNS as SNS (Email/Slack)

    EB->>SFN: 週次トリガー (run_id 自動生成)
    SFN->>L1: Ingest ステージ
    L1->>SM: J-Quants API キー取得
    L1->>DDB: RunLogs.__state__ から前回実行日取得
    L1->>DDB: 銘柄マスタ・株価・Fundamentals 書き込み<br/>(前回実行日翌日〜昨日の全営業日)
    L1->>DDB: RunLogs.__state__.last_success_date 更新
    L1-->>SFN: 完了

    SFN->>L2: ScreenScore ステージ
    L2->>S3: config.yaml 読み込み
    L2->>DDB: Fundamentals から5年FYデータ取得
    L2->>DDB: バフェット定量フィルタ通過銘柄を Candidates 書き込み
    L2-->>SFN: 完了

    SFN->>L3: Explain ステージ
    L3->>SM: EDINET API キー取得
    L3->>ED: 有価証券報告書テキスト取得
    L3->>BR: 定性5次元スコアリング (claude-3-5-sonnet)
    L3->>DDB: Candidates に定性スコア・最終スコア書き込み
    L3->>BR: 銘柄説明文生成
    L3->>S3: HTMLレポート保存
    L3-->>SFN: report_key

    SFN->>L4: Publish ステージ
    L4->>S3: レポート URL 署名付き生成
    L4->>SNS: Email / Slack 通知
    L4-->>SFN: 完了
```

---

## 5. シークレット管理

| シークレット名 | 管理場所 | 参照元 |
|---|---|---|
| `investment/dev/jquants-api-key` | Secrets Manager | ingest Lambda |
| `investment/dev/slack-webhook-url` | Secrets Manager | publish Lambda |
| `investment-dev/edinet-api-key` | Secrets Manager | explain Lambda |
| J-Quants リフレッシュトークン | Secrets Manager (上記に内包) | ingest Lambda |

**ルール**: シークレット値はコード・設定ファイル・git 履歴に一切含めない。  
IAM ポリシーで各 Lambda から当該シークレットのみ `GetSecretValue` を許可している。

---

## 6. 現状のドリフト（IaC 乖離）

Terraform が未インストールのため、以下を CLI で直接適用している。  
**Terraform 導入時に `terraform import` または `terraform apply` で解消すること。**

| 変更内容 | 適用方法 | .tf への反映 |
|---|---|---|
| explain Lambda IAM に `BatchWriteItem/PutItem/UpdateItem` on Candidates 追加 | `aws iam put-role-policy` | ✅ `iam/main.tf` 反映済み |
| explain Lambda IAM に `secretsmanager:GetSecretValue` (EDINET key) 追加 | `aws iam put-role-policy` | ❌ 未反映 |
| ingest Lambda IAM に `dynamodb:GetItem` 追加 | `aws iam put-role-policy` | ✅ `iam/main.tf` 反映済み |
| `investment-dev/edinet-api-key` シークレット作成 | `aws secretsmanager create-secret` | ❌ `secrets/main.tf` 未反映 |

---

## 7. ファイル構成と役割

```
investment/
├── infra/                        # Terraform (IaC)
│   ├── backend.tf                # tfstate バックエンド設定
│   ├── versions.tf               # プロバイダバージョン制約
│   ├── envs/dev/
│   │   ├── main.tf               # モジュール呼び出しエントリポイント
│   │   ├── variables.tf          # 環境変数定義
│   │   ├── outputs.tf            # 出力値
│   │   └── terraform.tfvars      # ← .gitignore (account_id 等を含む)
│   └── modules/
│       ├── dynamodb/             # DynamoDB テーブル定義
│       ├── s3/                   # S3 バケット定義
│       ├── secrets/              # Secrets Manager (値はプレースホルダ)
│       ├── sns/                  # SNS トピック・サブスクリプション
│       ├── iam/                  # Lambda・SFN・Scheduler の IAM ロール
│       ├── lambda/               # Lambda 関数定義
│       ├── stepfunctions/        # Step Functions ステートマシン定義
│       ├── eventbridge/          # 週次スケジューラ定義
│       └── cloudwatch/           # Lambda エラーアラーム定義
│
├── lambda_src/                   # Lambda ソースコード (pip 生成物は .gitignore)
│   ├── ingest/                   # データ取得 (J-Quants API)
│   ├── screen_score/             # バフェット定量フィルタ + スコアリング
│   ├── explain/                  # EDINET + Bedrock 定性評価
│   └── publish/                  # レポート生成・SNS 通知
│
├── scripts/
│   └── build_lambdas.ps1         # Lambda ビルド & デプロイスクリプト
│
├── config.yaml                   # スクリーニング条件 (Lambda 再デプロイ不要で変更可)
└── docs/                         # 設計ドキュメント
```

---

## 8. Terraform 導入手順（未導入環境向け）

```powershell
# 1. Terraform インストール (winget)
winget install HashiCorp.Terraform

# 2. バックエンド用リソースを事前に作成（初回のみ）
$ACCOUNT = aws sts get-caller-identity --query Account --output text
aws s3 mb s3://investment-tfstate-$ACCOUNT --region ap-northeast-1
aws s3api put-bucket-versioning --bucket investment-tfstate-$ACCOUNT `
    --versioning-configuration Status=Enabled
aws dynamodb create-table --table-name investment-tflock `
    --attribute-definitions AttributeName=LockID,AttributeType=S `
    --key-schema AttributeName=LockID,KeyType=HASH `
    --billing-mode PAY_PER_REQUEST --region ap-northeast-1

# 3. terraform.tfvars を作成（.gitignore 済み）
# infra/envs/dev/terraform.tfvars に記述:
#   environment = "dev"
#   account_id  = "304513313801"
#   aws_region  = "ap-northeast-1"
#   alert_email = "your@email.com"

# 4. 初期化 & backend.tf のアカウントID修正
cd infra/envs/dev
# backend.tf の YOUR_ACCOUNT_ID を実アカウント ID に置換後:
terraform init
terraform plan -var-file=terraform.tfvars

# 5. ドリフト解消: CLI適用済みリソースをインポート
terraform import module.iam.aws_iam_role_policy.ingest \
    investment-dev-lambda-ingest:terraform-20260624214409041500000006
```
