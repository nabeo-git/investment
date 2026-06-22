# 投資支援システム 基本設計書

| 項目 | 内容 |
|---|---|
| ドキュメントID | 02_basic_design |
| バージョン | 1.0 |
| 最終更新 | 2026-06-23 |
| ステータス | ドラフト |

---

## 1. 設計方針

### 1.1 設計の核
- **[Screening / Scoring] = 決定論的コード** ⇔ **[Explanation] = LLM（Bedrock）** を厳格に分離
- 数値判断はコード、自然言語化はLLMという責務分離により、ハルシネーションが選定結果に混入しない
- 各ステージは冪等で再実行可能。Step Functionsでオーケストレーションし、失敗時の部分再実行を容易にする

### 1.2 パイプライン構成
```
[1] Ingestion        : J-Quants → DynamoDB（株価/財務/配当）
        ↓
[2] Storage          : DynamoDBで正規化保管
        ↓
[3] Screening        : ルールベース粗フィルタ（LLM不使用）
        ↓
[4] Scoring          : 定量スコアリング（LLM不使用）
        ↓
[5] Ranking          : 上位N銘柄抽出、予算内組合せ
        ↓
[6] Explanation      : Bedrock（Claude Sonnet 4.6）でRAG＋説明文生成
        ↓
[7] Publishing       : MarkdownレポートをGoogle Driveに配置
        ↓
[8] Notification     : SNS経由でメール通知
        ↓
[9] Logging          : 提示履歴をDynamoDBに記録
```

---

## 2. システム全体像

### 2.1 構成図（テキスト）
```
EventBridge Scheduler (週次：土曜 06:00 JST)
       ↓
Step Functions (state machine)
       ↓
   ┌───────────────┬───────────────┬───────────────┬───────────────┐
   ↓               ↓               ↓               ↓
lambda-ingest  lambda-screen-  lambda-explain  lambda-publish
               score
   ↓               ↓               ↓               ↓
DynamoDB        DynamoDB        Bedrock          S3 (reports)
(Securities,    (Candidates)    (Claude Sonnet   Google Drive
 PriceHistory,                   4.6, us-east-1)  SNS → Email
 Fundamentals)
       ↑
S3 (config.yaml) ← 全Lambdaが起動時読み込み
       ↑
Secrets Manager ← J-Quants認証 / Google Drive Service Account Key
       ↑
CloudWatch Logs (全Lambda) → Metric Filter (Error) → SNS
```

### 2.2 リージョン構成
| リソース | リージョン | 理由 |
|---|---|---|
| メイン全体 | ap-northeast-1 | レイテンシ・データ主権 |
| Bedrock (Claude Sonnet 4.6) | us-east-1 | モデル提供リージョン |

---

## 3. コンポーネント設計

### 3.1 Lambda関数構成

| Lambda名 | 責務 | ランタイム | タイムアウト | メモリ |
|---|---|---|---|---|
| `lambda-ingest` | J-Quants APIから取得しDynamoDB保管。欠損フラグ設定・リトライ | Python 3.12 | 15分 | 512MB |
| `lambda-screen-score` | DynamoDBから読み出し→スクリーニング→スコアリング→候補保存 | Python 3.12 | 5分 | 1024MB |
| `lambda-explain` | 候補銘柄ごとにIR/決算をRetrieval→Bedrock呼出→Markdown生成 | Python 3.12 | 15分 | 512MB |
| `lambda-publish` | レポートをS3保存→Google Drive PUT→SNS publish | Python 3.12 | 5分 | 512MB |

### 3.2 共通事項
- 言語：Python 3.12（boto3、yfinance等のエコシステム）
- 共通レイヤー：`config.yaml` 読み込み・スキーマ検証・DynamoDBクライアントを共通Lambda Layerに切り出し
- ログ：構造化JSON（CloudWatch Logs Insightsで検索可能）
- 環境変数：テーブル名・S3バケット名・SNS Topic ARN等をTerraformから注入

---

## 4. 設定ファイル設計

### 4.1 配置と読み込み
- S3バケット：`investment-config-{env}` の `config.yaml`
- バージョニング有効化
- Lambda起動時にダウンロードしオンメモリで保持
- スキーマ検証（pydantic等）に失敗した場合はLambdaを即座にエラー終了

### 4.2 構造例
```yaml
data_sources:
  jquants:
    enabled: true
    endpoint: https://api.jquants.com
  edgar:    # Phase2
    enabled: false
  fred:     # Phase2
    enabled: false

screening:
  max_unit_price_jpy: 100000
  min_dividend_yield: 0.030
  min_equity_ratio: 0.40
  max_per: 20
  min_daily_volume: 1000
  min_continuous_dividend_years: 5

scoring:
  weights:
    dividend_continuity: 0.35
    valuation: 0.30
    financial_health: 0.20
    portfolio_correlation: 0.15
  # 拡張用：資産クラス別の重みを後から追加可能
  asset_class_overrides:
    # reit: {dividend_continuity: 0.40, ...}

candidates:
  top_n: 10
  monthly_budget_jpy: 100000

portfolio:    # Phase1は静的、Phase2でDB化
  holdings:
    - ticker: SPY
      asset_class: us_etf
      amount_jpy: 600000
    - ticker: japan_high_dividend_basket
      asset_class: jp_stock
      amount_jpy: 200000

explanation:
  bedrock_model_id: anthropic.claude-sonnet-4-6-20260101-v1:0
  bedrock_region: us-east-1
  max_tokens: 4000
  temperature: 0.2

notification:
  email_to: ${ALERT_EMAIL}
```

