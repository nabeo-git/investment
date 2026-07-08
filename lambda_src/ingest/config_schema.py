from pydantic import BaseModel, model_validator


class ScreeningConfig(BaseModel):
    max_unit_price_jpy: int = 100000
    min_dividend_yield: float = 0.03
    min_equity_ratio: float = 0.40
    max_per: float = 20.0
    min_daily_volume: int = 1000
    exclude_existing_tickers: bool = True


class ScoringWeights(BaseModel):
    dividend_continuity: float = 0.20
    valuation: float = 0.30
    financial_health: float = 0.50

    @model_validator(mode="after")
    def check_sum(self):
        total = self.dividend_continuity + self.valuation + self.financial_health
        if abs(total - 1.0) > 0.01:
            raise ValueError(f"scoring.weights の合計が 1.0 になっていません（合計={total}）")
        return self


class ScoringConfig(BaseModel):
    weights: ScoringWeights = ScoringWeights()


class CandidatesConfig(BaseModel):
    top_n: int = 10
    monthly_budget_jpy: int = 100000


class JQuantsConfig(BaseModel):
    enabled: bool = True
    plan: str = "light"


class DataSourcesConfig(BaseModel):
    jquants: JQuantsConfig = JQuantsConfig()


class Config(BaseModel):
    version: str = "1.0"
    screening: ScreeningConfig = ScreeningConfig()
    scoring: ScoringConfig = ScoringConfig()
    candidates: CandidatesConfig = CandidatesConfig()
    data_sources: DataSourcesConfig = DataSourcesConfig()
