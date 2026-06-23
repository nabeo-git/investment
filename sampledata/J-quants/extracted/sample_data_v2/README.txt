
1. 各APIの仕様詳細は以下を参照下さい。As for each API specification, please refer to the following.
日本語　https://jpx-jquants.com/ja/spec/
English　https://jpx-jquants.com/en/spec/

2. サンプルファイルごとのAPI実行条件は以下の通りです。As for each sample file, the combinations of parameter in the request are as follows.

■  Listed Issue Master.csv = 上場銘柄一覧
　　　code = 86970
　　　date ＝ 20220104

■  Stock Prices (OHLC).csv = 株価四本値
　　　code = 86970
　　　from = 20220104
　　　to = 20220110
     *日通しデータについては全プランで取得できますが、前場/後場別のデータについてはPremiumプランのみ取得可能です。
　　　Note: Daily prices can be obtained for all plans, but morning/afternoon session prices are available only for premium plan.

■  Financial Data.csv = 財務情報
　　　code = 86970
　　　*このサンプルファイルでは2022年中に公表された開示のみを収録しています。
　　　Note: This sample file contains only disclosures published during 2022.

■  Earnings Calendar.csv = 決算発表予定日

■  Trading Calendar.csv = 取引カレンダー
     from = 20220101
　　　to = 20221231

■  Trading by Type of Investors.csv = 投資部門別情報
　　　from = 20220104
　　　to = 20220110

■  TOPIX Prices (OHLC).csv = TOPIX四本値
　　　from = 20220104
　　　to = 20220110

■  Indices Prices(OHLC).csv = 指数四本値
　　　date = 20220104

■  Index Option Prices (OHLC).csv = オプション四本値
　　　date ＝ 20220104

■  Margin Trading Outstandings.csv = 信用取引週末残高
　　　code = 86970
　　　from = 20220104
　　　to = 20220110

■  Outstanding Short Selling Positions Reported.csv =  空売り残高報告
　　　code = 86970
　　　disc_date_from = 20250101
　　　disc_date_to = 20250430

■  Daily Margin Interest.csv = 日々公表信用取引残高
　　　code = 13210
　　　from = 20230101
　　　to = 20231231

■  Short Sale Value and Ratio by Sector.csv = 業種別空売り比率
　　　date ＝ 20220104

■  Breakdown Trading Data.csv = 売買内訳データ
　　　code = 86970
　　　from = 20220104
　　　to = 20220110

■  Morning Session Stock Prices (OHLC).csv = 前場四本値
　　　code = 86970

■  Cash Dividend Data.csv = 配当金情報
　　　code = 86970
　　　from = 20220104
　　　to = 20221230

■  Financial Statement Data(BSPLCF).csv = 財務諸表(BS/PL/CF)
　　　code = 86970
　　　*このサンプルファイルでは2022年中に公表された開示のみを収録しています。
　　　Note: This sample file contains only disclosures published during 2022.

■  Futures data (OHLC and settlement price).csv = 先物四本値
　　　date = 20220104
　　　contract_flag = 1

■  Options data (OHLC and settlement price).csv =  オプション四本値
　　　category = NK225MWE
　　　date = 20230529
　　　contract_flag = 1
