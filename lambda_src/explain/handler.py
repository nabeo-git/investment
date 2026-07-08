import json
import logging
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import boto3
from boto3.dynamodb.conditions import Key

from bedrock_client import BedrockClient
from config_loader import load_config

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ENVIRONMENT = os.environ["ENVIRONMENT"]
CONFIG_BUCKET = os.environ["CONFIG_BUCKET"]
REPORTS_BUCKET = os.environ["REPORTS_BUCKET"]
REGION = os.environ.get("AWS_REGION_MAIN", "ap-northeast-1")
TABLE_PREFIX = f"investment-{ENVIRONMENT}"


# ──────────────────────────────────────────────
# Markdown → HTML 変換（Bedrockの出力をインライン変換）
# ──────────────────────────────────────────────
def _md_to_html(text: str) -> str:
    lines = text.splitlines()
    out = []
    in_ul = False
    for line in lines:
        line = line.rstrip()
        # 見出し
        if line.startswith("### "):
            if in_ul: out.append("</ul>"); in_ul = False
            out.append(f"<h4>{line[4:]}</h4>")
        elif line.startswith("## "):
            if in_ul: out.append("</ul>"); in_ul = False
            out.append(f"<h3>{line[3:]}</h3>")
        # 箇条書き
        elif re.match(r"^[\-\*] ", line):
            if not in_ul: out.append("<ul>"); in_ul = True
            content = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", line[2:])
            out.append(f"<li>{content}</li>")
        # 番号付きリスト
        elif re.match(r"^\d+\. ", line):
            if in_ul: out.append("</ul>"); in_ul = False
            content = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", re.sub(r"^\d+\. ", "", line))
            out.append(f"<p><strong>{re.match(r'^\d+', line).group()}.</strong> {content}</p>")
        # 空行
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


# ──────────────────────────────────────────────
# スコアバー（0〜1 → 幅%のプログレスバー）
# ──────────────────────────────────────────────
def _score_bar(label: str, value: float, color: str = "#0073bb") -> str:
    pct = min(100, round(value * 100))
    return f"""
      <div class="score-row">
        <span class="score-label">{label}</span>
        <div class="score-track">
          <div class="score-fill" style="width:{pct}%;background:{color}"></div>
        </div>
        <span class="score-val">{value:.4f}</span>
      </div>"""


