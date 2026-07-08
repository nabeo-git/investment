# 投資支援システム DB設計書（DynamoDB）

| 項目 | 内容 |
|---|---|
| ドキュメントID | 03_database_design |
| バージョン | 1.2 |
| 最終更新 | 2026-06-24 |
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
> ソース：J-Quants `Equities Master` API（`/v2/equities/master`）

| 属性 | 型 | キー | API元フィールド | 説明 |
|---|---|---|---|---|
| ticker | String | PK | `Code` | 証券コード（例：`86970`） |
| asset_class | String | SK | （派生） | `jp_stock` / `us_stock` / `etf` / `reit` / `bond` / `gold` |
| name_ja | String | | `CoName` | 銘柄名（日本語） |
| name_en | String | | `CoNameEn` | 銘柄名（英語） |
| sector_17_code | String | | `S17` | 17業種コード |
| sector_17_name | String | | `S17Nm` | 17業種名 |
| sector_33_code | String | | `S33` | 33業種コード |
| sector_33_name | String | | `S33Nm` | 33業種名 |
| scale_category | String | | `ScaleCat` | 規模区分（TOPIX Large70等） |
| market_code | String | | `Mkt` | 市場コード |
| market_name | String | | `MktNm` | 市場名 |
| margin_code | String | | `Mrgn` | 信用区分コード |
| currency | String | | （固定） | `JPY`（日本株） |
| unit_size | Number | | （固定） | `100`（単元株数、日本株は通常100） |
| updated_at | String | | `Date` | データ取得日時 |

- 容量見積：約5,000銘柄 × 0.5KB ≒ 2.5MB
- GSI: なし（PK単独アクセス）
- **注意**：`Date` はマスタ取得日時であり上場日ではない。上場日は別途取得要。

### 3.2 `PriceHistory`（株価ヒストリカル）
> ソース：J-Quants `Equities Bars Daily` API（`/v2/equities/bars/daily`）

| 属性 | 型 | キー | API元フィールド | 説明 |
|---|---|---|---|---|
| ticker | String | PK | `Code` | 証券コード |
| date | String | SK | `Date` | 取引日（`YYYY-MM-DD`） |
| open | Number | | `O` | 始値 |
| high | Number | | `H` | 高値 |
| low | Number | | `L` | 安値 |
| close | Number | | `C` | 終値 |
| volume | Number | | `Vo` | 出来高 |
| turnover_value | Number | | `Va` | 売買代金 |
| adj_factor | Number | | `AdjFactor` | 調整係数（分割・合併考慮） |
| adj_close | Number | | `AdjC` | 調整後終値（スコアリングで使用） |
| adj_volume | Number | | `AdjVo` | 調整後出来高 |
| ttl | Number | | （計算） | TTL（取得日+5年のUnixtime） |
| missing_flag | Boolean | | （派生） | 欠損フラグ |

- 容量見積：4,000銘柄 × 252営業日 × 3年 × 0.2KB ≒ 約600MB（無料枠25GB内）
- TTL：5年（不要な過去データは自動削除）
- GSI: なし（PK+SK範囲クエリで十分）
- **注意**：前場/後場別（M*/A*プレフィックス列）はPremium限定。Phase1では日通しデータのみ保管。

### 3.3 `Fundamentals`（財務・配当データ）
> ソース：J-Quants `Financial Summary` API（`/v2/fins/summary`）  
> **全プランで取得可能。PER/PBRは直接提供されないため、EPS・BPS + 当日終値から計算する。**

| 属性 | 型 | キー | API元フィールド | 説明 |
|---|---|---|---|---|
| ticker | String | PK | `Code` | 証券コード |
| disc_date | String | SK | `DiscDate` | 開示日（`YYYY-MM-DD`）。**as_of_date**として機能 |
| doc_type | String | | `DocType` | 開示種別（`FYFinancialStatements_*`, `1QFinancialStatements_*` 等） |
| period_type | String | | `CurPerType` | 期間種別（`FY` / `1Q` / `2Q` / `3Q`） |
| period_start | String | | `CurPerSt` | 期間開始日 |
| period_end | String | | `CurPerEn` | 期間終了日 |
| fy_start | String | | `CurFYSt` | 当該年度開始日 |
| fy_end | String | | `CurFYEn` | 当該年度終了日 |
| sales | Number | | `Sales` | 売上高 |
| operating_profit | Number | | `OP` | 営業利益 |
| net_profit | Number | | `NP` | 純利益 |
| eps | Number | | `EPS` | 1株当たり利益（PER計算に使用） |
| bps | Number | | `BPS` | 1株当たり純資産（PBR計算に使用） |
| total_assets | Number | | `TA` | 総資産 |
| equity | Number | | `Eq` | 純資産 |
| equity_ratio | Number | | `EqAR` | 自己資本比率（直接取得可） |
| cfo | Number | | `CFO` | 営業CF |
| div_fy_actual | Number | | `DivFY` | 当期実績配当（通期） |
| div_forecast_ann | Number | | `FDivAnn` | 当期予想配当（年間合計、配当利回り計算に使用） |
| payout_ratio_forecast | Number | | `FPayoutRatioAnn` | 予想配当性向 |
| sales_forecast | Number | | `FSales` | 当期予想売上 |
| np_forecast | Number | | `FNP` | 当期予想純利益 |
| eps_forecast | Number | | `FEPS` | 当期予想EPS |
| shares_outstanding | Number | | `ShOutFY` | 発行済株式数 |
| consecutive_dividend_years | Number | | （計算） | 連続増配年数（過去`DivFY`の時系列から算出） |
| per_calc | Number | | （計算） | PER（取得時の終値 ÷ EPS で計算、格納） |
| pbr_calc | Number | | （計算） | PBR（取得時の終値 ÷ BPS で計算、格納） |
| dividend_yield_calc | Number | | （計算） | 配当利回り（FDivAnn ÷ 取得時終値 で計算） |
| missing_flag | Boolean | | （派生） | 欠損フラグ |

- 容量見積：5,000銘柄 × 4四半期 × 5年 × 1.5KB ≒ 約150MB
- GSI: なし
- **PER・PBR・配当利回りの計算タイミング**：`lambda-ingest` が `DiscDate` 当日（または翌営業日）の終値と組み合わせて計算し保管する
- **連続増配年数の計算**：`DivFY` の時系列を過去5年分（Light）検索し、増配継続しているか判定
- **ルックアヘッド回避**：スクリーニング・スコアリングでは、`disc_date <= バッチ実行日` のレコードのみ参照する

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
