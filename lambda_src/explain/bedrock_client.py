import json
import logging
import re
from typing import Optional

import boto3

logger = logging.getLogger(__name__)

# ── 説明生成プロンプト ─────────────────────────────────

EXPLANATION_PROMPT = """あなたは長期投資専門家です。
以下のデータに基づき、この銘柄が投資候補として選定された理由を日本語で説明してください。

## 銘柄情報
- 銘柄コード: {ticker}
- 銘柄名: {name}
- 業種: {sector}

## 定量スコア
- 総合スコア: {score_total}（定量:{score_quantitative} / ROE安定性:{score_roe} / 成長の質:{score_growth} / 財務盤石さ:{score_fin}）
- ROE基準通過年数: {roe_years}/5年
- EPS CAGR: {eps_cagr_pct}%
- CFO品質（CFO/純利益）: {cfo_quality}
- 負債倍率: {debt_multiple}倍

## バリュエーション
- 終値: {close}円
- 内在価値（簡易DCF）: {intrinsic_value}円
- 安全域: {margin_of_safety_pct}%（{valuation_status}）

## 定性評価（LLMスコア）
- 経済的なお堀: {score_moat}/10
- 能力の輪: {score_circle}/10
- 経営者の誠実さ: {score_sincerity}/10
- 資本配分: {score_capital_alloc}/10
- 成長への熱意: {score_enthusiasm}/10
- 定性評価サマリー: {qual_summary}

## 出力形式
以下の構成で400〜600字でまとめてください。
1. **なぜこの会社か**（定量・定性の両面から選定理由）
2. **競争優位性**（お堀・ビジネスの強さ）
3. **経営者評価**（誠実さ・資本配分）
4. **バリュエーション**（内在価値との比較）
5. **留意事項**（リスク・弱点）

※ 将来のリターンを保証する記述は避けてください。
"""

# ── 定性スコアリングプロンプト ─────────────────────────

QUALITATIVE_PROMPT = """あなたは長期投資の専門家です。
以下の企業情報テキストを分析し、5次元を各0〜10点で採点してJSON形式のみで返してください。

## 企業情報
- 銘柄コード: {ticker}
- 銘柄名: {name}
- 業種: {sector}

## 分析テキスト（有価証券報告書・IR情報）

### 事業概要
{business_text}

### 経営方針・社長メッセージ
{management_text}

### リスク要因
{risk_text}

## 採点基準
1. **moat（経済的なお堀）**: ブランド力・スイッチングコスト・価格支配力・参入障壁（10=非常に強い）
2. **circle（能力の輪）**: ビジネスモデルの明確さ・10年後も理解可能か（10=単純明快）
3. **sincerity（経営者の誠実さ）**: 失敗・リスクへの正直な言及・一貫性・透明性（10=非常に誠実）
4. **capital_alloc（資本配分）**: 株主目線の資本使用・再投資合理性・無駄な拡大回避（10=最良）
5. **enthusiasm（成長への熱意）**: 顧客・製品・従業員への語り・数字以外のビジョン（10=非常に高い）

## 出力形式（JSONのみ、説明文は不要）
{{
  "moat": 7,
  "circle": 8,
  "sincerity": 6,
  "capital_alloc": 7,
  "enthusiasm": 8,
  "summary": "このビジネスの特徴と評価根拠を50字以内で"
}}
"""


class BedrockClient:
    def __init__(self, model_id: str, region: str, max_tokens: int, temperature: float):
        self.model_id = model_id
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.client = boto3.client("bedrock-runtime", region_name=region)

    def _invoke(self, prompt: str, max_tokens: Optional[int] = None) -> str:
        payload = {
            "messages": [{"role": "user", "content": [{"text": prompt}]}],
            "inferenceConfig": {
                "maxTokens": max_tokens or self.max_tokens,
                "temperature": self.temperature,
            },
        }
        resp = self.client.invoke_model(
            modelId=self.model_id,
            body=json.dumps(payload),
            contentType="application/json",
            accept="application/json",
        )
        body = json.loads(resp["body"].read())
        return body["output"]["message"]["content"][0]["text"]

    def generate_explanation(self, context: dict) -> str:
        prompt = EXPLANATION_PROMPT.format(**context)
        try:
            return self._invoke(prompt)
        except Exception as e:
            logger.error(f"説明生成失敗 ticker={context.get('ticker')}: {e}")
            return f"（説明生成に失敗しました: {e}）"

    def score_qualitative(self, ticker: str, name: str, sector: str,
                           edinet_info: dict, ir_text: Optional[str]) -> dict:
        """
        有報テキスト・IRテキストをもとに定性5次元をBedrockで採点する。
        失敗時はデフォルト値（各5点）を返す。
        """
        business = edinet_info.get("business", "情報なし")[:800]
        management = (
            edinet_info.get("management", "")
            + ("\n\n" + ir_text[:600] if ir_text else "")
        )[:1000]
        risks = edinet_info.get("risks", "情報なし")[:600]

        if not any([business != "情報なし", management.strip(), risks != "情報なし"]):
            logger.info(f"定性スコア: {ticker} テキスト情報なし → デフォルト5点")
            return self._default_scores()

        prompt = QUALITATIVE_PROMPT.format(
            ticker=ticker,
            name=name,
            sector=sector,
            business_text=business or "情報なし",
            management_text=management or "情報なし",
            risk_text=risks,
        )
        try:
            raw = self._invoke(prompt, max_tokens=500)
            return self._parse_qualitative(raw)
        except Exception as e:
            logger.warning(f"定性スコアリング失敗 ticker={ticker}: {e}")
            return self._default_scores()

    def _parse_qualitative(self, raw: str) -> dict:
        """Bedrock の JSON レスポンスをパースする。"""
        json_match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not json_match:
            return self._default_scores()
        try:
            data = json.loads(json_match.group())
            keys = ["moat", "circle", "sincerity", "capital_alloc", "enthusiasm"]
            scores = {k: max(0, min(10, int(data.get(k, 5)))) for k in keys}
            scores["summary"] = str(data.get("summary", ""))[:100]
            scores["total"] = round(sum(scores[k] for k in keys) / len(keys), 2)
            return scores
        except Exception:
            return self._default_scores()

    def _default_scores(self) -> dict:
        return {
            "moat": 0, "circle": 0, "sincerity": 0,
            "capital_alloc": 0, "enthusiasm": 0,
            "total": 0.0, "summary": "テキスト情報なし",
        }
