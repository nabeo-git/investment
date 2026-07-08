import json
import logging
import math
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeSerializer

from bedrock_client import BedrockClient
from config_loader import load_config
from edinet_client import EdinetClient
from ir_scraper import IRScraper

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ENVIRONMENT = os.environ["ENVIRONMENT"]
CONFIG_BUCKET = os.environ["CONFIG_BUCKET"]
REPORTS_BUCKET = os.environ["REPORTS_BUCKET"]
REGION = os.environ.get("AWS_REGION_MAIN", "ap-northeast-1")
TABLE_PREFIX = f"investment-{ENVIRONMENT}"

_serializer = TypeSerializer()


def _serialize(item: dict) -> dict:
    cleaned = {}
    for k, v in item.items():
        if v is None:
            continue
        if isinstance(v, float):
            if math.isnan(v) or math.isinf(v):
                continue
            v = Decimal(str(v))
        cleaned[k] = v
    return {k: _serializer.serialize(v) for k, v in cleaned.items()}


def _safe_float(v, default: float = 0.0) -> float:
    try:
        f = float(v)
        return f if f == f else default
    except (TypeError, ValueError):
        return default


def _fmt_cfo(value) -> str:
    try:
        return f"{int(float(value)) // 1_000_000:,}"
    except Exception:
        return "-"


def _pct(v, decimals: int = 1) -> str:
    try:
        return f"{float(v) * 100:.{decimals}f}%"
    except Exception:
        return "-"


# ── Markdown → HTML ──────────────────────────────

def _md_to_html(text: str) -> str:
    lines = text.splitlines()
    out = []
    in_ul = False
    for line in lines:
        line = line.rstrip()
        if line.startswith("### "):
            if in_ul: out.append("</ul>"); in_ul = False
            out.append(f"<h4>{line[4:]}</h4>")
        elif line.startswith("## "):
            if in_ul: out.append("</ul>"); in_ul = False
            out.append(f"<h3>{line[3:]}</h3>")
        elif re.match(r"^[\-\*] ", line):
            if not in_ul: out.append("<ul>"); in_ul = True
            content = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", line[2:])
            out.append(f"<li>{content}</li>")
        elif re.match(r"^\d+\. ", line):
            if in_ul: out.append("</ul>"); in_ul = False
            content = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>",
                             re.sub(r"^\d+\. ", "", line))
            num = re.match(r"^\d+", line).group()
            out.append(f"<p><strong>{num}.</strong> {content}</p>")
        elif line == "":
            if in_ul: out.append("</ul>"); in_ul = False
            out.append("")
        else:
            if in_ul: out.append("</ul>"); in_ul = False
            content = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", line)
            out.append(f"<p>{content}</p>")
    if in_ul:
        out.append("</ul>")
    return "\n".join(out)


def _score_bar(label: str, value: float, color: str = "#0073bb",
               max_val: float = 1.0) -> str:
    pct = min(100, round(value / max_val * 100))
    return f"""
      <div class="score-row">
        <span class="score-label">{label}</span>
        <div class="score-track">
          <div class="score-fill" style="width:{pct}%;background:{color}"></div>
        </div>
        <span class="score-val">{value:.2f}</span>
      </div>"""


def _val_badge(status: str) -> str:
    color_map = {
        "buy": ("badge-buy", "買い候補"),
        "watch": ("badge-watch", "ウォッチ"),
        "expensive": ("badge-exp", "割高"),
        "unknown": ("badge-unk", "不明"),
    }
    cls, label = color_map.get(status, ("badge-unk", status))
    return f'<span class="val-badge {cls}">{label}</span>'


# ── HTML生成 ──────────────────────────────────────

