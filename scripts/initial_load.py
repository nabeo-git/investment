"""
初期データ投入スクリプト
- Bulk APIで過去1年分の月次CSVを取得
- Securities / PriceHistory / Fundamentals を DynamoDB に書き込む

使い方:
  $env:PYTHONUTF8 = "1"
  python scripts/initial_load.py

オプション:
  --months N     取得月数（デフォルト: 12）
  --skip-prices  株価スキップ
  --skip-fins    財務スキップ
"""
import argparse
import gzip
import io
import logging
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

import boto3
import jquantsapi
import pandas as pd
from boto3.dynamodb.types import TypeSerializer

# -----------------------------------------------------------
# ログ設定
# -----------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

_serializer = TypeSerializer()
REGION = "ap-northeast-1"
ENVIRONMENT = "dev"
TABLE_PREFIX = f"investment-{ENVIRONMENT}"

# -----------------------------------------------------------
# ユーティリティ
# -----------------------------------------------------------
def _dec(v) -> Optional[Decimal]:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    try:
        d = Decimal(str(v))
        return None if d.is_nan() else d
    except (InvalidOperation, ValueError):
        return None


def _serialize(item: dict) -> dict:
    cleaned = {k: v for k, v in item.items() if v is not None and v != "" and v != "None"}
    return {k: _serializer.serialize(v) for k, v in cleaned.items()}


def batch_write(client, table_name: str, items: list[dict]) -> int:
    CHUNK = 25
    written = 0
    for i in range(0, len(items), CHUNK):
        chunk = items[i: i + CHUNK]
        req = {table_name: [{"PutRequest": {"Item": _serialize(it)}} for it in chunk]}
        retries = 0
        while req and retries < 5:
            resp = client.batch_write_item(RequestItems=req)
            req = resp.get("UnprocessedItems")
            if req:
                retries += 1
                time.sleep(0.5 * (2 ** retries))
        written += len(chunk)
    return written


def get_api_key() -> str:
    sm = boto3.client("secretsmanager", region_name=REGION)
    return sm.get_secret_value(
        SecretId=f"investment/{ENVIRONMENT}/jquants-api-key"
    )["SecretString"]


def month_range(n_months: int) -> list[str]:
    """直近 n_months ヶ月の YYYYMM リストを返す（新しい順）。"""
    months = []
    dt = datetime.now().replace(day=1) - timedelta(days=1)  # 先月末
    for _ in range(n_months):
        months.append(dt.strftime("%Y%m"))
        dt = dt.replace(day=1) - timedelta(days=1)
    return months


# -----------------------------------------------------------
# Step 1: Securities
# -----------------------------------------------------------
def load_securities(client, ddb_client) -> int:
    log.info("▶ Securities マスタ取得中...")
    df = client.get_list()
    records = []
    for _, row in df.iterrows():
        records.append({
            "ticker": str(row.get("Code", "")),
            "asset_class": "jp_stock",
            "name_ja": str(row.get("CoName", "")),
            "name_en": str(row.get("CoNameEn", "")),
            "sector_17_code": str(row.get("S17", "")),
            "sector_17_name": str(row.get("S17Nm", "")),
            "sector_33_code": str(row.get("S33", "")),
            "sector_33_name": str(row.get("S33Nm", "")),
            "scale_category": str(row.get("ScaleCat", "")),
            "market_code": str(row.get("Mkt", "")),
            "market_name": str(row.get("MktNm", "")),
            "currency": "JPY",
            "unit_size": 100,
            "updated_at": str(row.get("Date", "")),
        })
    written = batch_write(ddb_client, f"{TABLE_PREFIX}-Securities", records)
    log.info(f"  → Securities: {written} 件書き込み完了")
    return written


# -----------------------------------------------------------
# Step 2: PriceHistory（Bulk CSV）
# -----------------------------------------------------------
def parse_price_csv(df: pd.DataFrame) -> list[dict]:
    ttl = int(time.time()) + 5 * 365 * 24 * 3600
    records = []
    for _, row in df.iterrows():
        date_str = str(row.get("Date", ""))
        if len(date_str) == 8:
            date_str = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
        records.append({
            "ticker": str(row.get("Code", "")),
            "date": date_str,
            "open": _dec(row.get("Open", row.get("O"))),
            "high": _dec(row.get("High", row.get("H"))),
            "low": _dec(row.get("Low", row.get("L"))),
            "close": _dec(row.get("Close", row.get("C"))),
            "volume": _dec(row.get("Volume", row.get("Vo"))),
            "turnover_value": _dec(row.get("TurnoverValue", row.get("Va"))),
            "adj_factor": _dec(row.get("AdjustmentFactor", row.get("AdjFactor"))),
            "adj_close": _dec(row.get("AdjustmentClose", row.get("AdjC"))),
            "adj_volume": _dec(row.get("AdjustmentVolume", row.get("AdjVo"))),
            "ttl": ttl,
            "missing_flag": False,
        })
    return records


