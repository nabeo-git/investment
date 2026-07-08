import logging
from decimal import Decimal
from typing import Optional

import boto3
from boto3.dynamodb.conditions import Key

logger = logging.getLogger(__name__)


def _dec(v) -> Optional[Decimal]:
    try:
        return Decimal(str(v)) if v is not None else None
    except Exception:
        return None


class Screener:
    def __init__(self, table_prefix: str):
        ddb = boto3.resource("dynamodb")
        self.t_securities = ddb.Table(f"{table_prefix}-Securities")
        self.t_price = ddb.Table(f"{table_prefix}-PriceHistory")
        self.t_fundamentals = ddb.Table(f"{table_prefix}-Fundamentals")
        self.t_portfolio = ddb.Table(f"{table_prefix}-Portfolio")

    def get_all_tickers(self) -> list[str]:
        tickers = []
        scan_kwargs: dict = {"ProjectionExpression": "ticker"}
        while True:
            resp = self.t_securities.scan(**scan_kwargs)
            tickers += [item["ticker"] for item in resp.get("Items", [])]
            if "LastEvaluatedKey" not in resp:
                break
            scan_kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        return tickers

    def get_latest_price(self, ticker: str) -> Optional[dict]:
        resp = self.t_price.query(
            KeyConditionExpression=Key("ticker").eq(ticker),
            ScanIndexForward=False,
            Limit=1,
        )
        items = resp.get("Items", [])
        return items[0] if items else None

    def get_latest_fundamentals(self, ticker: str) -> Optional[dict]:
        resp = self.t_fundamentals.query(
            KeyConditionExpression=Key("ticker").eq(ticker),
            ScanIndexForward=False,
            Limit=1,
        )
        items = resp.get("Items", [])
        return items[0] if items else None

    def get_portfolio_tickers(self, user_id: str = "default") -> set[str]:
        resp = self.t_portfolio.query(
            KeyConditionExpression=Key("user_id").eq(user_id)
        )
        return {item["ticker"] for item in resp.get("Items", [])}

    def get_sector_map(self, classification: str = "17") -> dict[str, str]:
        """ticker → sector_name のマップを返す。"""
        field = "sector_17_name" if classification == "17" else "sector_33_name"
        sector_map = {}
        scan_kwargs: dict = {"ProjectionExpression": "ticker, sector_17_name, sector_33_name"}
        while True:
            resp = self.t_securities.scan(**scan_kwargs)
            for item in resp.get("Items", []):
                ticker = item.get("ticker", "")
                sector_map[ticker] = item.get(field, "その他")
            if "LastEvaluatedKey" not in resp:
                break
            scan_kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        return sector_map

    def screen(self, cfg, tickers: list[str]) -> list[dict]:
        """スクリーニング条件を満たす銘柄を返す。"""
        s = cfg.screening
        portfolio_tickers = self.get_portfolio_tickers() if s.exclude_existing_tickers else set()
        passed = []

        for ticker in tickers:
            if s.exclude_existing_tickers and ticker in portfolio_tickers:
                continue

            price_item = self.get_latest_price(ticker)
            if not price_item:
                continue

            close = _dec(price_item.get("close"))
            volume = _dec(price_item.get("volume"))
            if close is None or volume is None:
                continue

            # 単元購入額チェック（100株）
            unit_price = close * 100
            if unit_price > s.max_unit_price_jpy:
                continue

            # 出来高チェック
            if volume < s.min_daily_volume:
                continue

            fund = self.get_latest_fundamentals(ticker)
            if not fund:
                # Fundamentals未取得の銘柄はスキップ
                continue

            equity_ratio = _dec(fund.get("equity_ratio"))
            div_forecast = _dec(fund.get("div_forecast_ann"))
            eps = _dec(fund.get("eps"))

            # 自己資本比率チェック
            if equity_ratio is None or equity_ratio < Decimal(str(s.min_equity_ratio)):
                continue

            # 配当利回りチェック（予想配当 / 終値）
            if div_forecast is None or close == 0:
                continue
            div_yield = div_forecast / close
            if div_yield < Decimal(str(s.min_dividend_yield)):
                continue

            # PERチェック
            if eps is not None and eps > 0:
                per = close / eps
                if per > Decimal(str(s.max_per)):
                    continue

            passed.append({
                "ticker": ticker,
                "close": close,
                "volume": volume,
                "unit_price": unit_price,
                "equity_ratio": equity_ratio,
                "div_yield": div_yield,
                "div_forecast_ann": div_forecast,
                "eps": eps,
                "bps": _dec(fund.get("bps")),
                "equity": _dec(fund.get("equity")),
                "cfo": _dec(fund.get("cfo")),
                "disc_date": fund.get("disc_date", ""),
                "sector": "",  # handler側でsector_mapから付与
            })

        logger.info(f"スクリーニング: {len(tickers)}銘柄 → {len(passed)}銘柄通過")
        return passed
