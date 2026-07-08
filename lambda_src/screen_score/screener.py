import logging
from typing import Optional

import boto3
from boto3.dynamodb.conditions import Attr, Key

logger = logging.getLogger(__name__)


def _to_float(v) -> Optional[float]:
    try:
        f = float(v)
        return f if f == f else None  # NaN check
    except (TypeError, ValueError):
        return None


def _linear_slope(xs: list[float], ys: list[float]) -> float:
    """最小二乗法で傾きを返す。データが2点未満なら0.0。"""
    n = len(xs)
    if n < 2:
        return 0.0
    sx = sum(xs)
    sy = sum(ys)
    sxy = sum(x * y for x, y in zip(xs, ys))
    sx2 = sum(x * x for x in xs)
    denom = n * sx2 - sx * sx
    if denom == 0:
        return 0.0
    return (n * sxy - sx * sy) / denom


def _cagr(start: float, end: float, years: float) -> Optional[float]:
    """複合年間成長率。負値・ゼロは計算不能としてNoneを返す。"""
    if start <= 0 or end <= 0 or years <= 0:
        return None
    return (end / start) ** (1.0 / years) - 1.0


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

    def get_annual_fundamentals(self, ticker: str, years: int = 5) -> list[dict]:
        """FY期の財務データを最新年度順に最大 years 件返す。"""
        resp = self.t_fundamentals.query(
            KeyConditionExpression=Key("ticker").eq(ticker),
            FilterExpression=Attr("period_type").eq("FY"),
            ScanIndexForward=False,
        )
        return resp.get("Items", [])[:years]

    def get_portfolio_tickers(self, user_id: str = "default") -> set[str]:
        resp = self.t_portfolio.query(
            KeyConditionExpression=Key("user_id").eq(user_id)
        )
        return {item["ticker"] for item in resp.get("Items", [])}

    def get_sector_map(self, classification: str = "17") -> dict[str, str]:
        field = "sector_17_name" if classification == "17" else "sector_33_name"
        sector_map = {}
        scan_kwargs: dict = {"ProjectionExpression": "ticker, sector_17_name, sector_33_name"}
        while True:
            resp = self.t_securities.scan(**scan_kwargs)
            for item in resp.get("Items", []):
                sector_map[item.get("ticker", "")] = item.get(field, "その他")
            if "LastEvaluatedKey" not in resp:
                break
            scan_kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        return sector_map

    # ── バフェット定量フィルタ計算関数群 ─────────────────────

    def _roe_years_passing(self, records: list[dict], min_roe: float) -> int:
        """ROE >= min_roe を満たす年数を返す。"""
        count = 0
        for r in records:
            np_ = _to_float(r.get("net_profit"))
            eq = _to_float(r.get("equity"))
            if np_ is not None and eq is not None and eq > 0 and np_ / eq >= min_roe:
                count += 1
        return count

    def _eps_cagr_ok(self, records: list[dict], min_cagr: float) -> tuple[bool, Optional[float]]:
        """
        EPSが連続赤字なし & CAGR >= min_cagr かをチェック。
        records は新しい順（records[0] が最新）。
        (合格か否か, CAGR値) を返す。
        """
        eps_vals = [_to_float(r.get("eps")) for r in records]
        eps_vals = [e for e in eps_vals if e is not None]
        if len(eps_vals) < 2:
            return False, None
        if any(e < 0 for e in eps_vals):
            return False, None
        oldest, newest = eps_vals[-1], eps_vals[0]
        cagr = _cagr(oldest, newest, float(len(eps_vals) - 1))
        return (cagr is not None and cagr >= min_cagr), cagr

    def _cfo_quality(self, records: list[dict]) -> Optional[float]:
        """CFO / 純利益 の平均（両者が正の年のみ使用）。"""
        ratios = []
        for r in records:
            cfo = _to_float(r.get("cfo"))
            np_ = _to_float(r.get("net_profit"))
            if cfo is not None and np_ is not None and np_ > 0:
                ratios.append(cfo / np_)
        return sum(ratios) / len(ratios) if ratios else None

    def _debt_multiple(self, records: list[dict]) -> Optional[float]:
        """(総資産 - 純資産) / 純利益 の平均（純利益 > 0 の年のみ）。"""
        multiples = []
        for r in records:
            ta = _to_float(r.get("total_assets"))
            eq = _to_float(r.get("equity"))
            np_ = _to_float(r.get("net_profit"))
            if ta is not None and eq is not None and np_ is not None and np_ > 0:
                multiples.append((ta - eq) / np_)
        return sum(multiples) / len(multiples) if multiples else None

    def _margin_slope(self, records: list[dict]) -> Optional[float]:
        """営業利益率の線形回帰傾き（年次）。正=改善、負=悪化。"""
        margins = []
        for r in records:
            op = _to_float(r.get("operating_profit"))
            sales = _to_float(r.get("sales"))
            if op is not None and sales is not None and sales > 0:
                margins.append(op / sales)
        if len(margins) < 2:
            return None
        # records は新しい順なので古い=0, 新しい=n-1 に逆転
        xs = list(range(len(margins) - 1, -1, -1))
        return _linear_slope(xs, margins)

    def _intrinsic_value(
        self,
        close: float,
        latest_eps: Optional[float],
        eps_cagr: Optional[float],
        per_cap: float,
    ) -> Optional[float]:
        """
        簡易内在価値 = EPS × (1 + CAGR)^5 × 許容PER
        許容PER = min(現在PER × 0.8, per_cap)  ← 保守的
        """
        if latest_eps is None or latest_eps <= 0 or eps_cagr is None or close <= 0:
            return None
        current_per = close / latest_eps
        allowed_per = min(current_per * 0.8, per_cap)
        if allowed_per <= 0:
            return None
        growth_factor = (1 + eps_cagr) ** 5
        return latest_eps * growth_factor * allowed_per

    # ── スクリーニング本体 ─────────────────────────────────

    def screen(self, cfg, tickers: list[str]) -> list[dict]:
        s = cfg.screening
        v = cfg.valuation
        portfolio_tickers = self.get_portfolio_tickers() if s.exclude_existing_tickers else set()
        passed = []
        cnt_price = cnt_fund = cnt_buffett = 0

        for ticker in tickers:
            if s.exclude_existing_tickers and ticker in portfolio_tickers:
                continue

            # ── ステップ1: 株価・出来高フィルタ（低コスト）──
            price_item = self.get_latest_price(ticker)
            if not price_item:
                cnt_price += 1
                continue
            close = _to_float(price_item.get("close"))
            volume = _to_float(price_item.get("volume"))
            if close is None or volume is None or close <= 0:
                cnt_price += 1
                continue
            if close * 100 > s.max_unit_price_jpy:
                cnt_price += 1
                continue
            if volume < s.min_daily_volume:
                cnt_price += 1
                continue

            # ── ステップ2: 5年分FY財務データ取得 ──
            records = self.get_annual_fundamentals(ticker)
            if len(records) < s.min_history_years:
                cnt_fund += 1
                continue

            # ── ステップ3: バフェット定量フィルタ ──

            # ROE（株主資本利益率）
            roe_years = self._roe_years_passing(records, s.min_roe)
            if roe_years < s.min_roe_years:
                cnt_buffett += 1
                continue

            # EPS成長（連続赤字なし & CAGR >= 0%）
            eps_ok, eps_cagr = self._eps_cagr_ok(records, s.min_eps_cagr)
            if not eps_ok:
                cnt_buffett += 1
                continue

            # CFO品質（利益の現金化能力）
            cfo_q = self._cfo_quality(records)
            if cfo_q is None or cfo_q < s.min_cfo_quality:
                cnt_buffett += 1
                continue

            # 負債倍率
            debt_mul = self._debt_multiple(records)
            if debt_mul is None or debt_mul > s.max_debt_to_earnings:
                cnt_buffett += 1
                continue

            # 営業利益率トレンド（有意な悪化をチェック）
            slope = self._margin_slope(records)
            if slope is not None and slope < s.max_margin_decline:
                cnt_buffett += 1
                continue

            # ── バリュエーション計算 ──
            latest_fund = records[0]
            latest_eps = _to_float(latest_fund.get("eps"))
            intrinsic = self._intrinsic_value(close, latest_eps, eps_cagr, v.conservative_per_cap)
            if intrinsic is not None and intrinsic > 0:
                mos = (intrinsic - close) / intrinsic
            else:
                mos = None

            # 著しく割高（内在価値の50%超）は除外
            if mos is not None and mos < -0.5:
                cnt_buffett += 1
                continue

            if mos is None:
                val_status = "unknown"
            elif mos >= v.margin_of_safety_threshold:
                val_status = "buy"
            elif mos >= 0:
                val_status = "watch"
            else:
                val_status = "expensive"

            passed.append({
                "ticker": ticker,
                "close": close,
                "volume": volume,
                "unit_price": close * 100,
                "eps": latest_eps,
                "eps_cagr": eps_cagr,
                "cfo_quality": cfo_q,
                "debt_multiple": debt_mul,
                "margin_slope": slope,
                "roe_years": roe_years,
                "intrinsic_value": intrinsic,
                "margin_of_safety": mos,
                "valuation_status": val_status,
                "equity_ratio": _to_float(latest_fund.get("equity_ratio")),
                "equity": _to_float(latest_fund.get("equity")),
                "total_assets": _to_float(latest_fund.get("total_assets")),
                "sales": _to_float(latest_fund.get("sales")),
                "operating_profit": _to_float(latest_fund.get("operating_profit")),
                "net_profit": _to_float(latest_fund.get("net_profit")),
                "cfo": _to_float(latest_fund.get("cfo")),
                "bps": _to_float(latest_fund.get("bps")),
                "disc_date": latest_fund.get("disc_date", ""),
                "fy_end": latest_fund.get("fy_end", ""),
                "sector": "",
                "annual_records": records,  # scorer 用（DynamoDB保存時は除外）
            })

        logger.info(
            f"スクリーニング: {len(tickers)}銘柄 → 通過{len(passed)}銘柄 "
            f"（株価除外:{cnt_price} / 財務不足:{cnt_fund} / バフェット基準除外:{cnt_buffett}）"
        )
        return passed
