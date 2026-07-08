import logging
import time
from decimal import Decimal, InvalidOperation
from typing import Optional

import jquantsapi

logger = logging.getLogger(__name__)

TTL_5_YEARS = int(time.time()) + 5 * 365 * 24 * 3600


def _dec(value) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        d = Decimal(str(value))
        return None if d.is_nan() else d
    except (InvalidOperation, ValueError):
        return None


class JQuantsFetcher:
    def __init__(self, api_key: str):
        self.client = jquantsapi.ClientV2(api_key=api_key)

    def get_securities(self) -> list[dict]:
        df = self.client.get_list()
        records = []
        for _, row in df.iterrows():
            records.append({
                "ticker": str(row.get("Code", "")),
                "asset_class": "jp_stock",
                "name_ja": str(row.get("CompanyName", row.get("CoName", ""))),
                "name_en": str(row.get("CompanyNameEnglish", row.get("CoNameEn", ""))),
                "sector_17_code": str(row.get("Sector17Code", row.get("S17", ""))),
                "sector_17_name": str(row.get("Sector17CodeName", row.get("S17Nm", ""))),
                "sector_33_code": str(row.get("Sector33Code", row.get("S33", ""))),
                "sector_33_name": str(row.get("Sector33CodeName", row.get("S33Nm", ""))),
                "scale_category": str(row.get("ScaleCategory", row.get("ScaleCat", ""))),
                "market_code": str(row.get("MarketCode", row.get("Mkt", ""))),
                "market_name": str(row.get("MarketCodeName", row.get("MktNm", ""))),
                "currency": "JPY",
                "unit_size": 100,
                "updated_at": str(row.get("Date", "")),
            })
        return records

    def get_prices_for_date(self, date_yyyymmdd: str) -> list[dict]:
        try:
            df = self.client.get_eq_bars_daily(date_yyyymmdd=date_yyyymmdd)
        except Exception as e:
            logger.warning(f"株価取得失敗 date={date_yyyymmdd}: {e}")
            return []

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
                "ttl": TTL_5_YEARS,
                "missing_flag": False,
            })
        return records

    def get_fundamentals_for_date(self, date_yyyymmdd: str) -> list[dict]:
        """指定日に開示されたFundamentalsを全銘柄分取得（インクリメンタル用）。"""
        date_fmt = f"{date_yyyymmdd[:4]}-{date_yyyymmdd[4:6]}-{date_yyyymmdd[6:]}"
        try:
            result = self.client.get_fin_summary_cursor(date=date_fmt)
            df = result[0] if isinstance(result, tuple) else result
        except Exception as e:
            logger.warning(f"Fundamentals取得失敗 date={date_fmt}: {e}")
            return []
        return self._parse_fins_df(df)

    def get_fundamentals_for_ticker(self, code: str) -> list[dict]:
        """銘柄単位でFundamentalsを取得（初期投入バッチ用）。"""
        try:
            result = self.client.get_fin_summary_cursor(code=code)
            df = result[0] if isinstance(result, tuple) else result
        except Exception as e:
            logger.warning(f"Fundamentals取得失敗 code={code}: {e}")
            return []
        return self._parse_fins_df(df)

    def _parse_fins_df(self, df) -> list[dict]:
        records = []
        for _, row in df.iterrows():
            records.append({
                "ticker": str(row.get("LocalCode", row.get("Code", ""))),
                "disc_date": str(row.get("DisclosureDate", row.get("DiscDate", ""))),
                "doc_type": str(row.get("TypeOfDocument", row.get("DocType", ""))),
                "period_type": str(row.get("TypeOfCurrentPeriod", row.get("CurPerType", ""))),
                "period_start": str(row.get("CurrentPeriodStartDate", row.get("CurPerSt", ""))),
                "period_end": str(row.get("CurrentPeriodEndDate", row.get("CurPerEn", ""))),
                "fy_start": str(row.get("CurrentFiscalYearStartDate", row.get("CurFYSt", ""))),
                "fy_end": str(row.get("CurrentFiscalYearEndDate", row.get("CurFYEn", ""))),
                "sales": _dec(row.get("NetSales", row.get("Sales"))),
                "operating_profit": _dec(row.get("OperatingProfit", row.get("OP"))),
                "net_profit": _dec(row.get("NetIncome", row.get("NP"))),
                "eps": _dec(row.get("EarningsPerShare", row.get("EPS"))),
                "bps": _dec(row.get("BookValuePerShare", row.get("BPS"))),
                "total_assets": _dec(row.get("TotalAssets", row.get("TA"))),
                "equity": _dec(row.get("Equity", row.get("Eq"))),
                "equity_ratio": _dec(row.get("EquityToAssetRatio", row.get("EqAR"))),
                "cfo": _dec(row.get("CashFlowsFromOperatingActivities", row.get("CFO"))),
                "div_fy_actual": _dec(row.get("AnnualDividendPerShare", row.get("DivFY"))),
                "div_forecast_ann": _dec(row.get("ForecastAnnualDividendPerShare", row.get("FDivAnn"))),
                "payout_ratio_forecast": _dec(row.get("ForecastPayoutRatioAnnual", row.get("FPayoutRatioAnn"))),
                "sales_forecast": _dec(row.get("ForecastNetSales", row.get("FSales"))),
                "np_forecast": _dec(row.get("ForecastNetIncome", row.get("FNP"))),
                "eps_forecast": _dec(row.get("ForecastEarningsPerShare", row.get("FEPS"))),
            })
        return records
