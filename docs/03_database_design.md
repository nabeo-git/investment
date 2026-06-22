# 投資支援システム DB設計書（DynamoDB）

| 項目 | 内容 |
|---|---|
| ドキュメントID | 03_database_design |
| バージョン | 1.0 |
| 最終更新 | 2026-06-23 |
| ステータス | ドラフト |

---

## 1. DB方針

### 1.1 採用ストレージ
- **Amazon DynamoDB**（オンデマンドキャパシティ）
- 無料枠：25GBストレージ／月25WCU・25RCU相当のオンデマンド利用枠
- バックアップ：PITR（Point-in-Time Recovery）有効化

### 1.2 設計方針
- MVPは**マルチテーブル設計**を採用（明快さと拡張性を優先）
- シングルテーブル設計は将来要件次第で検討（Phase2以降）
- TTLでヒストリカルデータの自動削除（無料枠維持）
- GSIは必要最小限

---

## 2. テーブル一覧

| # | テーブル名 | 用途 | Phase |
|---|---|---|---|
| 1 | `Securities` | 銘柄マスタ | 1 |
| 2 | `PriceHistory` | 株価ヒストリカル（日次） | 1 |
| 3 | `Fundamentals` | 財務・配当データ | 1 |
| 4 | `Portfolio` | 自分の保有 | 1（静的）→2（動的） |
| 5 | `Candidates` | 候補生成履歴 | 1 |
| 6 | `RunLogs` | バッチ実行ログ | 1 |
| 7 | `MacroIndicators` | マクロ指標（金利・為替・インフレ） | 2 |

---

## 3. テーブル詳細

### 3.1 `Securities`（銘柄マスタ）
| 属性 | 型 | キー | 説明 |
|---|---|---|---|
| ticker | String | PK | 証券コード（例：`7203`） |
| asset_class | String | SK | `jp_stock` / `us_stock` / `etf` / `reit` / `bond` / `gold` |
| name | String | | 銘柄名 |
| market | String | | 市場（`TSE`/`NYSE`等） |
| sector | String | | 業種 |
| currency | String | | 通貨（`JPY`/`USD`） |
| unit_size | Number | | 単元株数（日本株は通常100） |
| listed_date | String | | 上場日（ISO 8601） |
| updated_at | String | | 最終更新日時 |

- 容量見積：約5,000銘柄（日本株4000＋ETF等）×0.5KB ≒ 2.5MB
- GSI: なし（PKでアクセス）

### 3.2 `PriceHistory`（株価ヒストリカル）
| 属性 | 型 | キー | 説明 |
|---|---|---|---|
| ticker | String | PK | 証券コード |
| date | String | SK | 取引日（`YYYY-MM-DD`） |
| open | Number | | 始値 |
| high | Number | | 高値 |
| low | Number | | 安値 |
| close | Number | | 終値 |
| volume | Number | | 出来高 |
| adj_close | Number | | 調整後終値 |
| ttl | Number | | TTL（5年経過後自動削除） |
| missing_flag | Boolean | | 欠損フラグ |

- 容量見積：4,000銘柄 × 252営業日 × 3年 × 0.2KB ≒ 約600MB（無料枠25GB内）
- TTL：5年（不要な過去データは自動削除）
- GSI: なし（PK+SK範囲クエリで十分）

### 3.3 `Fundamentals`（財務・配当データ）
| 属性 | 型 | キー | 説明 |
|---|---|---|---|
| ticker | String | PK | 証券コード |
| fiscal_period | String | SK | 会計期（`2025Q4` 等） |
| as_of_date | String | | 発表日（ルックアヘッド回避用） |
| per | Number | | PER |
| pbr | Number | | PBR |
| dividend_yield | Number | | 配当利回り |
| dividend_per_share | Number | | 1株配当 |
| payout_ratio | Number | | 配当性向 |
| equity_ratio | Number | | 自己資本比率 |
| roe | Number | | ROE |
| consecutive_dividend_years | Number | | 連続増配年数 |
| missing_flag | Boolean | | 欠損フラグ |

- 容量見積：5,000銘柄 × 4四半期 × 5年 × 1KB ≒ 約100MB
- GSI: なし

