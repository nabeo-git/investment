# 投資支援システム 作業計画

| 項目 | 内容 |
|---|---|
| 最終更新 | 2026-07-02 |
| 対象フェーズ | Phase 1（MVP） |
| **プロジェクトステータス** | **🔄 作業再開中** |
| **J-Quants解約予定日** | **2026-07-24（この日までに初期データ投入を完了すること）** |

> ⚠️ **期限あり**：J-Quantsは2026-07-24に解約予定。それまでにlambda-ingest実装 → Bulk APIで初期データDynamoDB投入を完了させること。解約後はAPIキーが無効になる。

---

## Phase 0：環境準備・PoC ✅ 完了

### 0-1. ローカル環境セットアップ
- [ ] Python仮想環境作成（venv or conda）
- [x] `jquants-api-client` インストール（v2.2.0）
- [x] `boto3` / `google-api-python-client` インストール
- [x] AWS CLI インストール・`aws configure`（ap-northeast-1）

### 0-2. J-Quants API 疎通テスト ★PoC① ✅ 完了（2026-06-24）
- [x] J-Quants有料プラン（Light以上）契約・APIキー発行
- [x] `ClientV2` でAPIキー認証確認
- [x] `/v2/equities/master` → 銘柄一覧取得（4,443件）
- [x] `/v2/equities/bars/daily` → 日付指定で全銘柄株価取得確認
- [x] `/v2/fins/summary` → 財務サマリー取得確認（トヨタ 20件）
- [x] Bulk API → 77ファイル確認（月次CSV利用可能）
- **設計反映**：`date` 単日指定のみ全銘柄取得可。週次更新は営業日ループ方式に確定

### 0-3. AWS Bedrock 疎通テスト ★PoC② ✅ 完了（2026-06-25）
- [x] `us-east-1` リージョンで Amazon Nova Pro（`amazon.nova-pro-v1:0`）InvokeModel確認
- [x] プロンプト投入・日本語出力確認

### 0-4. Google Drive → Slack通知に変更 ★PoC③ ✅ 完了（2026-06-25）
- [x] Google Drive検証（OAuth2）→ **設計変更：Slack Incoming Webhookに切替**
- [x] Bedrock・DynamoDB疎通確認済み
- **設計反映**：lambda-publishはSlack Webhook URLに直接POST。OAuth2不要

### 0-5. DynamoDB 疎通テスト ★PoC④ ✅ 完了（2026-06-25）
- [x] テスト用テーブル作成・PutItem / GetItem / Query・削除確認

---

## Phase 1：インフラ構築（Terraform） ✅ 完了（2026-07-02）

### 1-1. Terraformバックエンド事前準備 ✅
- [x] tfstate用S3バケット作成（`investment-tfstate-YOUR_ACCOUNT_ID`）
- [x] DynamoDBロックテーブル作成（`investment-tflock`）

### 1-2. Terraformモジュール実装 ✅
- [x] `modules/dynamodb` — 6テーブル（PITR・TTL・暗号化）
- [x] `modules/s3` — configバケット（バージョニング有効）・reportsバケット
- [x] `modules/iam` — Lambda別最小権限ロール × 4
- [x] `modules/secrets` — 2シークレット（jquants・slack-webhook-url）
- [x] `modules/lambda` — 4関数（プレースホルダー）
- [x] `modules/stepfunctions` — state machine定義（リトライ・Catch）
- [x] `modules/eventbridge` — 週次スケジューラ（土曜 06:00 JST、dev=DISABLED）
- [x] `modules/sns` — alertsトピック + emailサブスクリプション
- [x] `modules/cloudwatch` — ロググループ・メトリクスアラーム