---

## 5. データソース抽象化（拡張性）

### 5.1 アダプタパターン
```
DataSourceAdapter (interface)
  ├ JQuantsAdapter      (Phase1)
  ├ EdgarAdapter        (Phase2)
  ├ FredAdapter         (Phase2)
  └ YFinanceAdapter     (Phase2、補完用)
```

各アダプタは以下の共通インタフェースを実装：
- `fetch_securities()` — 銘柄マスタ取得
- `fetch_price_history(ticker, from_date, to_date)` — 株価ヒストリカル
- `fetch_fundamentals(ticker)` — 財務・配当データ

Phase2でアダプタを追加する際、`config.yaml` の `data_sources.{name}.enabled: true` だけで有効化できる。

### 5.2 資産クラス抽象化
```
ScoringStrategy (interface)
  ├ StockScoring        (株式：配当＋割安＋財務)
  ├ ReitScoring         (REIT：配当＋金利感応度) — Phase2
  ├ BondScoring         (債券：金利・信用) — Phase2
  └ GoldScoring         (金：マクロ・インフレ) — Phase2
```

`Securities` テーブルの `asset_class` 属性で評価戦略を切り替える。

---

## 6. オーケストレーション設計

### 6.1 Step Functions State Machine
```
START
  → Ingest (lambda-ingest)
      ├ Retry: 3回 / 指数バックオフ
      └ Catch: → ErrorHandler
  → ScreenScore (lambda-screen-score)
  → Explain (lambda-explain)
      ├ Map: 候補銘柄ごとに並列実行（最大10並列）
  → Publish (lambda-publish)
  → END

ErrorHandler:
  → SNS publish (エラー詳細)
  → FAIL
```

### 6.2 スケジューラ
- EventBridge Scheduler
- スケジュール：`cron(0 21 ? * FRI *)` UTC = 土曜 06:00 JST
- ターゲット：Step Functions state machine

---

## 7. レポート設計

### 7.1 ファイル形式
- Markdown（`.md`）
- ファイル名：`investment_report_YYYYMMDD.md`
- Google Drive配置先：`/InvestmentReports/` フォルダ（サービスアカウントに共有設定）

### 7.2 レポート構造
```markdown
# 投資候補レポート 2026-06-27

## 1. サマリ
- 候補銘柄数：10
- 月次予算：100,000円
- 推奨組合せ予算消化：98,500円

## 2. 候補銘柄
### 2.1 [銘柄名] (証券コード)
- **総合スコア**: 0.87
- **スコア内訳**: 配当継続性 0.32 / 割安度 0.25 / 財務健全性 0.18 / 既存PF相関 0.12
- **株価**: 850円（単元 85,000円）
- **選定理由**: （Bedrockによる自然言語説明、決算/IRからRAGで根拠引用）
- **既存PFとの相関**: SP500 とのβ 相関 0.3（低相関）
- **参照IR**: [リンク]

（以下、上位10銘柄分）

## 3. 推奨組合せ
| 銘柄 | 数量 | 金額 |
| ... |

## 4. 注意点
- 最終判断は本人が実施
- データas-of: 2026-06-26
```

---

## 8. エラーハンドリング・モニタリング

### 8.1 リトライ方針
| 種別 | 方針 |
|---|---|
| 外部API一時障害 | Lambda内で3回リトライ（指数バックオフ）。それでも失敗ならStep Functions側で3回 |
| データ欠損 | 欠損フラグを立てて続行（スコア計算時に該当銘柄を除外） |
| Bedrock呼出失敗 | 該当銘柄の説明を空欄で続行、SNS通知 |
| Drive PUT失敗 | S3にレポート保管済みのためバッチ完了は維持、SNS通知でリカバリ促す |

### 8.2 モニタリング
- 各Lambda：CloudWatch Logs（構造化JSON）
- CloudWatch Metric Filter：`level=ERROR` を検出 → SNS Topic `investment-alerts`
- Step Functions：実行履歴をAWS Console で参照
- DynamoDB `RunLogs` テーブル：各ステージの開始/終了/件数を記録（業務観点の追跡）

---

## 9. セキュリティ設計

| 項目 | 対策 |
|---|---|
| 認証情報 | Secrets Manager（J-Quants認証、Google Driveサービスアカウント鍵） |
| IAM | Lambdaごとに最小権限ロール（テーブル別read/write） |
| 通信 | 全AWS API通信はTLS。J-Quants/Drive APIもHTTPS |
| データ暗号化 | DynamoDB暗号化（AWSマネージドKMS）、S3 SSE-S3 |
| 公開範囲 | 外部公開なし。Lambda URL/API Gateway無し |

---

## 10. Phase2拡張ポイント整理

| Phase2機能 | Phase1での対応 |
|---|---|
| 米国株対応 | DataSourceAdapter抽象化、`config.yaml` でenabledトグル |
| マクロ・為替 | 同上＋DynamoDB拡張用テーブル設計を予約（03_database_design.md） |
| 資産クラス拡張 | ScoringStrategy抽象化、`Securities.asset_class` 属性で切替 |
| ユーザ実購入INPUT | `Portfolio` テーブルを最初から動的更新可能な構造で設計 |

---

## 11. 関連ドキュメント
- `01_requirements.md`
- `03_database_design.md`
- `04_infrastructure_design.md`
