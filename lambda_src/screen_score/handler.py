import json
import logging
import math
import os
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.types import TypeSerializer

from config_loader import load_config
from screener import Screener
from scorer import Scorer

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ENVIRONMENT = os.environ["ENVIRONMENT"]
CONFIG_BUCKET = os.environ["CONFIG_BUCKET"]
REGION = os.environ.get("AWS_REGION_MAIN", "ap-northeast-1")
TABLE_PREFIX = f"investment-{ENVIRONMENT}"

_serializer = TypeSerializer()


def _serialize(item: dict) -> dict:
    cleaned = {}
    for k, v in item.items():
        if v is None:
            continue
        if isinstance(v, float):
            if math.isnan(v) or math.isinf(v):
                continue
            v = Decimal(str(v))
        cleaned[k] = v
    return {k: _serializer.serialize(v) for k, v in cleaned.items()}


def _batch_write(client, table_name: str, items: list[dict]) -> None:
    CHUNK = 25
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


def _select_by_sector(scored: list[dict], sector_map: dict[str, str], per_sector: int) -> list[dict]:
    """業種別に上位 per_sector 銘柄を選出し、業種×スコア順で並べた一覧を返す。"""
    buckets: dict[str, list[dict]] = defaultdict(list)
    for c in scored:
        sector = sector_map.get(c["ticker"], "その他")
        c["sector"] = sector
        buckets[sector].append(c)

    result = []
    rank = 1
    # 各業種の上位 per_sector 銘柄を収集（スコア順に既にソート済み）
    sector_tops: dict[str, list[dict]] = {}
    for sector, candidates in sorted(buckets.items()):
        top = candidates[:per_sector]
        if top:
            sector_tops[sector] = top

    # 業種内ランクを付与して全体リストを構築（業種アルファベット順→スコア降順）
    overall_rank = 1
    for sector, tops in sector_tops.items():
        for sector_rank, c in enumerate(tops, start=1):
            result.append({**c, "rank": overall_rank, "sector_rank": sector_rank})
            overall_rank += 1

    logger.info(f"業種別選出: {len(sector_tops)}業種 × 最大{per_sector}銘柄 = {len(result)}銘柄")
    for sector, tops in sector_tops.items():
        tickers = [t["ticker"] for t in tops]
        logger.info(f"  {sector}: {tickers}")

    return result


def handler(event: dict, context) -> dict:
    run_id = event.get("run_id")
    if not run_id:
        raise ValueError("run_id が event に含まれていません")

    logger.info(json.dumps({"run_id": run_id, "stage": "screen_score"}))

    config = load_config(CONFIG_BUCKET)
    ddb_client = boto3.client("dynamodb")
    ddb_resource = boto3.resource("dynamodb")

    t_run_logs = ddb_resource.Table(f"{TABLE_PREFIX}-RunLogs")
    t_candidates = f"{TABLE_PREFIX}-Candidates"

    t_run_logs.put_item(Item={
        "run_id": run_id,
        "stage": "screen_score",
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
    })

    try:
        screener = Screener(TABLE_PREFIX)
        scorer = Scorer()
        cfg_cand = config.candidates

        tickers = screener.get_all_tickers()
        logger.info(f"対象銘柄数: {len(tickers)}")

        passed = screener.screen(config, tickers)
        # 全体スコアリング（min-max正規化のベースは全通過銘柄）
        scored = scorer.score(passed, config.scoring.weights, config.valuation)

        run_date = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d")

        if cfg_cand.sector_mode:
            sector_map = screener.get_sector_map(cfg_cand.sector_classification)
            top_candidates = _select_by_sector(scored, sector_map, cfg_cand.per_sector)
        else:
            top_candidates = scored[:cfg_cand.top_n]
            for rank, c in enumerate(top_candidates, start=1):
                c["rank"] = rank
                c["sector_rank"] = 1
                c["sector"] = ""

        records = []
        for c in top_candidates:
            records.append({
                "run_id": run_id,
                "ticker": c["ticker"],
                "run_date": run_date,
                "rank": c["rank"],
                "sector": c.get("sector", ""),
                "sector_rank": c.get("sector_rank", 1),
                # スコア
                "score_total": c["score_total"],
                "score_quantitative": c["score_quantitative"],
                "score_roe_stability": c["score_roe_stability"],
                "score_growth_quality": c["score_growth_quality"],
                "score_financial_solidity": c["score_financial_solidity"],
                # バリュエーション
                "valuation_status": c.get("valuation_status", "unknown"),
                "margin_of_safety": str(c.get("margin_of_safety", "")) if c.get("margin_of_safety") is not None else "",
                "intrinsic_value": str(c.get("intrinsic_value", "")) if c.get("intrinsic_value") is not None else "",
                # 株価・財務スナップショット
                "close": str(c.get("close", "")),
                "eps": str(c.get("eps", "")) if c.get("eps") is not None else "",
                "eps_cagr": str(round(c["eps_cagr"], 4)) if c.get("eps_cagr") is not None else "",
                "cfo_quality": str(round(c["cfo_quality"], 4)) if c.get("cfo_quality") is not None else "",
                "debt_multiple": str(round(c["debt_multiple"], 2)) if c.get("debt_multiple") is not None else "",
                "equity_ratio": str(c.get("equity_ratio", "")) if c.get("equity_ratio") is not None else "",
                "roe_years": c.get("roe_years", 0),
                "disc_date": c.get("disc_date", ""),
                "fy_end": c.get("fy_end", ""),
            })

        _batch_write(ddb_client, t_candidates, records)

        buy_count = sum(1 for c in top_candidates if c.get("valuation_status") == "buy")
        summary = {
            "screened_in": len(passed),
            "candidates": len(top_candidates),
            "sector_mode": cfg_cand.sector_mode,
            "top_ticker": top_candidates[0]["ticker"] if top_candidates else None,
            "buy_status_count": buy_count,
        }
        t_run_logs.update_item(
            Key={"run_id": run_id, "stage": "screen_score"},
            UpdateExpression="SET #s = :s, completed_at = :t, summary = :sum",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":s": "success",
                ":t": datetime.now(timezone.utc).isoformat(),
                ":sum": json.dumps(summary),
            },
        )

        logger.info(json.dumps({"run_id": run_id, "result": "success", **summary}))
        return {"run_id": run_id, "status": "success", "candidates": len(top_candidates)}

    except Exception as e:
        logger.error(f"ScreenScore失敗: {e}", exc_info=True)
        t_run_logs.update_item(
            Key={"run_id": run_id, "stage": "screen_score"},
            UpdateExpression="SET #s = :s, completed_at = :t, error_message = :e",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":s": "error",
                ":t": datetime.now(timezone.utc).isoformat(),
                ":e": str(e),
            },
        )
        raise