def load_prices(client, ddb_client, months: list[str]) -> int:
    log.info(f"▶ PriceHistory Bulk取得: {len(months)} ヶ月分")
    total = 0
    with tempfile.TemporaryDirectory() as tmpdir:
        for ym in months:
            path = os.path.join(tmpdir, f"prices_{ym}.csv.gz")
            try:
                client.download_bulk_by_endpoint(
                    endpoint="equities/bars/daily",
                    date=ym,
                    output_path=path,
                )
                with gzip.open(path, "rt", encoding="utf-8") as f:
                    df = pd.read_csv(f)
                records = parse_price_csv(df)
                written = batch_write(ddb_client, f"{TABLE_PREFIX}-PriceHistory", records)
                total += written
                log.info(f"  {ym}: {written} 件書き込み")
            except Exception as e:
                log.warning(f"  {ym}: スキップ ({e})")
    log.info(f"  → PriceHistory 合計: {total} 件")
    return total


# -----------------------------------------------------------
# Step 3: Fundamentals（Bulk CSV）
# -----------------------------------------------------------
def parse_fins_csv(df: pd.DataFrame) -> list[dict]:
    records = []
    for _, row in df.iterrows():
        disc_date = str(row.get("DiscDate", ""))
        if len(disc_date) == 8:
            disc_date = f"{disc_date[:4]}-{disc_date[4:6]}-{disc_date[6:]}"
        records.append({
            "ticker": str(row.get("Code", "")),
            "disc_date": disc_date,
            "doc_type": str(row.get("DocType", "")),
            "period_type": str(row.get("CurPerType", "")),
            "period_start": str(row.get("CurPerSt", "")),
            "period_end": str(row.get("CurPerEn", "")),
            "fy_start": str(row.get("CurFYSt", "")),
            "fy_end": str(row.get("CurFYEn", "")),
            "sales": _dec(row.get("Sales")),
            "operating_profit": _dec(row.get("OP")),
            "net_profit": _dec(row.get("NP")),
            "eps": _dec(row.get("EPS")),
            "bps": _dec(row.get("BPS")),
            "total_assets": _dec(row.get("TA")),
            "equity": _dec(row.get("Eq")),
            "equity_ratio": _dec(row.get("EqAR")),
            "cfo": _dec(row.get("CFO")),
            "div_fy_actual": _dec(row.get("DivFY")),
            "div_forecast_ann": _dec(row.get("FDivAnn")),
            "payout_ratio_forecast": _dec(row.get("FPayoutRatioAnn")),
            "sales_forecast": _dec(row.get("FSales")),
            "np_forecast": _dec(row.get("FNP")),
            "eps_forecast": _dec(row.get("FEPS")),
        })
    return records


def load_fundamentals(client, ddb_client, months: list[str]) -> int:
    log.info(f"▶ Fundamentals Bulk取得: {len(months)} ヶ月分")
    total = 0
    with tempfile.TemporaryDirectory() as tmpdir:
        for ym in months:
            path = os.path.join(tmpdir, f"fins_{ym}.csv.gz")
            try:
                client.download_bulk_by_endpoint(
                    endpoint="fins/summary",
                    date=ym,
                    output_path=path,
                )
                with gzip.open(path, "rt", encoding="utf-8") as f:
                    df = pd.read_csv(f)
                records = parse_fins_csv(df)
                # BatchWriteItem は同一バッチ内の重複キー不可 → (ticker, disc_date) でデデュプ
                seen = set()
                deduped = []
                for r in records:
                    key = (r["ticker"], r["disc_date"])
                    if key not in seen:
                        seen.add(key)
                        deduped.append(r)
                written = batch_write(ddb_client, f"{TABLE_PREFIX}-Fundamentals", deduped)
                total += written
                log.info(f"  {ym}: {written} 件書き込み（元{len(records)}件→重複除去後{len(deduped)}件）")
            except Exception as e:
                log.warning(f"  {ym}: スキップ ({e})")
    log.info(f"  → Fundamentals 合計: {total} 件")
    return total


# -----------------------------------------------------------
# メイン
# -----------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="投資支援システム 初期データ投入")
    parser.add_argument("--months", type=int, default=12, help="取得月数（デフォルト: 12）")
    parser.add_argument("--skip-prices", action="store_true")
    parser.add_argument("--skip-fins", action="store_true")
    args = parser.parse_args()

    log.info("=== 初期データ投入開始 ===")
    log.info(f"  対象: {args.months} ヶ月分 / テーブルプレフィックス: {TABLE_PREFIX}")

    api_key = get_api_key()
    jquants_client = jquantsapi.ClientV2(api_key=api_key)
    ddb_client = boto3.client("dynamodb", region_name=REGION)

    months = month_range(args.months)
    log.info(f"  対象月: {months[-1]} 〜 {months[0]}")

    start = time.time()

    # Step 1: Securities
    load_securities(jquants_client, ddb_client)

    # Step 2: PriceHistory
    if not args.skip_prices:
        load_prices(jquants_client, ddb_client, months)
    else:
        log.info("▶ PriceHistory スキップ")

    # Step 3: Fundamentals
    if not args.skip_fins:
        load_fundamentals(jquants_client, ddb_client, months)
    else:
        log.info("▶ Fundamentals スキップ")

    elapsed = time.time() - start
    log.info(f"=== 完了 ({elapsed:.0f}秒) ===")


if __name__ == "__main__":
    main()
