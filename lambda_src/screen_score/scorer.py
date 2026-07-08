import logging
from typing import Optional

logger = logging.getLogger(__name__)


def _safe_float(v, default: float = 0.0) -> float:
    try:
        f = float(v)
        return f if f == f else default
    except (TypeError, ValueError):
        return default


def _normalize(values: list[float]) -> list[float]:
    """min-max 正規化して 0〜1 に変換。全て同値なら 0.5 を返す。"""
    if not values:
        return []
    mn = min(values)
    mx = max(values)
    if mx == mn:
        return [0.5] * len(values)
    return [(v - mn) / (mx - mn) for v in values]


class Scorer:
    def score(self, candidates: list[dict], weights, valuation_cfg) -> list[dict]:
        """
        3軸スコア（ROE安定性・成長の質・財務盤石さ）を min-max 正規化後に加重平均。
        バリュエーションステータスを付与し、score_quantitative を計算して返す。
        """
        if not candidates:
            return []

        w_roe = _safe_float(weights.roe_stability)
        w_growth = _safe_float(weights.growth_quality)
        w_fin = _safe_float(weights.financial_solidity)

        # ── ROE 安定性スコア: 5年ROE平均 / (1 + 標準偏差) ──
        roe_scores = []
        for c in candidates:
            roe_vals = []
            for r in c.get("annual_records", []):
                np_ = _safe_float(r.get("net_profit"))
                eq = _safe_float(r.get("equity"))
                if eq > 0:
                    roe_vals.append(np_ / eq)
            if not roe_vals:
                roe_scores.append(0.0)
            elif len(roe_vals) == 1:
                roe_scores.append(max(0.0, roe_vals[0]))
            else:
                avg = sum(roe_vals) / len(roe_vals)
                std = (sum((r - avg) ** 2 for r in roe_vals) / len(roe_vals)) ** 0.5
                roe_scores.append(max(0.0, avg / (1 + std)))

        # ── 成長の質スコア: EPS CAGR × CFO品質 ──
        growth_scores = []
        for c in candidates:
            eps_cagr = _safe_float(c.get("eps_cagr"), 0.0)
            cfo_q = _safe_float(c.get("cfo_quality"), 0.0)
            growth_scores.append(max(0.0, eps_cagr * cfo_q))

        # ── 財務盤石さスコア: 負債倍率逆数 × 自己資本比率 ──
        solidity_scores = []
        for c in candidates:
            debt_mul = _safe_float(c.get("debt_multiple"), 10.0)
            eq_ratio = _safe_float(c.get("equity_ratio"), 0.0)
            inv_debt = 1.0 / max(debt_mul, 0.1)
            solidity_scores.append(inv_debt * eq_ratio)

        norm_roe = _normalize(roe_scores)
        norm_growth = _normalize(growth_scores)
        norm_fin = _normalize(solidity_scores)

        scored = []
        for i, c in enumerate(candidates):
            quant = (
                w_roe * norm_roe[i]
                + w_growth * norm_growth[i]
                + w_fin * norm_fin[i]
            )
            scored.append({
                **c,
                "score_roe_stability": round(norm_roe[i], 4),
                "score_growth_quality": round(norm_growth[i], 4),
                "score_financial_solidity": round(norm_fin[i], 4),
                "score_quantitative": round(quant, 4),
                "score_total": round(quant, 4),  # 定性スコア追加前の暫定値
            })

        scored.sort(key=lambda x: x["score_total"], reverse=True)
        if scored:
            top = scored[0]
            logger.info(
                f"スコアリング: {len(scored)}銘柄 "
                f"top={top['ticker']} quant={top['score_quantitative']:.4f} "
                f"val={top.get('valuation_status','?')}"
            )
        return scored
