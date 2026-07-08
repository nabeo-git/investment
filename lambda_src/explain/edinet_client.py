"""
EDINET API クライアント
有価証券報告書から事業テキストを取得し、LLM定性分析に使用する。
API: https://disclosure.edinet-fsa.go.jp/api/v2/
"""
import io
import logging
import os
import re
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from typing import Optional

import boto3
import requests

logger = logging.getLogger(__name__)

EDINET_BASE = "https://disclosure.edinet-fsa.go.jp/api/v2"
DOC_TYPE_ANNUAL = "120"  # 有価証券報告書
EDINET_SECRET_NAME = os.environ.get("EDINET_SECRET_NAME", "investment-dev/edinet-api-key")


def _get_api_key() -> Optional[str]:
    """Secrets Manager から EDINET API キーを取得する。"""
    try:
        client = boto3.client("secretsmanager",
                              region_name=os.environ.get("AWS_REGION_MAIN", "ap-northeast-1"))
        resp = client.get_secret_value(SecretId=EDINET_SECRET_NAME)
        return resp["SecretString"].strip()
    except Exception as e:
        logger.warning(f"EDINET APIキー取得失敗: {e}")
        return None


class EdinetClient:
    def __init__(self, timeout: int = 10):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "investment-research-system/1.0"
        api_key = _get_api_key()
        if api_key:
            self.session.headers["Ocp-Apim-Subscription-Key"] = api_key
            logger.info("EDINET APIキー設定済み")
        else:
            logger.warning("EDINET APIキー未設定 — 認証なしで試行")

    def find_latest_annual_report(self, ticker: str, fy_end: Optional[str] = None) -> Optional[dict]:
        """
        証券コードから最新の有価証券報告書メタ情報を返す。
        有報提出期間の全日付を並列検索し、最初にヒットした書類を返す。
        fy_end: 決算期末日（YYYY-MM-DD）。指定があれば検索範囲を絞る。
        """
        # J-Quants の ticker は既に 5桁（4桁証券コード+"0"）= EDINET の secCode と一致
        sec_code = ticker
        search_dates = self._candidate_dates(fy_end)

        logger.info(f"EDINET: {ticker} 検索開始 {len(search_dates)}日分")

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(self._search_on_date, d, sec_code): d
                       for d in search_dates}
            result = None
            for fut in as_completed(futures):
                doc = fut.result()
                if doc:
                    result = doc
                    # 残タスクをキャンセルして早期終了
                    for pending in futures:
                        pending.cancel()
                    break

        if result:
            logger.info(f"EDINET: {ticker} 有報発見 docID={result.get('docID')} period={result.get('periodEnd')}")
        else:
            logger.info(f"EDINET: {ticker} 有報が見つかりません")
        return result

    def _candidate_dates(self, fy_end: Optional[str]) -> list[str]:
        """
        有報が提出される可能性のある日付リストを返す（全日検索）。
        fy_end が分かっている場合: 決算期末+55日〜+120日 を2期分
        fy_end 不明の場合: 直近1年を全日検索
        """
        today = date.today()
        dates: list[str] = []

        if fy_end:
            try:
                fy_date = datetime.strptime(fy_end[:10], "%Y-%m-%d").date()
                for years_back in range(2):
                    base = fy_date.replace(year=fy_date.year - years_back)
                    # 有報提出義務: 決算期末から3ヶ月以内（実態は60〜90日後が多い）
                    window_start = base + timedelta(days=55)
                    window_end   = min(base + timedelta(days=125), today)
                    d = window_start
                    while d <= window_end:
                        dates.append(d.strftime("%Y-%m-%d"))
                        d += timedelta(days=1)
            except Exception as e:
                logger.debug(f"EDINET candidate_dates parse error: {e}")

        if not dates:
            # fy_end 不明: 直近1年を全日検索
            d = today - timedelta(days=365)
            while d <= today:
                dates.append(d.strftime("%Y-%m-%d"))
                d += timedelta(days=1)

        return dates

    def _search_on_date(self, target_date: str, sec_code: str) -> Optional[dict]:
        """指定日のEDINET提出一覧から対象企業の有価証券報告書を検索。"""
        try:
            resp = self.session.get(
                f"{EDINET_BASE}/documents.json",
                params={"date": target_date, "type": 2},
                timeout=self.timeout,
            )
            if resp.status_code != 200:
                return None
            for doc in resp.json().get("results", []):
                if (doc.get("secCode", "") == sec_code and
                        doc.get("docTypeCode") == DOC_TYPE_ANNUAL):
                    return doc
        except Exception as e:
            # 401 は APIキー未設定を示す。1回だけ WARNING に昇格
            msg = str(e)
            if "401" in msg or "subscription" in msg.lower():
                logger.warning(f"EDINET 認証エラー（APIキー未設定）: {e}")
                return None  # 以降のリトライは不要なので呼び出し側で判断
            logger.debug(f"EDINET {target_date}: {e}")
        return None

    def get_document_text(self, doc_id: str, max_bytes: int = 5 * 1024 * 1024) -> Optional[str]:
        """
        有価証券報告書ZIPをダウンロードし、HTML→テキスト変換して返す。
        type=1: 書類本体（HTMLを含むZIP）
        """
        try:
            resp = self.session.get(
                f"{EDINET_BASE}/documents/{doc_id}",
                params={"type": 1},
                timeout=30,
                stream=True,
            )
            if resp.status_code != 200:
                return None
            content = b""
            for chunk in resp.iter_content(chunk_size=8192):
                content += chunk
                if len(content) >= max_bytes:
                    break
            return self._extract_text_from_zip(content)
        except Exception as e:
            logger.warning(f"EDINET download failed {doc_id}: {e}")
            return None

    def _extract_text_from_zip(self, zip_bytes: bytes) -> Optional[str]:
        """ZIPからHTMLファイルを抽出してテキストに変換する。"""
        try:
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                texts = []
                for name in sorted(zf.namelist()):
                    if name.lower().endswith((".htm", ".html")):
                        with zf.open(name) as f:
                            raw = f.read(1024 * 1024)
                            text = _html_to_text(raw.decode("utf-8", errors="replace"))
                            if len(text) > 200:
                                texts.append(text)
                        if len(texts) >= 5:
                            break
                return "\n\n".join(texts) if texts else None
        except Exception as e:
            logger.debug(f"ZIP extract failed: {e}")
            return None

    def extract_key_sections(self, full_text: str) -> dict[str, str]:
        """有報テキストから事業概要・リスク・経営方針の各セクションを抽出する。"""
        sections = {}
        patterns = {
            "business": [
                r"事業の内容(.{300,3000}?)(?:従業員|生産|販売|設備)",
                r"事業概要(.{200,2000}?)(?:セグメント|事業区分|主要)",
            ],
            "risks": [
                r"事業等のリスク(.{300,3000}?)(?:内部統制|コーポレート|財政)",
                r"リスク要因(.{300,2000}?)(?:内部統制|コーポレート)",
            ],
            "management": [
                r"経営方針、経営環境(.{300,2000}?)(?:経営上の重要|リスク|セグメント)",
                r"経営者による財政状態(.{300,2000}?)(?:リスク|セグメント|設備)",
                r"代表取締役.{0,30}メッセージ(.{200,2000}?)(?:事業|財務|リスク)",
            ],
        }
        for key, pats in patterns.items():
            for pat in pats:
                m = re.search(pat, full_text, re.DOTALL)
                if m:
                    sections[key] = m.group(1)[:1500].strip()
                    break
        return sections

    def get_company_info(self, ticker: str, fy_end: Optional[str] = None) -> dict:
        """
        ticker に対応する有報テキストの主要セクションを返す。
        失敗時は空辞書を返す（呼び出し元で graceful fallback）。
        """
        doc = self.find_latest_annual_report(ticker, fy_end)
        if not doc:
            return {}
        full_text = self.get_document_text(doc.get("docID", ""))
        if not full_text:
            return {}
        sections = self.extract_key_sections(full_text)
        sections["company_name"] = doc.get("filerName", "")
        sections["period_end"] = doc.get("periodEnd", "")
        return sections


def _html_to_text(html: str) -> str:
    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()