### 3.4 `Portfolio`（自分の保有）
| 属性 | 型 | キー | 説明 |
|---|---|---|---|
| user_id | String | PK | `me` 固定（Phase1） |
| ticker | String | SK | 銘柄 |
| asset_class | String | | 資産クラス |
| quantity | Number | | 保有数量 |
| acquisition_price | Number | | 取得単価 |
| amount_jpy | Number | | 取得金額（円換算） |
| updated_at | String | | 最終更新日時 |

- Phase1は `config.yaml` から初期投入のみ（静的）
- Phase2でユーザINPUTからの動的更新を想定
- 容量見積：数十レコード、ほぼ0

### 3.5 `Candidates`（候補生成履歴）
| 属性 | 型 | キー | 説明 |
|---|---|---|---|
| run_id | String | PK | バッチ実行ID（`YYYYMMDDHHMMSS-uuid`） |
| ticker | String | SK | 候補銘柄 |
| score_total | Number | | 総合スコア |
| score_breakdown | Map | | 各評価軸のスコア |
| rank | Number | | ランキング順位 |
| unit_price_jpy | Number | | 単元購入額 |
| run_date | String | | 実行日（`YYYY-MM-DD`、GSI用） |
| explanation_s3_key | String | | レポート内の該当セクションS3キー |

- GSI: `run_date-score_total-index`
  - PK: `run_date`、SK: `score_total`
  - 用途：日付指定で全候補をスコア順に取得
- 容量見積：週次52回 × 10銘柄 × 5年 × 1KB ≒ 約25MB

### 3.6 `RunLogs`（バッチ実行ログ）
| 属性 | 型 | キー | 説明 |
|---|---|---|---|
| run_id | String | PK | バッチ実行ID |
| stage | String | SK | ステージ名（`ingest`/`screen-score`/`explain`/`publish`） |
| status | String | | `started` / `success` / `failed` |
| started_at | String | | 開始時刻（ISO 8601） |
| ended_at | String | | 終了時刻 |
| record_count | Number | | 処理件数 |
| error_message | String | | エラー詳細（失敗時） |
| ttl | Number | | TTL（180日） |

- TTL：180日
- 容量見積：52回×4ステージ×5年×0.5KB ≒ 約0.5MB

### 3.7 `MacroIndicators`（Phase2）
| 属性 | 型 | キー | 説明 |
|---|---|---|---|
| indicator | String | PK | 指標名（`FED_FUNDS_RATE` / `USDJPY` / `CPI_US` 等） |
| date | String | SK | 観測日 |
| value | Number | | 値 |
| source | String | | データソース（`FRED` 等） |

- Phase2で追加

---

## 4. GSI設計まとめ

| テーブル | GSI名 | PK | SK | 用途 |
|---|---|---|---|---|
| `Candidates` | `run_date-score_total-index` | run_date | score_total | 日付別スコア順検索 |

その他のテーブルはPK/SKの組合せだけで要件を満たすためGSI不要。

---

## 5. 容量見積（合計）

| テーブル | 容量 |
|---|---|
| Securities | 〜3MB |
| PriceHistory | 〜600MB（TTLで安定） |
| Fundamentals | 〜100MB |
| Portfolio | 〜1MB |
| Candidates | 〜25MB |
| RunLogs | 〜1MB |
| **合計** | **〜800MB** |

→ 無料枠 25GB の **3%程度** で運用可能。Phase2の `MacroIndicators` を加えても余裕。

---

## 6. アクセスパターン

| ユースケース | 操作 | 対象 |
|---|---|---|
| 銘柄一覧取得 | Scan + Filter or Query by asset_class | Securities |
| 特定銘柄の株価ヒストリカル取得 | Query (PK=ticker, SK between dates) | PriceHistory |
| 特定銘柄の最新財務取得 | Query (PK=ticker) ScanIndexForward=false Limit=1 | Fundamentals |
| 候補の日付別取得（スコア順） | Query GSI (run_date, score_total desc) | Candidates |
| バッチ実行履歴確認 | Query (PK=run_id) | RunLogs |

---

## 7. データ整合性・運用

| 項目 | 方針 |
|---|---|
| バックアップ | PITR有効化（全テーブル） |
| 暗号化 | AWSマネージドKMS（デフォルト） |
| 削除保護 | 本番テーブルは削除保護有効 |
| TTL運用 | PriceHistory・RunLogsで容量管理 |
| データas-of | Fundamentalsの `as_of_date` でルックアヘッド回避 |

---

## 8. 関連ドキュメント
- `01_requirements.md`
- `02_basic_design.md`
- `04_infrastructure_design.md`
