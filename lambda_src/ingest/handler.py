import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone

import boto3

from config_loader import load_config
from dynamo_writer import DynamoWriter
from jquants_fetcher import JQuantsFetcher

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ENVIRONMENT = os.environ["ENVIRONMENT"]
CONFIG_BUCKET = os.environ["CONFIG_BUCKET"]
REGION = os.environ.get("AWS_REGION_MAIN", "ap-northeast-1")
TABLE_PREFIX = f"investment-{ENVIRONMENT}"


def handler(event: dict, context) -> dict:
    """
    event パラメータ:
      run_id               : str  (省略時は自動生成)
      mode                 : "incremental" | "initial_load"  (省略時 incremental)
      from_date            : "YYYYMMDD"  (initial_load 時の株価取得開始日)
      to_date              : "YYYYMMDD"  (initial_load 時の株価取得終了日)
      fundamentals_tickers : ["7203", ...]  (指定銘柄のFundamentalsのみ取得)
    """
    run_id = event.get("run_id") or str(uuid.uuid4())
    mode = event.get("mode", "incremental")
    logger.info(json.dumps({"run_id": run_id, "mode": mode}))

    config = load_config(CONFIG_BUCKET)  # noqa: F841 (スキーマ検証目的)

    sm = boto3.client("secretsmanager", region_name=REGION)
    api_key = sm.get_secret_value(
        SecretId=f"investment/{ENVIRONMENT}/jquants-api-key"
    )["SecretString"]

    writer = DynamoWriter(TABLE_PREFIX)
    writer.log_run_start(run_id, "ingest", mode)

    try:
        fetcher = JQuantsFetcher(api_key)

        # 1. 銘柄マスタ
        securities = fetcher.get_securities()
        writer.write_securities(securities)
        logger.info(f"Securities: {len(securities)}件")

        # 2. 株価ヒストリカル
        if mode == "initial_load":
            dates = _dates_in_range(event.get("from_date"), event.get("to_date"))
        else:
            dates = _last_n_trading_dates(5)

        total_prices = 0
        for d in dates:
            prices = fetcher.get_prices_for_date(d)
            if prices:
                writer.write_prices(prices)
                total_prices += len(prices)
            logger.info(f"PriceHistory date={d}: {len(prices)}件")

        # 3. Fundamentals
        # - explicit_tickers 指定時: その銘柄のみ（初期投入バッチ用）
        # - incremental モード: その週に開示された銘柄を日付ベースで取得
        # - initial_load かつ tickers 未指定: スキップ（別途バッチ実行）
        explicit_tickers = event.get("fundamentals_tickers", [])
        total_funds = 0
        if explicit_tickers:
            for ticker in explicit_tickers:
                funds = fetcher.get_fundamentals_for_ticker(ticker)
                if funds:
                    writer.write_fundamentals(funds)
                    total_funds += len(funds)
        elif mode == "incremental":
            for d in dates:
                funds = fetcher.get_fundamentals_for_date(d)
                if funds:
                    writer.write_fundamentals(funds)
                    total_funds += len(funds)
        logger.info(f"Fundamentals: {total_funds}件")

        summary = {
            "securities": len(securities),
            "price_records": total_prices,
            "fundamental_records": total_funds,
            "dates": dates,
        }
        writer.log_run_complete(run_id, "ingest", summary)
        logger.info(json.dumps({"run_id": run_id, "result": "success", **summary}))

        return {"run_id": run_id, "status": "success", **summary}

    except Exception as e:
        logger.error(f"Ingest失敗: {e}", exc_info=True)
        writer.log_run_error(run_id, "ingest", str(e))
        raise


def _last_n_trading_dates(n: int) -> list[str]:
    jst = timezone(timedelta(hours=9))
    day = datetime.now(jst) - timedelta(days=1)
    dates = []
    while len(dates) < n:
        if day.weekday() < 5:
            dates.append(day.strftime("%Y%m%d"))
        day -= timedelta(days=1)
    return list(reversed(dates))


def _dates_in_range(from_date: str | None, to_date: str | None) -> list[str]:
    if not from_date or not to_date:
        return _last_n_trading_dates(5)
    start = datetime.strptime(from_date, "%Y%m%d")
    end = datetime.strptime(to_date, "%Y%m%d")
    dates = []
    cur = start
    while cur <= end:
        if cur.weekday() < 5:
            dates.append(cur.strftime("%Y%m%d"))
        cur += timedelta(days=1)
    return dates
