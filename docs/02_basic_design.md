# 投資支援システム 基本設計書

| 項目 | 内容 |
|---|---|
| ドキュメントID | 02_basic_design |
| バージョン | 1.1 |
| 最終更新 | 2026-06-24 |
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

### 7.2 レポート構造（出力イメージ）
```markdown
# 投資候補レポート 2026-06-27

## 1. サマリ
- 候補銘柄数：10
- 月次予算：100,000円
- 推奨組合せ予算消化：98,500円

## 2. 候補銘柄

### 2.1 ○○食品 (2345)
**総合スコア: 0.87**
スコア内訳：配当継続性 0.32 / 割安度 0.25 / 財務健全性 0.18 / 既存PF相関 0.12
株価：850円（単元100株 = 85,000円）

#### なぜこの銘柄が候補に挙がったか

**① PER（株価収益率）が割安水準**
PERとは「今の株価が1株あたり利益の何倍で取引されているか」を示す指標です。
PERが低いほど、利益に対して株価が安い（割安）と判断できます。
一般的に日本株の平均PERは15〜16倍程度です。

○○食品の現在のPER：**12.3倍**（業種平均14.8倍）
→ 業種平均より約2.5倍割安な水準で取引されており、スコアリングで高評価となっています。

**② 配当利回りが高く、連続増配の実績あり**
配当利回りとは「株価に対して年間配当がどれくらいの割合か」を示す指標です。
例えば株価1,000円で年間配当30円なら配当利回り3%です。

○○食品の配当利回り：**3.8%**（スクリーニング閾値3.0%を上回る）
連続増配年数：**8年**（リーマンショック後も減配なし）
→ 安定的に株主に還元し続けている実績があり、長期保有に適しています。

**③ 自己資本比率が高く財務が健全**
自己資本比率とは「総資産のうち、返済不要な自己資本が占める割合」です。
高いほど借金に頼らない経営体質で、業績悪化時の耐性があります。
一般的に40%以上が健全の目安とされます。

○○食品の自己資本比率：**62%**（閾値40%を大きく上回る）
→ 借入依存度が低く、景気後退局面でも配当を維持しやすい財務構造です。

**④ 既存ポートフォリオとの相関が低い**
あなたの既存PFはSP500（米国株）が中心です。SP500と値動きが似た銘柄を
買い増しても分散効果が薄れます。○○食品（内需・食品）はSP500との相関が低く、
米国株が下落した局面でも影響を受けにくい傾向があります。

SP500との相関係数：**0.28**（低相関）

**⑤ 直近の決算・IRより**
（RAGで取得した適時開示の要点）
2026年3月期：売上高+5.2%、営業利益+8.1%。原材料費の高騰を価格転嫁で吸収。
2026年度の配当予定：34円（前年比+2円）。増配継続方針を確認。

**⚠ 注意点**
- 食品セクターのため、原材料費（小麦・油脂）の高騰がリスク
- 流動性はやや低め（出来高1,800株/日）。単元売買には問題ないが、大口では注意

---
（以下、上位10銘柄分）

## 3. 推奨組合せ（月次予算 100,000円 以内）
| 銘柄 | 株価 | 数量 | 金額 |
|---|---|---|---|
| ○○食品 (2345) | 850円 | 100株 | 85,000円 |
| △△REIT (3456) | 120,000円 | 1口 | 120,000円 |
※ 組合せは参考です。最終判断はご自身で実施してください。

## 4. 注意点
- **このレポートは投資アドバイスではありません。最終判断は必ずご自身で行ってください。**
- データas-of: 2026-06-26
- 説明文中の「一般的な目安」は参考値であり、業種・相場環境によって異なります。
```

### 7.3 Bedrockプロンプト設計

#### 7.3.1 設計方針
- **数値はシステム側（コード）が渡す**：LLMには計算させず「この数値の意味を説明せよ」と指示
- **用語定義を必ず含める**：PER・PBR・自己資本比率・配当利回り・相関係数など、使う指標は毎回定義してから数値を当てはめる
- **「なぜ候補になったか」を軸に展開**：「買え」とは言わず「スコアリングでこの点が評価された」という構造
- **リスクも必ず記載**：候補に挙がった理由と同様に、懸念点・注意事項も生成させる

