import json
import logging
import os
import re
import urllib.request
from datetime import datetime, timezone

import boto3

from config_loader import load_config

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ENVIRONMENT = os.environ["ENVIRONMENT"]
CONFIG_BUCKET = os.environ["CONFIG_BUCKET"]
REPORTS_BUCKET = os.environ["REPORTS_BUCKET"]
REGION = os.environ.get("AWS_REGION_MAIN", "ap-northeast-1")
TABLE_PREFIX = f"investment-{ENVIRONMENT}"

PRESIGNED_URL_EXPIRES = 48 * 3600  # 48時間


def handler(event: dict, context) -> dict:
    run_id = event.get("run_id")
    report_key = event.get("report_key")
    if not run_id or not report_key:
        raise ValueError("run_id または report_key が event に含まれていません")

    logger.info(json.dumps({"run_id": run_id, "report_key": report_key}))

    load_config(CONFIG_BUCKET)  # スキーマ検証

    sm = boto3.client("secretsmanager", region_name=REGION)
    slack_url = sm.get_secret_value(
        SecretId=f"investment/{ENVIRONMENT}/slack-webhook-url"
    )["SecretString"]

    s3 = boto3.client("s3", region_name=REGION)
    sns = boto3.client("sns", region_name=REGION)
    ddb = boto3.resource("dynamodb")
    t_run_logs = ddb.Table(f"{TABLE_PREFIX}-RunLogs")

    t_run_logs.put_item(Item={
        "run_id": run_id,
        "stage": "publish",
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
    })

    try:
        # 署名付きURL生成（48時間有効）
        presigned_url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": REPORTS_BUCKET, "Key": report_key},
            ExpiresIn=PRESIGNED_URL_EXPIRES,
        )
        logger.info("署名付きURL生成完了")

        run_date = report_key.split("/")[1] if "/" in report_key else "不明"

        # HTMLからサマリー抽出（業種×銘柄名 上位3件）
        html_body = s3.get_object(Bucket=REPORTS_BUCKET, Key=report_key)["Body"].read().decode("utf-8")
        sector_names = re.findall(r'<span class="sector-icon">▸</span>(.+?)</h2>', html_body)[:3]
        stock_names = re.findall(r'<span class="stock-name">(.+?)</span>', html_body)[:3]
        if stock_names:
            summary = "\n".join(
                f"• {s}：{n}" for s, n in zip(sector_names, stock_names)
            ) if sector_names else "\n".join(f"• {n}" for n in stock_names)
        else:
            summary = "(候補なし)"

        slack_payload = {
            "text": (
                f":bar_chart: *投資候補レポート {run_date} 生成完了*\n\n"
                f"*各業種トップ候補:*\n{summary}\n\n"
                f":link: *HTMLレポート（48時間有効）:*\n{presigned_url}"
            )
        }
        _post_slack(slack_url, slack_payload)
        logger.info("Slack通知完了")

        # SNSメール通知
        sns_topic_arn = _get_sns_topic_arn()
        if sns_topic_arn:
            sns.publish(
                TopicArn=sns_topic_arn,
                Subject=f"[InvestmentSystem] 投資候補レポート {run_date}",
                Message=(
                    f"週次投資候補レポートが生成されました。\n\n"
                    f"▼ HTMLレポート（48時間有効・ブラウザで開けます）\n{presigned_url}\n\n"
                    f"--- 各業種トップ候補 ---\n{summary}"
                ),
            )
            logger.info("SNSメール通知完了")

        t_run_logs.update_item(
            Key={"run_id": run_id, "stage": "publish"},
            UpdateExpression="SET #s = :s, completed_at = :t, presigned_url = :u",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":s": "success",
                ":t": datetime.now(timezone.utc).isoformat(),
                ":u": presigned_url,
            },
        )

        return {"run_id": run_id, "status": "success", "report_key": report_key}

    except Exception as e:
        logger.error(f"Publish失敗: {e}", exc_info=True)
        t_run_logs.update_item(
            Key={"run_id": run_id, "stage": "publish"},
            UpdateExpression="SET #s = :s, completed_at = :t, error_message = :e",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":s": "error",
                ":t": datetime.now(timezone.utc).isoformat(),
                ":e": str(e),
            },
        )
        raise


def _post_slack(webhook_url: str, payload: dict) -> None:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        if resp.status != 200:
            raise RuntimeError(f"Slack POST失敗: status={resp.status}")


def _get_sns_topic_arn() -> str | None:
    return os.environ.get("SNS_TOPIC_ARN")
