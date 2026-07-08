import logging
from decimal import Decimal

logger = logging.getLogger(__name__)

_ZERO = Decimal("0")
_ONE = Decimal("1")


def _safe_div(a, b) -> Decimal:
    try:
        if b is None or b == _ZERO:
            return _ZERO
        return Decimal(str(a)) / Decimal(str(b))
    except Exception:
        return _ZERO


def _normalize(values: list[Decimal]) -> list[Decimal]:
    """min-max正規化して0〜1に変換。全て同値なら0.5を返す。"""
    if not values:
        return []
    mn = min(values)
    mx = max(values)
    if mx == mn:
        return [Decimal("0.5")] * len(values)
    return [(v - mn) / (mx - mn) for v in values]


class Scorer:
    def score(self, candidates: list[dict], weights) -> list[dict]:
        """
        各銘柄のスコアを計算し score_total でソートして返す。
        3軸スコア（dividend_continuity / valuation / financial_health）を
        min-max正規化後に加重平均。
        """
        if not candidates:
            return []

        w_div = Decimal(str(weights.dividend_continuity))
        w_val = Decimal(str(weights.valuation))
        w_fin = Decimal(str(weights.financial_health))

        # --- 配当継続性スコア（配当利回り）---
        div_yields = [c.get("div_yield") or _ZERO for c in candidates]
        norm_div = _normalize(div_yields)

        # --- 割安度スコア（PBR逆数：低PBR=割安）---
        pbr_values = []
        for c in candidates:
            close = c.get("close") or _ZERO
            bps = c.get("bps") or _ZERO
            pbr_values.append(_safe_div(close, bps) if bps > _ZERO else Decimal("999"))
        # PBRは低い方が良いので逆数で正規化
        inv_pbr = [_safe_div(_ONE, v) if v > _ZERO else _ZERO for v in pbr_values]
        norm_val = _normalize(inv_pbr)

        # --- 財務健全性スコア（自己資本比率）---
        eq_ratios = [c.get("equity_ratio") or _ZERO for c in candidates]
        norm_fin = _normalize(eq_ratios)

        scored = []
        for i, c in enumerate(candidates):
            score_total = (
                w_div * norm_div[i]
                + w_val * norm_val[i]
                + w_fin * norm_fin[i]
            )
            scored.append({
                **c,
                "score_dividend_continuity": float(round(norm_div[i], 4)),
                "score_valuation": float(round(norm_val[i], 4)),
                "score_financial_health": float(round(norm_fin[i], 4)),
                "score_total": float(round(score_total, 4)),
            })

        scored.sort(key=lambda x: x["score_total"], reverse=True)
        logger.info(f"スコアリング: {len(scored)}銘柄 top={scored[0]['ticker']} score={scored[0]['score_total']:.4f}")
        return scored
