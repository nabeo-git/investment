"""
J-Quants Bulk CSV.gz を S3 に保存するスクリプト（DynamoDB書き込みなし）
full_history_load.py でDynamoDBへの投入が完了した後、
生データをS3にアーカイブするために使用。

使い方:
  $env:PYTHONUTF8 = "1"
  python scripts/save_bulk_to_s3.py              # 財務 + 株価
  python scripts/save_bulk_to_s3.py --skip-prices  # 財務のみ
  python scripts/save_bulk_to_s3.py --skip-fins    # 株価のみ
"""
import argparse
import logging
import os
import sys
import tempfile
import time
from datetime import datetime

import boto3
import jquantsapi

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

REGION = "ap-northeast-1"
ENVIRONMENT = "dev"
S3_BUCKET = "investment-dev-config-YOUR_ACCOUNT_ID"
S3_BULK_PREFIX = "bulk-data"
BULK_START = datetime(2021, 7, 1)


def get_api_key() -> str:
    sm = boto3.client("secretsmanager", region_name=REGION)
    return sm.get_secret_value(SecretId=f"investment/{ENVIRONMENT}/jquants-api-key")["SecretString"]


def all_months() -> list[str]:
    months = []
    dt = BULK_START
    now = datetime.now().replace(day=1)
    while dt <= now:
        months.append(dt.strftime("%Y%m"))
        if dt.month == 12:
            dt = dt.replace(year=dt.year + 1, month=1)
        else:
            dt = dt.replace(month=dt.month + 1)
    return months


def save_endpoint_to_s3(client, s3, endpoint: str, s3_prefix: str, months: list[str], tmpdir: str) -> int:
    saved = 0
    skipped = 0
    for ym in months:
        s3_key = f"{S3_BULK_PREFIX}/{s3_prefix}/{ym}.csv.gz"
        # 既存ファイルがあればスキップ
        try:
            s3.head_object(Bucket=S3_BUCKET, Key=s3_key)
            log.info(f"  既存スキップ {ym}: s3://{S3_BUCKET}/{s3_key}")
            skipped += 1
            continue
        except Exception:
            pass

        path = os.path.join(tmpdir, f"{s3_prefix}_{ym}.csv.gz")
        try:
            client.download_bulk_by_endpoint(endpoint=endpoint, date=ym, output_path=path)
            size_kb = os.path.getsize(path) // 1024
            s3.upload_file(path, S3_BUCKET, s3_key)
            log.info(f"  保存 {ym}: {size_kb}KB → s3://{S3_BUCKET}/{s3_key}")
            saved += 1
        except Exception as e:
            log.warning(f"  スキップ {ym}: {str(e)[:80]}")
        finally:
            if os.path.exists(path):
                os.remove(path)
        time.sleep(0.1)
    return saved


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-prices", action="store_true")
    parser.add_argument("--skip-fins", action="store_true")
    args = parser.parse_args()

    months = all_months()
    log.info(f"=== S3アーカイブ開始 ===")
    log.info(f"  対象: {months[0]} 〜 {months[-1]}（{len(months)}ヶ月）")
    log.info(f"  保存先: s3://{S3_BUCKET}/{S3_BULK_PREFIX}/")

    api_key = get_api_key()
    jq = jquantsapi.ClientV2(api_key=api_key)
    s3 = boto3.client("s3", region_name=REGION)

    start = time.time()

    with tempfile.TemporaryDirectory() as tmpdir:
        if not args.skip_fins:
            log.info("▶ Fundamentals (fins/summary) をS3に保存中...")
            n = save_endpoint_to_s3(jq, s3, "fins/summary", "fins", months, tmpdir)
            log.info(f"  財務: {n}ヶ月分保存完了")

        if not args.skip_prices:
            log.info("▶ PriceHistory (equities/bars/daily) をS3に保存中...")
            n = save_endpoint_to_s3(jq, s3, "equities/bars/daily", "prices", months, tmpdir)
            log.info(f"  株価: {n}ヶ月分保存完了")

    elapsed = time.time() - start
    log.info(f"=== 完了 ({elapsed/60:.1f}分) ===")
    log.info(f"  確認: aws s3 ls s3://{S3_BUCKET}/{S3_BULK_PREFIX}/ --recursive --region {REGION}")


if __name__ == "__main__":
    main()