# ──────────────────────────────────────────────
# HTML生成
# ──────────────────────────────────────────────
def _build_html(run_date: str, candidates: list[dict], securities: dict, fundamentals: dict, explanations: dict) -> str:
    # 業種グループ化
    sectors: dict[str, list[dict]] = defaultdict(list)
    for c in sorted(candidates, key=lambda x: int(x.get("rank", 99))):
        sector = c.get("sector", "その他") or "その他"
        sectors[sector].append(c)

    # ── サマリーテーブル行 ──
    summary_rows = ""
    for c in sorted(candidates, key=lambda x: int(x.get("rank", 99))):
        ticker = c["ticker"]
        sec = securities.get(ticker, {})
        name = sec.get("name_ja", ticker)
        sector = c.get("sector", "")
        score = float(c.get("score_total", 0))
        div_yield = float(c.get("div_yield", 0)) * 100
        eq_ratio = float(c.get("equity_ratio", 0)) * 100
        close = c.get("close", "-")
        score_cls = "high" if score >= 0.4 else ("mid" if score >= 0.2 else "low")
        summary_rows += f"""
        <tr>
          <td><span class="badge badge-sector">{sector}</span></td>
          <td class="rank-cell">{c.get("sector_rank", c.get("rank", "-"))}位</td>
          <td><a class="stock-link" href="#stock-{ticker}"><strong>{name}</strong><br><span class="ticker-code">{ticker}</span></a></td>
          <td><span class="score-badge {score_cls}">{score:.4f}</span></td>
          <td class="col-hide-sp">{close}円</td>
          <td class="num">{div_yield:.2f}%</td>
          <td class="num col-hide-sp">{eq_ratio:.1f}%</td>
        </tr>"""

    # ── 銘柄カード ──
    cards_html = ""
    for sector, items in sectors.items():
        cards_html += f'<div class="sector-section"><h2 class="sector-title"><span class="sector-icon">▸</span>{sector}</h2>'
        for c in items:
            ticker = c["ticker"]
            sec = securities.get(ticker, {})
            fund = fundamentals.get(ticker, {})
            name = sec.get("name_ja", ticker)
            score_total = float(c.get("score_total", 0))
            score_div = float(c.get("score_dividend_continuity", 0))
            score_val = float(c.get("score_valuation", 0))
            score_fin = float(c.get("score_financial_health", 0))
            div_yield = float(c.get("div_yield", 0)) * 100
            eq_ratio = float(c.get("equity_ratio", 0)) * 100
            close = c.get("close", "-")
            unit_price = int(float(close) * 100) if close not in ("-", "", None) else "-"
            eps = fund.get("eps", "-")
            bps = fund.get("bps", "-")
            cfo = _fmt_cfo(fund.get("cfo"))
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
      <div class="metric-block highlight">
        <div class="metric-label">配当利回り（予想）</div>
        <div class="metric-value">{div_yield:.2f}<span class="metric-unit">%</span></div>
      </div>
      <div class="metric-block">
        <div class="metric-label">自己資本比率</div>
        <div class="metric-value">{eq_ratio:.1f}<span class="metric-unit">%</span></div>
      </div>
      <div class="metric-block">
        <div class="metric-label">EPS</div>
        <div class="metric-value">{eps}<span class="metric-unit">円</span></div>
      </div>
      <div class="metric-block">
        <div class="metric-label">BPS</div>
        <div class="metric-value">{bps}<span class="metric-unit">円</span></div>
      </div>
      <div class="metric-block">
        <div class="metric-label">営業CF</div>
        <div class="metric-value">{cfo}<span class="metric-unit">百万円</span></div>
      </div>
    </div>

    <div class="score-section">
      <div class="score-section-title">スコア内訳</div>
      {_score_bar("配当継続性", score_div, "#1d8348")}
      {_score_bar("割安度", score_val, "#0073bb")}
      {_score_bar("財務健全性", score_fin, "#b7950b")}
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
  <title>投資候補レポート {run_date}</title>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f2f3f3;color:#16191f;font-size:14px;line-height:1.6}}

    /* ── ヘッダー ── */
    .page-header{{background:#232f3e;color:#fff;padding:0}}
    .page-header-inner{{max-width:1100px;margin:0 auto;padding:16px 24px;display:flex;align-items:center;justify-content:space-between}}
    .page-header h1{{font-size:20px;font-weight:500;letter-spacing:.3px}}
    .page-header .run-meta{{font-size:12px;color:#8d9196;text-align:right}}
    .orange-bar{{background:#ff9900;height:3px}}

    /* ── コンテンツ ── */
    .content{{max-width:1100px;margin:0 auto;padding:24px}}

    /* ── セクションタイトル ── */
    .section-title{{font-size:18px;font-weight:600;color:#16191f;margin:28px 0 12px;padding-bottom:6px;border-bottom:2px solid #e5e7ea}}

    /* ── サマリーテーブル ── */
    .summary-box{{background:#fff;border:1px solid #dfe3e8;border-radius:6px;margin-bottom:28px;overflow-x:auto;-webkit-overflow-scrolling:touch}}
    .summary-box table{{width:100%;border-collapse:collapse;min-width:480px}}
    .summary-box th{{background:#f2f3f3;color:#5f6b7a;font-weight:600;font-size:12px;padding:10px 14px;text-align:left;border-bottom:1px solid #dfe3e8;white-space:nowrap}}
    .summary-box td{{padding:10px 14px;border-bottom:1px solid #f2f3f3;vertical-align:middle;white-space:nowrap}}
    .summary-box tr:last-child td{{border-bottom:none}}
    .summary-box tr:hover td{{background:#fafafa}}
    .col-hide-sp{{}}
    .stock-link{{color:inherit;text-decoration:none;display:block}}
    .stock-link:hover strong{{color:#0073bb;text-decoration:underline}}
    /* スムーズスクロール */
    html{{scroll-behavior:smooth}}
    /* アンカー位置補正（固定ヘッダ分）*/
    .stock-card{{scroll-margin-top:16px}}
    .num{{text-align:right}}
    .rank-cell{{color:#5f6b7a;font-size:12px;white-space:nowrap}}
    .ticker-code{{color:#8d9196;font-size:11px;font-family:monospace}}

    /* ── バッジ類 ── */
    .badge{{display:inline-block;border-radius:3px;padding:2px 8px;font-size:11px;font-weight:600}}
    .badge-sector{{background:#e8f4fd;color:#0073bb}}
    .score-badge{{display:inline-block;border-radius:12px;padding:2px 10px;font-size:12px;font-weight:700}}
    .score-badge.high{{background:#d4efdf;color:#1a7a34}}
    .score-badge.mid{{background:#fef9e7;color:#a07700}}
    .score-badge.low{{background:#f9ebea;color:#b03030}}

    /* ── 業種セクション ── */
    .sector-section{{margin-bottom:32px}}
    .sector-title{{font-size:16px;font-weight:700;color:#16191f;margin:24px 0 12px;display:flex;align-items:center;gap:8px}}
    .sector-icon{{color:#ff9900;font-size:14px}}

    /* ── 株カード ── */
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

    /* ── メトリクスグリッド ── */
    .metrics-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:1px;background:#dfe3e8;border-bottom:1px solid #dfe3e8}}
    .metric-block{{background:#fff;padding:12px 16px}}
    .metric-block.highlight{{background:#fffbf0}}
    .metric-label{{font-size:11px;color:#8d9196;margin-bottom:4px;font-weight:500}}
    .metric-value{{font-size:18px;font-weight:700;color:#16191f;line-height:1.2}}
    .metric-unit{{font-size:11px;font-weight:400;color:#8d9196;margin-left:2px}}

    /* ── スコアバー ── */
    .score-section{{padding:14px 20px;border-bottom:1px solid #f2f3f3;background:#fafcff}}
    .score-section-title{{font-size:12px;font-weight:600;color:#5f6b7a;margin-bottom:8px}}
    .score-row{{display:flex;align-items:center;gap:10px;margin-bottom:6px}}
    .score-label{{font-size:12px;color:#5f6b7a;width:90px;flex-shrink:0}}
    .score-track{{flex:1;background:#e5e7ea;border-radius:4px;height:8px;overflow:hidden}}
    .score-fill{{height:100%;border-radius:4px;transition:width .3s}}
    .score-val{{font-size:12px;font-weight:600;color:#16191f;width:52px;text-align:right;flex-shrink:0}}

    /* ── 説明文 ── */
    .explanation{{padding:16px 20px}}
    .explanation-title{{font-size:12px;font-weight:600;color:#5f6b7a;margin-bottom:10px;display:flex;align-items:center;gap:6px}}
    .explanation-title::before{{content:"";display:inline-block;width:3px;height:13px;background:#ff9900;border-radius:2px}}
    .explanation h3,.explanation h4{{font-size:13px;font-weight:700;color:#16191f;margin:10px 0 4px}}
    .explanation p{{color:#333;margin-bottom:6px;font-size:13px}}
    .explanation ul{{padding-left:18px;margin-bottom:6px}}
    .explanation li{{color:#333;font-size:13px;margin-bottom:3px}}
    .explanation strong{{color:#16191f}}

    /* ── フッター ── */
    .page-footer{{text-align:center;color:#8d9196;font-size:11px;padding:24px;margin-top:16px}}

    @media(max-width:640px){{
      .content{{padding:12px 8px}}
      .page-header-inner{{padding:12px 16px}}
      .page-header h1{{font-size:16px}}
      .section-title{{font-size:15px;margin:20px 0 10px}}
      /* サマリーテーブル: 横スクロール＋終値・自己資本比率列を非表示 */
      .col-hide-sp{{display:none}}
      .summary-box{{border-radius:4px}}
      .summary-box th,.summary-box td{{padding:8px 10px;font-size:12px}}
      /* 銘柄カード */
      .card-header{{flex-direction:column;align-items:flex-start;gap:6px}}
      .stock-name{{font-size:14px}}
      .metrics-grid{{grid-template-columns:repeat(2,1fr)}}
      .metric-value{{font-size:15px}}
      /* スコアバー */
      .score-label{{width:72px;font-size:11px}}
      .score-val{{width:44px;font-size:11px}}
      /* 説明文 */
      .explanation{{padding:12px 14px}}
      .explanation p,.explanation li{{font-size:12px}}
      .sector-title{{font-size:14px}}
    }}
  </style>
</head>
<body>

<div class="page-header">
  <div class="page-header-inner">
    <h1>📊 投資候補レポート</h1>
    <div class="run-meta">
      <div>{run_date}</div>
      <div>個人投資支援システム</div>
    </div>
  </div>
</div>
<div class="orange-bar"></div>

<div class="content">

  <div class="section-title">候補銘柄サマリー</div>
  <div class="summary-box">
    <table>
      <thead>
        <tr>
          <th>業種</th><th>順位</th><th>銘柄</th><th>総合スコア</th>
          <th class="col-hide-sp">終値</th><th class="num">配当利回り</th><th class="num col-hide-sp">自己資本比率</th>
        </tr>
      </thead>
      <tbody>{summary_rows}</tbody>
    </table>
  </div>

  <div class="section-title">業種別 詳細レポート</div>
  {cards_html}

</div>

<div class="page-footer">
  このレポートはシステムが自動生成したものです。投資判断は必ずご自身の責任で行ってください。<br>
  生成日時: {run_date} ／ 個人投資支援システム
</div>

</body>
</html>"""


# ──────────────────────────────────────────────
# ハンドラ
# ──────────────────────────────────────────────
def handler(event: dict, context) -> dict:
    run_id = event.get("run_id")
    if not run_id:
        raise ValueError("run_id が event に含まれていません")

    logger.info(json.dumps({"run_id": run_id, "stage": "explain"}))

    config = load_config(CONFIG_BUCKET)
    ddb = boto3.resource("dynamodb")
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

        run_date = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d")

        # 銘柄マスタ・財務データ・説明を一括取得
        securities: dict[str, dict] = {}
        fundamentals: dict[str, dict] = {}
        explanations: dict[str, str] = {}

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

            ctx = {
                "ticker": ticker,
                "name": securities[ticker].get("name_ja", ticker),
                "sector": securities[ticker].get("sector_33_name", "不明"),
                "close": c.get("close", "-"),
                "unit_price": str(int(float(c.get("close", 0)) * 100)),
                "div_yield_pct": f"{float(c.get('div_yield', 0)) * 100:.2f}",
                "score_total": f"{float(c.get('score_total', 0)):.4f}",
                "score_div": f"{float(c.get('score_dividend_continuity', 0)):.4f}",
                "score_val": f"{float(c.get('score_valuation', 0)):.4f}",
                "score_fin": f"{float(c.get('score_financial_health', 0)):.4f}",
                "equity_ratio_pct": f"{float(c.get('equity_ratio', 0)) * 100:.1f}",
                "eps": fundamentals[ticker].get("eps", "-"),
                "bps": fundamentals[ticker].get("bps", "-"),
                "cfo": _fmt_cfo(fundamentals[ticker].get("cfo")),
            }
            explanations[ticker] = bedrock.generate_explanation(ctx)
            logger.info(f"説明生成完了: {ticker}")

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


def _fmt_cfo(value) -> str:
    try:
        return f"{int(float(value)) // 1_000_000:,}"
    except Exception:
        return "-"
