"""
EDINET API クライアント
有価証券報告書から事業テキストを取得し、LLM定性分析に使用する。
API: https://disclosure.edinet-fsa.go.jp/api/v2/
"""
import calendar
import io
import logging
import re
import zipfile
from datetime import date
from typing import Optional

import requests

logger = logging.getLogger(__name__)

EDINET_BASE = "https://disclosure.edinet-fsa.go.jp/api/v2"
DOC_TYPE_ANNUAL = "120"  # 有価証券報告書


class EdinetClient:
    def __init__(self, timeout: int = 15):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "investment-research-system/1.0"

    def find_latest_annual_report(self, ticker: str, fy_end: Optional[str] = None) -> Optional[dict]:
        """
        証券コードから最新の有価証券報告書のメタ情報を返す。
        fy_end: 決算期末日（YYYY-MM-DD形式）。Noneの場合は過去18ヶ月を広く検索。
        """
        # EDINET の secCode は証券コード + "0"（5桁）
        sec_code = ticker + "0"

        search_dates = self._candidate_dates(fy_end)
        for d in search_dates:
            doc = self._search_on_date(d, sec_code)
            if doc:
                logger.info(f"EDINET: {ticker} の有報を発見 date={d} docID={doc.get('docID')}")
                return doc
        logger.info(f"EDINET: {ticker} の有報が見つかりません")
        return None

    def _candidate_dates(self, fy_end: Optional[str]) -> list[str]:
        """有報提出が見込まれる月末日リストを生成する。"""
        today = date.today()
        candidates = []

        if fy_end:
            # 決算期末の3〜4ヶ月後が提出月（過去2期分を検索）
            try:
                from datetime import datetime
                fy_date = datetime.strptime(fy_end[:7], "%Y-%m").date()
                for years_back in range(2):
                    base_year = fy_date.year - years_back
                    for extra in [3, 4, 2]:
                        month = fy_date.month + extra
                        year = base_year
                        while month > 12:
                            month -= 12
                            year += 1
                        last_day = calendar.monthrange(year, month)[1]
                        candidates.append(f"{year:04d}-{month:02d}-{last_day:02d}")
            except Exception:
                pass

        # フォールバック: 直近18ヶ月を月末日で検索
        if not candidates:
            for months_back in range(18):
                year = today.year
                month = today.month - months_back
                while month <= 0:
                    month += 12
                    year -= 1
                last_day = calendar.monthrange(year, month)[1]
                candidates.append(f"{year:04d}-{month:02d}-{last_day:02d}")

        return candidates

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
            logger.debug(f"EDINET search {target_date}: {e}")
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
        """
        有報テキストから事業概要・リスク・経営方針の各セクションを抽出する。
        """
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