### 1-3. dev環境 apply・検証 ⏸ 停止中
- [x] `terraform apply`（42リソース作成済み）
- [x] DynamoDBテーブル6個作成確認
- [x] Lambda 4関数デプロイ確認
- [ ] **Secrets Manager に認証情報を手動投入** ← **再開時の最初のタスク**
  - `investment/dev/jquants-api-key`（J-Quants再契約後）
  - `investment/dev/slack-webhook-url`（notification-slackのWebhook URL）
- [ ] `config.yaml` 作成・S3アップロード
- [ ] SNSメール購読の確認メール承認

---

## Phase 2：Lambda実装 🔜 未着手

### 2-1. 共通Layer（`lambda_src/layers/common/`）
- [ ] `config_loader.py` — S3からconfig.yaml取得・pydantic検証
- [ ] `db_client.py` — DynamoDB操作ユーティリティ
- [ ] `jquants_client.py` — `ClientV2` ラッパー
- [ ] `run_logger.py` — RunLogsへの記録

### 2-2. `lambda-ingest`
- [ ] 初期ロードスクリプト `scripts/initial_load.py`（Bulk API → BatchWriteItem）
- [ ] 週次増分取得（Securities差分Upsert・PriceHistory追記・Fundamentals追記）
- [ ] RunLogsへ取得範囲・件数を記録

### 2-3. `lambda-screen-score`
- [ ] スクリーニングロジック（config.yamlの条件を動的に適用）
- [ ] スコアリングロジック（重みをconfig.yamlから取得）
- [ ] 上位N銘柄をCandidatesテーブルに書き込み

### 2-4. `lambda-explain`
- [ ] Candidatesから候補一覧取得
- [ ] Fundamentals / PriceHistoryからコンテキスト構築
- [ ] Bedrockへプロンプト投入・Markdown取得（Map並列）
- [ ] S3 reportsバケットへレポート保管

### 2-5. `lambda-publish`
- [ ] S3からレポート取得
- [ ] Slack Webhook URLへPOST（Secrets Managerから取得）
- [ ] SNS通知（メール）
- [ ] RunLogsをsuccess更新

---

## Phase 3：テスト・結合確認 🔜 未着手

### 3-1. 単体テスト
- [ ] 各Lambda関数のユニットテスト（モックあり）
- [ ] スクリーニング・スコアリングロジックの境界値テスト
- [ ] config.yamlスキーマ検証（pydantic）の異常系テスト

### 3-2. Step Functions 結合テスト
- [ ] コンソールから手動 `StartExecution`
- [ ] DynamoDB `Candidates` にレコード生成確認
- [ ] S3 reportsバケットにMarkdown出力確認
- [ ] Slack通知受信確認
- [ ] SNS通知メール受信確認

### 3-3. 異常系テスト
- [ ] J-Quants API障害シミュレーション → リトライ・SNS通知確認
- [ ] Bedrock呼出失敗 → 該当銘柄空欄でレポート継続確認
- [ ] config.yaml不正値 → Lambda即時エラー停止確認

---

## Phase 4：本番移行 🔜 未着手

- [ ] prod環境 Terraform apply（envs/prod）
- [ ] 本番用Secrets投入・config.yaml確認
- [ ] EventBridge有効化（週次スケジュール開始）
- [ ] Cost Explorer — `Project: InvestmentSystem` タグ有効化
- [ ] 初回週次バッチ完走確認

---

## 再開時のチェックリスト

1. J-Quants Light（月1,650円）を再契約・APIキー発行
2. `aws secretsmanager put-secret-value --secret-id investment/dev/jquants-api-key --secret-string "新しいAPIキー"`
3. `aws secretsmanager put-secret-value --secret-id investment/dev/slack-webhook-url --secret-string "https://hooks.slack.com/services/..."`
4. `config.yaml` 作成・S3アップロード → Phase 2（Lambda実装）へ

---

## 関連ドキュメント
- `01_requirements.md` — 要件定義
- `02_basic_design.md` — 基本設計
- `03_database_design.md` — DB設計
- `04_infrastructure_design.md` — インフラ設計
