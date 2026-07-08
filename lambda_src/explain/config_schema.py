from pydantic import BaseModel, model_validator


class ScreeningConfig(BaseModel):
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


class CandidatesConfig(BaseModel):
    top_n: int = 10
    monthly_budget_jpy: int = 100000


class ExplanationConfig(BaseModel):
    bedrock_model_id: str = "amazon.nova-pro-v1:0"
    bedrock_region: str = "us-east-1"
    max_tokens: int = 4000
    temperature: float = 0.2


class QualitativeConfig(BaseModel):
    edinet_enabled: bool = True
    ir_scraping_enabled: bool = True
    dimensions: list = ["moat", "circle", "sincerity", "capital_alloc", "enthusiasm"]


class Config(BaseModel):
    version: str = "1.0"
    screening: ScreeningConfig = ScreeningConfig()
    scoring: ScoringConfig = ScoringConfig()
    candidates: CandidatesConfig = CandidatesConfig()
    explanation: ExplanationConfig = ExplanationConfig()
    qualitative: QualitativeConfig = QualitativeConfig()
