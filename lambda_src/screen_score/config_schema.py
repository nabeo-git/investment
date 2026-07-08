from pydantic import BaseModel, model_validator


class ScreeningConfig(BaseModel):
    # バフェット定量フィルタ
    min_roe: float = 0.15
    min_roe_years: int = 3           # 5年中何年ROE基準を満たすか
    min_eps_cagr: float = 0.0        # EPS CAGR下限（連続赤字なし）
    min_cfo_quality: float = 0.60    # CFO/純利益 5年平均 下限
    max_debt_to_earnings: float = 5.0  # (総資産-純資産)/純利益 上限
    max_margin_decline: float = -0.03  # 営業利益率 年間変化率の下限
    min_history_years: int = 3         # 必要最低限のFYデータ年数
    # 基本フィルタ
    max_unit_price_jpy: int = 100000
    min_daily_volume: int = 1000
    exclude_existing_tickers: bool = True


class ScoringWeights(BaseModel):
    roe_stability: float = 0.40
    growth_quality: float = 0.35
    financial_solidity: float = 0.25

    @model_validator(mode="after")
    def check_sum(self):
        total = self.roe_stability + self.growth_quality + self.financial_solidity
        if abs(total - 1.0) > 0.01:
            raise ValueError(f"scoring.weights の合計が 1.0 になっていません（合計={total}）")
        return self


class ScoringConfig(BaseModel):
    weights: ScoringWeights = ScoringWeights()
    qualitative_weight: float = 0.60
    quantitative_weight: float = 0.40


class ValuationConfig(BaseModel):
    margin_of_safety_threshold: float = 0.25  # 25%以上で買い候補
    conservative_per_cap: float = 20.0         # 許容PER上限（保守的）


class CandidatesConfig(BaseModel):
    top_n: int = 10
    monthly_budget_jpy: int = 100000
    sector_mode: bool = True
    sector_classification: str = "17"
    per_sector: int = 2


class JQuantsConfig(BaseModel):
    enabled: bool = True
    plan: str = "light"


class DataSourcesConfig(BaseModel):
    jquants: JQuantsConfig = JQuantsConfig()


class Config(BaseModel):
    version: str = "1.0"
    screening: ScreeningConfig = ScreeningConfig()
    scoring: ScoringConfig = ScoringConfig()
    valuation: ValuationConfig = ValuationConfig()
    candidates: CandidatesConfig = CandidatesConfig()
    data_sources: DataSourcesConfig = DataSourcesConfig()
