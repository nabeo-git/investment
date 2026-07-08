"""
会社IRページスクレイパー
社長メッセージ・代表者コメントをテキストとして取得する。
各社でURL構造が異なるため、一般的なパスパターンを順番に試みる。
"""
import logging
import re
from typing import Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

IR_MESSAGE_PATHS = [
    "/ir/message",
    "/ir/ceo-message",
    "/ir/ceo_message",
    "/ir/top-message",
    "/ir/management-message",
    "/company/message",
    "/company/ceo-message",
    "/about/message",
    "/ceo-message",
    "/top-message",
    "/ir/investor/message",
    "/corporate/message",
    "/ja/ir/message",
    "/ja/company/message",
]

KNOWN_IR_BASE_URLS: dict[str, str] = {
    "7203": "https://global.toyota",
    "6758": "https://www.sony.com",
    "9984": "https://www.softbank.jp",
    "8306": "https://www.mufg.jp",
    "6861": "https://www.keyence.co.jp",
    "4568": "https://www.daiichisankyo.co.jp",
    "6954": "https://www.fanuc.co.jp",
    "7974": "https://www.nintendo.co.jp",
    "4519": "https://chugai-pharm.co.jp",
    "8035": "https://www.tel.co.jp",
    "6367": "https://www.daikin.co.jp",
    "2914": "https://www.jti.com",
}


class IRScraper:
    def __init__(self, timeout: int = 10):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) investment-research/1.0",
            "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml",
        })

    def get_ir_text(self, ticker: str, name_ja: str = "") -> Optional[str]:
        """
        IRテキストを取得する。失敗時はNoneを返す（graceful fallback）。
        """
        base_url = KNOWN_IR_BASE_URLS.get(ticker)
        if not base_url:
            return None

        for path in IR_MESSAGE_PATHS:
            url = base_url.rstrip("/") + path
            text = self._fetch_text(url)
            if text and len(text) > 300:
                logger.info(f"IR: {ticker} テキスト取得成功 url={url}")
                return text[:3000]

        logger.info(f"IR: {ticker} メッセージページが見つかりません")
        return None

    def _fetch_text(self, url: str) -> Optional[str]:
        try:
            resp = self.session.get(url, timeout=self.timeout, allow_redirects=True)
            if resp.status_code != 200:
                return None
            soup = BeautifulSoup(resp.content, "html.parser")
            for tag in soup(["script", "style", "nav", "header", "footer",
                              "aside", "form", "button"]):
                tag.decompose()

            # メインコンテンツ領域を優先的に探す
            for selector in ["main", "article", ".message", ".ceo-message",
                              ".president-message", "#content", ".content"]:
                el = soup.select_one(selector)
                if el:
                    text = el.get_text(separator=" ", strip=True)
                    text = re.sub(r"\s+", " ", text)
                    if len(text) > 200:
                        return text

            text = soup.get_text(separator=" ", strip=True)
            text = re.sub(r"\s+", " ", text)
            return text.strip() if text else None
        except Exception as e:
            logger.debug(f"IR fetch failed {url}: {e}")
            return None
