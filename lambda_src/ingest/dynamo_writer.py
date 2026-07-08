import json
import logging
import time
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.types import TypeSerializer

logger = logging.getLogger(__name__)
_serializer = TypeSerializer()


def _serialize(item: dict) -> dict:
    """None値を除去してDynamoDB形式にシリアライズ。"""
    cleaned = {k: v for k, v in item.items() if v is not None and v != "None" and v != ""}
    return {k: _serializer.serialize(v) for k, v in cleaned.items()}


def _batch_write(client, table_name: str, items: list[dict]) -> None:
    """25件ずつBatchWriteItemし、UnprocessedItemsをリトライ。"""
    CHUNK = 25
    for i in range(0, len(items), CHUNK):
        chunk = items[i: i + CHUNK]
        request_items = {
            table_name: [
                {"PutRequest": {"Item": _serialize(item)}} for item in chunk
            ]
        }
        retries = 0
        while request_items and retries < 5:
            resp = client.batch_write_item(RequestItems=request_items)
            request_items = resp.get("UnprocessedItems")
            if request_items:
                retries += 1
                time.sleep(0.5 * (2 ** retries))


class DynamoWriter:
    def __init__(self, table_prefix: str):
        self.client = boto3.client("dynamodb")
        self.t_securities = f"{table_prefix}-Securities"
        self.t_price = f"{table_prefix}-PriceHistory"
        self.t_fundamentals = f"{table_prefix}-Fundamentals"
        self.t_run_logs = f"{table_prefix}-RunLogs"

    def write_securities(self, records: list[dict]) -> None:
        _batch_write(self.client, self.t_securities, records)

    def write_prices(self, records: list[dict]) -> None:
        _batch_write(self.client, self.t_price, records)

    def write_fundamentals(self, records: list[dict]) -> None:
        _batch_write(self.client, self.t_fundamentals, records)

    def log_run_start(self, run_id: str, stage: str, mode: str) -> None:
        item = {
            "run_id": run_id,
            "stage": stage,
            "status": "running",
            "mode": mode,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        self.client.put_item(TableName=self.t_run_logs, Item=_serialize(item))

    def log_run_complete(self, run_id: str, stage: str, summary: dict) -> None:
        self.client.update_item(
            TableName=self.t_run_logs,
            Key=_serialize({"run_id": run_id, "stage": stage}),
            UpdateExpression="SET #s = :s, completed_at = :t, summary = :sum",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues=_serialize({
                ":s": "success",
                ":t": datetime.now(timezone.utc).isoformat(),
                ":sum": json.dumps(summary, default=str),
            }),
        )

    def log_run_error(self, run_id: str, stage: str, error: str) -> None:
        self.client.update_item(
            TableName=self.t_run_logs,
            Key=_serialize({"run_id": run_id, "stage": stage}),
            UpdateExpression="SET #s = :s, completed_at = :t, error_message = :e",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues=_serialize({
                ":s": "error",
                ":t": datetime.now(timezone.utc).isoformat(),
                ":e": error,
            }),
        )
