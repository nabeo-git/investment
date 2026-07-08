import json
import logging

import boto3

logger = logging.getLogger(__name__)

PROMPT_TEMPLATE = """あなたは個人投資家向けの投資アドバイザーです。
以下の銘柄データを分析し、投資候補として選定された理由を日本語で説明してください。

## 銘柄情報
- 銘柄コード: {ticker}
- 銘柄名: {name}
- 業種: {sector}

## 直近株価・スコア
- 終値: {close}円
- 単元購入額（100株）: {unit_price}円
- 配当利回り（予想）: {div_yield_pct}%
- 総合スコア: {score_total}（配当継続性:{score_div} / 割安度:{score_val} / 財務健全性:{score_fin}）

## 財務指標
- 自己資本比率: {equity_ratio_pct}%
- EPS（1株利益）: {eps}円
- BPS（1株純資産）: {bps}円
- 営業CF: {cfo}百万円

## 出力形式
以下の構成で300〜500字でまとめてください。
1. **選定理由**（スコアが高い軸を中心に）
2. **注目ポイント**（業種・財務特性など）
3. **留意事項**（リスク・注意点）

※ 将来の株価・リターンを保証する記述は避けてください。
"""


class BedrockClient:
    def __init__(self, model_id: str, region: str, max_tokens: int, temperature: float):
        self.model_id = model_id
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.client = boto3.client("bedrock-runtime", region_name=region)

    def generate_explanation(self, context: dict) -> str:
        prompt = PROMPT_TEMPLATE.format(**context)
        payload = {
            "messages": [{"role": "user", "content": [{"text": prompt}]}],
            "inferenceConfig": {
                "maxTokens": self.max_tokens,
                "temperature": self.temperature,
            },
        }
        try:
            resp = self.client.invoke_model(
                modelId=self.model_id,
                body=json.dumps(payload),
                contentType="application/json",
                accept="application/json",
            )
            body = json.loads(resp["body"].read())
            return body["output"]["message"]["content"][0]["text"]
        except Exception as e:
            logger.error(f"Bedrock呼出失敗 ticker={context.get('ticker')}: {e}")
            return f"（説明生成に失敗しました: {e}）"