def _build_html(run_date: str, candidates: list[dict],
                securities: dict, fundamentals: dict, explanations: dict) -> str:
    sectors: dict[str, list[dict]] = defaultdict(list)
    for c in sorted(candidates, key=lambda x: int(x.get("rank", 99))):
        sector = c.get("sector", "その他") or "その他"
        sectors[sector].append(c)

    # サマリーテーブル
    summary_rows = ""
    for c in sorted(candidates, key=lambda x: int(x.get("rank", 99))):
        ticker = c["ticker"]
        sec = securities.get(ticker, {})
        name = sec.get("name_ja", ticker)
        score = _safe_float(c.get("score_total", 0))
        close = c.get("close", "-")
        val_status = c.get("valuation_status", "unknown")
        mos = c.get("margin_of_safety")
        mos_str = f"{_safe_float(mos)*100:.1f}%" if mos is not None else "-"
        score_cls = "high" if score >= 0.4 else ("mid" if score >= 0.2 else "low")
        summary_rows += f"""
        <tr>
          <td><span class="badge badge-sector">{c.get("sector","")}</span></td>
          <td class="rank-cell">{c.get("sector_rank", c.get("rank","-"))}位</td>
          <td><a class="stock-link" href="#stock-{ticker}"><strong>{name}</strong><br><span class="ticker-code">{ticker}</span></a></td>
          <td><span class="score-badge {score_cls}">{score:.4f}</span></td>
          <td>{_val_badge(val_status)}</td>
          <td class="num">{mos_str}</td>
          <td class="num col-hide-sp">{close}円</td>
        </tr>"""

    # 銘柄カード
    cards_html = ""
    for sector, items in sectors.items():
        cards_html += (f'<div class="sector-section">'
                       f'<h2 class="sector-title"><span class="sector-icon">▸</span>{sector}</h2>')
        for c in items:
            ticker = c["ticker"]
            sec = securities.get(ticker, {})
            fund = fundamentals.get(ticker, {})
            name = sec.get("name_ja", ticker)

            score_total = _safe_float(c.get("score_total", 0))
            score_quant = _safe_float(c.get("score_quantitative", 0))
            score_qual = _safe_float(c.get("score_qualitative", 0.5))
            score_roe = _safe_float(c.get("score_roe_stability", 0))
            score_growth = _safe_float(c.get("score_growth_quality", 0))
            score_fin = _safe_float(c.get("score_financial_solidity", 0))

            qual = c.get("qual_scores", {})
            score_moat = qual.get("moat", 5)
            score_circle = qual.get("circle", 5)
            score_sincerity = qual.get("sincerity", 5)
            score_cap = qual.get("capital_alloc", 5)
            score_enth = qual.get("enthusiasm", 5)
            qual_summary = qual.get("summary", "")

            close = c.get("close", "-")
            unit_price = int(float(close) * 100) if close not in ("-", "", None) else "-"
            eps = c.get("eps") or fund.get("eps", "-")
            bps = fund.get("bps", "-")
            cfo = _fmt_cfo(fund.get("cfo") or c.get("cfo"))
            eps_cagr_str = _pct(c.get("eps_cagr")) if c.get("eps_cagr") is not None else "-"
            cfo_q_str = f"{_safe_float(c.get('cfo_quality',0)):.2f}" if c.get("cfo_quality") is not None else "-"
            debt_str = f"{_safe_float(c.get('debt_multiple',0)):.1f}倍" if c.get("debt_multiple") is not None else "-"
            roe_years = c.get("roe_years", "-")
            val_status = c.get("valuation_status", "unknown")
            intrinsic = c.get("intrinsic_value")
            intrinsic_str = f"{float(intrinsic):,.0f}円" if intrinsic else "-"
            mos = c.get("margin_of_safety")
            mos_str = f"{float(mos)*100:.1f}%" if mos else "-"

            sector_rank = c.get("sector_rank", c.get("rank", "-"))
            explanation_html = _md_to_html(explanations.get(ticker, "（説明なし）"))
            score_cls = "high" if score_total >= 0.4 else ("mid" if score_total >= 0.2 else "low")

            cards_html += f"""
  <div class="stock-card" id="stock-{ticker}">
    <div class="card-header">
      <div class="card-title">
        <span class="rank-badge">{sector_rank}位</span>
        <span class="stock-name">{name}</span>
        <span class="ticker-pill">{ticker}</span>
        {_val_badge(val_status)}
      </div>
      <span class="score-badge-lg {score_cls}">総合スコア {score_total:.4f}</span>
    </div>

    <div class="metrics-grid">
      <div class="metric-block">
        <div class="metric-label">終値</div>
        <div class="metric-value">{close}<span class="metric-unit">円</span></div>
      </div>
      <div class="metric-block">
        <div class="metric-label">単元購入額</div>
        <div class="metric-value">{unit_price:,}<span class="metric-unit">円</span></div>
      </div>
      <div class="metric-block highlight-val">
        <div class="metric-label">内在価値（DCF）</div>
        <div class="metric-value">{intrinsic_str}</div>
      </div>
      <div class="metric-block highlight-val">
        <div class="metric-label">安全域</div>
        <div class="metric-value">{mos_str}</div>
      </div>
      <div class="metric-block">
        <div class="metric-label">EPS CAGR</div>
        <div class="metric-value">{eps_cagr_str}</div>
      </div>
      <div class="metric-block">
        <div class="metric-label">CFO品質</div>
        <div class="metric-value">{cfo_q_str}</div>
      </div>
      <div class="metric-block">
        <div class="metric-label">負債倍率</div>
        <div class="metric-value">{debt_str}</div>
      </div>
      <div class="metric-block">
        <div class="metric-label">ROE通過年数</div>
        <div class="metric-value">{roe_years}<span class="metric-unit">/5年</span></div>
      </div>
    </div>

    <div class="score-section">
      <div class="score-cols">
        <div class="score-col">
          <div class="score-section-title">定量スコア（{score_quant:.4f}）</div>
          {_score_bar("ROE安定性", score_roe, "#1d8348")}
          {_score_bar("成長の質", score_growth, "#0073bb")}
          {_score_bar("財務盤石さ", score_fin, "#6c3483")}
        </div>
        <div class="score-col">
          <div class="score-section-title">定性スコア（{score_qual*10:.1f}/10）</div>
          {_score_bar("経済的なお堀", score_moat, "#c0392b", max_val=10)}
          {_score_bar("能力の輪", score_circle, "#d35400", max_val=10)}
          {_score_bar("経営者の誠実さ", score_sincerity, "#2e86c1", max_val=10)}
          {_score_bar("資本配分", score_cap, "#1e8449", max_val=10)}
          {_score_bar("成長への熱意", score_enth, "#7d3c98", max_val=10)}
          {"" if not qual_summary else f'<p class="qual-summary">"{qual_summary}"</p>'}
        </div>
      </div>
    </div>

    <div class="explanation">
      <div class="explanation-title">AI 分析コメント</div>
      {explanation_html}
    </div>
  </div>"""
        cards_html += "</div>"

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>バフェット流投資候補レポート {run_date}</title>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f2f3f3;color:#16191f;font-size:14px;line-height:1.6}}
    .page-header{{background:#232f3e;color:#fff;padding:0}}
    .page-header-inner{{max-width:1200px;margin:0 auto;padding:16px 24px;display:flex;align-items:center;justify-content:space-between}}
    .page-header h1{{font-size:20px;font-weight:500}}
    .page-header .run-meta{{font-size:12px;color:#8d9196;text-align:right}}
    .orange-bar{{background:#ff9900;height:3px}}
    .content{{max-width:1200px;margin:0 auto;padding:24px}}
    .section-title{{font-size:18px;font-weight:600;color:#16191f;margin:28px 0 12px;padding-bottom:6px;border-bottom:2px solid #e5e7ea}}

    /* サマリーテーブル */
    .summary-box{{background:#fff;border:1px solid #dfe3e8;border-radius:6px;margin-bottom:28px;overflow-x:auto}}
    .summary-box table{{width:100%;border-collapse:collapse;min-width:520px}}
    .summary-box th{{background:#f2f3f3;color:#5f6b7a;font-weight:600;font-size:12px;padding:10px 14px;text-align:left;border-bottom:1px solid #dfe3e8;white-space:nowrap}}
    .summary-box td{{padding:10px 14px;border-bottom:1px solid #f2f3f3;vertical-align:middle;white-space:nowrap}}
    .summary-box tr:last-child td{{border-bottom:none}}
    .summary-box tr:hover td{{background:#fafafa}}
    html{{scroll-behavior:smooth}}
    .stock-card{{scroll-margin-top:16px}}
    .num{{text-align:right}}
    .rank-cell{{color:#5f6b7a;font-size:12px}}
    .ticker-code{{color:#8d9196;font-size:11px;font-family:monospace}}
    .col-hide-sp{{}}
    .stock-link{{color:inherit;text-decoration:none}}
    .stock-link:hover strong{{color:#0073bb;text-decoration:underline}}

    /* バッジ */
    .badge{{display:inline-block;border-radius:3px;padding:2px 8px;font-size:11px;font-weight:600}}
    .badge-sector{{background:#e8f4fd;color:#0073bb}}
    .score-badge{{display:inline-block;border-radius:12px;padding:2px 10px;font-size:12px;font-weight:700}}
    .score-badge.high{{background:#d4efdf;color:#1a7a34}}
    .score-badge.mid{{background:#fef9e7;color:#a07700}}
    .score-badge.low{{background:#f9ebea;color:#b03030}}
    .val-badge{{display:inline-block;border-radius:3px;padding:2px 8px;font-size:11px;font-weight:700}}
    .badge-buy{{background:#d4efdf;color:#1a7a34}}
    .badge-watch{{background:#fef9e7;color:#a07700}}
    .badge-exp{{background:#f9ebea;color:#b03030}}
    .badge-unk{{background:#f2f3f3;color:#8d9196}}

    /* 業種・カード */
    .sector-section{{margin-bottom:32px}}
    .sector-title{{font-size:16px;font-weight:700;color:#16191f;margin:24px 0 12px;display:flex;align-items:center;gap:8px}}
    .sector-icon{{color:#ff9900;font-size:14px}}
    .stock-card{{background:#fff;border:1px solid #dfe3e8;border-radius:6px;margin-bottom:16px;overflow:hidden}}
    .card-header{{background:#fafafa;border-bottom:1px solid #dfe3e8;padding:14px 20px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px}}
    .card-title{{display:flex;align-items:center;gap:10px;flex-wrap:wrap}}
    .rank-badge{{background:#232f3e;color:#ff9900;border-radius:3px;padding:2px 8px;font-size:12px;font-weight:700}}
    .stock-name{{font-size:16px;font-weight:700;color:#16191f}}
    .ticker-pill{{background:#e8f4fd;color:#0073bb;border-radius:10px;padding:1px 10px;font-size:12px;font-family:monospace;font-weight:600}}
    .score-badge-lg{{display:inline-block;border-radius:4px;padding:4px 14px;font-size:13px;font-weight:700}}
    .score-badge-lg.high{{background:#d4efdf;color:#1a7a34}}
    .score-badge-lg.mid{{background:#fef9e7;color:#a07700}}
    .score-badge-lg.low{{background:#f9ebea;color:#b03030}}

    /* メトリクス */
    .metrics-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:1px;background:#dfe3e8;border-bottom:1px solid #dfe3e8}}
    .metric-block{{background:#fff;padding:12px 16px}}
    .metric-block.highlight-val{{background:#fffbf0}}
    .metric-label{{font-size:11px;color:#8d9196;margin-bottom:4px;font-weight:500}}
    .metric-value{{font-size:17px;font-weight:700;color:#16191f;line-height:1.2}}
    .metric-unit{{font-size:11px;font-weight:400;color:#8d9196;margin-left:2px}}

    /* スコアセクション（2カラム） */
    .score-section{{padding:14px 20px;border-bottom:1px solid #f2f3f3;background:#fafcff}}
    .score-cols{{display:grid;grid-template-columns:1fr 1fr;gap:20px}}
    .score-col{{}}
    .score-section-title{{font-size:12px;font-weight:600;color:#5f6b7a;margin-bottom:8px}}
    .score-row{{display:flex;align-items:center;gap:10px;margin-bottom:6px}}
    .score-label{{font-size:11px;color:#5f6b7a;width:100px;flex-shrink:0}}
    .score-track{{flex:1;background:#e5e7ea;border-radius:4px;height:7px;overflow:hidden}}
    .score-fill{{height:100%;border-radius:4px}}
    .score-val{{font-size:11px;font-weight:600;color:#16191f;width:38px;text-align:right;flex-shrink:0}}
    .qual-summary{{font-size:11px;color:#5f6b7a;font-style:italic;margin-top:8px;padding:6px 8px;background:#f0f4fa;border-radius:4px}}

    /* 説明 */
    .explanation{{padding:16px 20px}}
    .explanation-title{{font-size:12px;font-weight:600;color:#5f6b7a;margin-bottom:10px;display:flex;align-items:center;gap:6px}}
    .explanation-title::before{{content:"";display:inline-block;width:3px;height:13px;background:#ff9900;border-radius:2px}}
    .explanation h3,.explanation h4{{font-size:13px;font-weight:700;color:#16191f;margin:10px 0 4px}}
    .explanation p{{color:#333;margin-bottom:6px;font-size:13px}}
    .explanation ul{{padding-left:18px;margin-bottom:6px}}
    .explanation li{{color:#333;font-size:13px;margin-bottom:3px}}
    .explanation strong{{color:#16191f}}

    .page-footer{{text-align:center;color:#8d9196;font-size:11px;padding:24px;margin-top:16px}}

    @media(max-width:640px){{
      .content{{padding:12px 8px}}
      .col-hide-sp{{display:none}}
      .score-cols{{grid-template-columns:1fr}}
      .metrics-grid{{grid-template-columns:repeat(2,1fr)}}
    }}
  </style>
</head>
<body>
<div class="page-header">
  <div class="page-header-inner">
    <h1>バフェット流 投資候補レポート</h1>
    <div class="run-meta"><div>{run_date}</div><div>個人投資支援システム</div></div>
  </div>
</div>
<div class="orange-bar"></div>
<div class="content">

  <div class="section-title">候補銘柄サマリー</div>
  <div class="summary-box">
    <table>
      <thead><tr>
        <th>業種</th><th>順位</th><th>銘柄</th><th>総合スコア</th>
        <th>バリュエーション</th><th class="num">安全域</th><th class="num col-hide-sp">終値</th>
      </tr></thead>
      <tbody>{summary_rows}</tbody>
    </table>
  </div>

  <div class="section-title">業種別 詳細レポート</div>
  {cards_html}

</div>
<div class="page-footer">
  このレポートはシステムが自動生成したものです。投資判断は必ずご自身の責任で行ってください。<br>
  生成日時: {run_date} ／ バフェット流個人投資支援システム
</div>
</body>
</html>"""


# ── ハンドラ ──────────────────────────────────────

def handler(event: dict, context) -> dict:
    run_id = event.get("run_id")
    if not run_id:
        raise ValueError("run_id が event に含まれていません")

    logger.info(json.dumps({"run_id": run_id, "stage": "explain"}))

    config = load_config(CONFIG_BUCKET)
    ddb = boto3.resource("dynamodb")
    ddb_client = boto3.client("dynamodb")
    s3 = boto3.client("s3")

    t_candidates = ddb.Table(f"{TABLE_PREFIX}-Candidates")
    t_fundamentals = ddb.Table(f"{TABLE_PREFIX}-Fundamentals")
    t_securities = ddb.Table(f"{TABLE_PREFIX}-Securities")
    t_run_logs = ddb.Table(f"{TABLE_PREFIX}-RunLogs")

    t_run_logs.put_item(Item={
        "run_id": run_id,
        "stage": "explain",
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
    })

    try:
        resp = t_candidates.query(
            KeyConditionExpression=Key("run_id").eq(run_id),
            ScanIndexForward=True,
        )
        candidates = resp.get("Items", [])
        if not candidates:
            raise ValueError(f"run_id={run_id} の候補銘柄が見つかりません")

        bedrock = BedrockClient(
            model_id=config.explanation.bedrock_model_id,
            region=config.explanation.bedrock_region,
            max_tokens=config.explanation.max_tokens,
            temperature=config.explanation.temperature,
        )
        edinet = EdinetClient() if config.qualitative.edinet_enabled else None
        ir = IRScraper() if config.qualitative.ir_scraping_enabled else None

        q_weight = _safe_float(config.scoring.qualitative_weight, 0.6)
        p_weight = _safe_float(config.scoring.quantitative_weight, 0.4)

        run_date = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d")

        securities: dict[str, dict] = {}
        fundamentals: dict[str, dict] = {}
        explanations: dict[str, str] = {}

        # ── 銘柄ごとの処理 ─────────────────────────────
        updated_records = []
        for c in candidates:
            ticker = c["ticker"]

            sec_resp = t_securities.get_item(Key={"ticker": ticker, "asset_class": "jp_stock"})
            securities[ticker] = sec_resp.get("Item", {})

            fund_resp = t_fundamentals.query(
                KeyConditionExpression=Key("ticker").eq(ticker),
                ScanIndexForward=False,
                Limit=1,
            )
            fund_items = fund_resp.get("Items", [])
            fundamentals[ticker] = fund_items[0] if fund_items else {}

            sec = securities[ticker]
            name = sec.get("name_ja", ticker)
            sector = sec.get("sector_33_name", "不明")

            # ── 定性分析（EDINET + IR → Bedrock採点）──
            edinet_info = {}
            ir_text = None

            if edinet:
                fy_end = c.get("fy_end") or fundamentals[ticker].get("fy_end")
                try:
                    edinet_info = edinet.get_company_info(ticker, fy_end)
                except Exception as e:
                    logger.warning(f"EDINET取得失敗 {ticker}: {e}")

            if ir:
                try:
                    ir_text = ir.get_ir_text(ticker, name)
                except Exception as e:
                    logger.warning(f"IR取得失敗 {ticker}: {e}")

            qual_scores = bedrock.score_qualitative(ticker, name, sector, edinet_info, ir_text)
            logger.info(f"定性スコア: {ticker} total={qual_scores.get('total', '?')}")

            # 最終スコア計算: 定量(0-1) × p_weight + 定性(0-1) × q_weight
            score_quant = _safe_float(c.get("score_quantitative", 0))
            score_qual_norm = qual_scores.get("total", 5.0) / 10.0
            final_score = round(p_weight * score_quant + q_weight * score_qual_norm, 4)

            # candidatesにin-memory追加
            c["qual_scores"] = qual_scores
            c["score_qualitative"] = round(score_qual_norm, 4)
            c["score_total"] = final_score

            # DynamoDB更新用レコード
            updated_records.append({
                **{k: v for k, v in c.items() if k not in ("qual_scores", "annual_records")},
                "score_qualitative": score_qual_norm,
                "score_total": final_score,
                "qual_moat": qual_scores["moat"],
                "qual_circle": qual_scores["circle"],
                "qual_sincerity": qual_scores["sincerity"],
                "qual_capital_alloc": qual_scores["capital_alloc"],
                "qual_enthusiasm": qual_scores["enthusiasm"],
                "qual_summary": qual_scores.get("summary", ""),
                "score_qual_total": qual_scores.get("total", 5.0),
            })

            # ── 説明生成（定量+定性を両方含むコンテキスト）──
            mos = c.get("margin_of_safety")
            intrinsic = c.get("intrinsic_value")
            ctx = {
                "ticker": ticker,
                "name": name,
                "sector": sector,
                "close": c.get("close", "-"),
                "score_total": f"{final_score:.4f}",
                "score_quantitative": f"{score_quant:.4f}",
                "score_roe": f"{_safe_float(c.get('score_roe_stability',0)):.4f}",
                "score_growth": f"{_safe_float(c.get('score_growth_quality',0)):.4f}",
                "score_fin": f"{_safe_float(c.get('score_financial_solidity',0)):.4f}",
                "roe_years": c.get("roe_years", "-"),
                "eps_cagr_pct": f"{_safe_float(c.get('eps_cagr',0))*100:.1f}",
                "cfo_quality": f"{_safe_float(c.get('cfo_quality',0)):.2f}",
                "debt_multiple": f"{_safe_float(c.get('debt_multiple',0)):.1f}",
                "close": c.get("close", "-"),
                "intrinsic_value": f"{float(intrinsic):,.0f}" if intrinsic else "算出不可",
                "margin_of_safety_pct": f"{float(mos)*100:.1f}" if mos is not None else "不明",
                "valuation_status": c.get("valuation_status", "unknown"),
                "score_moat": qual_scores.get("moat", 5),
                "score_circle": qual_scores.get("circle", 5),
                "score_sincerity": qual_scores.get("sincerity", 5),
                "score_capital_alloc": qual_scores.get("capital_alloc", 5),
                "score_enthusiasm": qual_scores.get("enthusiasm", 5),
                "qual_summary": qual_scores.get("summary", ""),
            }
            explanations[ticker] = bedrock.generate_explanation(ctx)
            logger.info(f"説明生成完了: {ticker}")

        # 最終スコアで再ソート・ランク更新
        candidates.sort(key=lambda x: _safe_float(x.get("score_total", 0)), reverse=True)
        for i, c in enumerate(candidates, start=1):
            c["rank"] = i

        # DynamoDB一括更新
        table_name = f"{TABLE_PREFIX}-Candidates"
        CHUNK = 25
        for i in range(0, len(updated_records), CHUNK):
            chunk = updated_records[i:i + CHUNK]
            req = {table_name: [{"PutRequest": {"Item": _serialize(it)}} for it in chunk]}
            retries = 0
            while req and retries < 5:
                resp = ddb_client.batch_write_item(RequestItems=req)
                req = resp.get("UnprocessedItems")
                if req:
                    retries += 1
                    import time; time.sleep(0.5 * (2 ** retries))

        # HTML生成 & S3保存
        html_content = _build_html(run_date, candidates, securities, fundamentals, explanations)
        report_key = f"reports/{run_date}/{run_id}.html"
        s3.put_object(
            Bucket=REPORTS_BUCKET,
            Key=report_key,
            Body=html_content.encode("utf-8"),
            ContentType="text/html; charset=utf-8",
        )
        logger.info(f"レポート保存: s3://{REPORTS_BUCKET}/{report_key}")

        t_run_logs.update_item(
            Key={"run_id": run_id, "stage": "explain"},
            UpdateExpression="SET #s = :s, completed_at = :t, report_key = :k",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":s": "success",
                ":t": datetime.now(timezone.utc).isoformat(),
                ":k": report_key,
            },
        )

        return {
            "run_id": run_id,
            "status": "success",
            "report_key": report_key,
            "candidates_explained": len(candidates),
        }

    except Exception as e:
        logger.error(f"Explain失敗: {e}", exc_info=True)
        t_run_logs.update_item(
            Key={"run_id": run_id, "stage": "explain"},
            UpdateExpression="SET #s = :s, completed_at = :t, error_message = :e",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":s": "error",
                ":t": datetime.now(timezone.utc).isoformat(),
                ":e": str(e),
            },
        )
        raise