#### 7.3.2 プロンプトテンプレート（lambda-explain）

```python
SYSTEM_PROMPT = """
あなたは投資初心者から中級者向けに銘柄の選定理由を解説する役割を担います。
以下のルールを厳守してください。

【絶対ルール】
- 数値計算は一切行わない。スコアや財務数値は必ず入力データの値をそのまま引用する
- 「買い時です」「お買い得です」などの断定的な推奨表現を使わない
- 各指標を使う際は、必ずその指標の定義・一般的な目安を先に説明してから数値を当てはめる

【出力ルール】
- 日本語で出力する
- 対象読者：PERやPBRを知らない投資初心者でも理解できる水準で説明する
- 各指標の説明 → その銘柄の実数値 → スコアリングへの影響、の順序で記述する
- リスク・懸念点のセクションを必ず含める
"""

USER_PROMPT_TEMPLATE = """
以下の銘柄について、スコアリング結果と財務データに基づいて選定理由を解説してください。

## 銘柄情報
- 銘柄名：{name}（証券コード：{ticker}）
- 株価：{price}円（単元{unit_size}株 = {unit_price}円）
- 業種：{sector}

## スコアリング結果（コードが算出した値。この数値で説明すること）
- 総合スコア：{score_total}（1.0満点）
- 配当継続性スコア：{score_dividend}（重み35%）
- 割安度スコア：{score_valuation}（重み30%）
- 財務健全性スコア：{score_financial}（重み20%）
- 既存PF相関スコア：{score_correlation}（重み15%）

## 財務データ（as-of: {as_of_date}）
- PER：{per}倍（業種平均：{sector_avg_per}倍）
- PBR：{pbr}倍
- 配当利回り：{dividend_yield}%
- 連続増配年数：{continuous_dividend_years}年
- 自己資本比率：{equity_ratio}%
- 出来高（直近平均）：{avg_volume}株/日

## 既存ポートフォリオとの相関
- SP500との相関係数：{correlation_sp500}

## 直近IR・決算情報（RAGで取得）
{ir_summary}

## 出力形式
以下の構成で Markdown 形式で出力してください。

### なぜこの銘柄が候補に挙がったか

**① [最も評価された指標名]**
[指標の定義と一般的な目安の説明（2〜3文）]
[この銘柄の実数値と、それが何を意味するかの説明]

**② [次に評価された指標名]**
（同様の構成）

（スコアに影響した評価軸を全て説明する）

**直近の決算・IRより**
[IRサマリの要点を2〜3文で記述]

**⚠ 注意点・リスク**
[この銘柄特有のリスクを2〜3点箇条書き]
"""
```

#### 7.3.3 プロンプトの注入データ構造
```python
# lambda-explain が構築するコンテキスト
prompt_context = {
    # 銘柄マスタから
    "name": "○○食品",
    "ticker": "2345",
    "sector": "食料品",
    "unit_size": 100,
    # コードが計算したスコア（LLMに計算させない）
    "score_total": 0.87,
    "score_dividend": 0.32,
    "score_valuation": 0.25,
    "score_financial": 0.18,
    "score_correlation": 0.12,
    # DynamoDBから取得したファンダメンタル
    "per": 12.3,
    "sector_avg_per": 14.8,
    "pbr": 1.1,
    "dividend_yield": 3.8,
    "continuous_dividend_years": 8,
    "equity_ratio": 62.0,
    "avg_volume": 1800,
    "as_of_date": "2026-06-26",
    # ポートフォリオ相関（コードが計算）
    "correlation_sp500": 0.28,
    # IRサマリ（TDnet/適時開示のテキストをRAGで要約）
    "ir_summary": "2026年3月期：売上高+5.2%、営業利益+8.1%。...",
}
```

#### 7.3.4 LLMに「させないこと」の徹底
| やらせること | やらせないこと |
|---|---|
| 指標の定義・一般的目安の説明 | スコアの計算・再計算 |
| 数値の意味の解釈・文章化 | 「割安かどうか」の独自判断 |
| IRの要点整理・引用 | IRから数値を独自に抽出・計算 |
| リスク・懸念点の自然言語化 | 「買うべき」という推奨 |

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
