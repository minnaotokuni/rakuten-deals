"""楽天市場APIクライアント（2026-05-13 新仕様: accessKey + Referer 必須）"""

from __future__ import annotations

import os
import time
import urllib.parse

import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

API_URL = "https://openapi.rakuten.co.jp/ichibams/api/IchibaItem/Search/20260401"


class RakutenClient:
    def __init__(self) -> None:
        self.app_id = os.environ["RAKUTEN_APP_ID"]
        self.access_key = os.environ["RAKUTEN_ACCESS_KEY"]
        self.affiliate_id = os.environ.get("RAKUTEN_AFFILIATE_ID", "")
        self.referer = os.environ.get("RAKUTEN_REFERER", "")
        self._last = 0.0

    def _headers(self) -> dict[str, str]:
        if not self.referer:
            return {}
        p = urllib.parse.urlparse(self.referer)
        return {"Referer": self.referer, "Origin": f"{p.scheme}://{p.netloc}"}

    def search(self, keyword: str, hits: int = 30, page: int = 1,
               sort: str = "-reviewCount", min_price: int = 0) -> list[dict]:
        # API レート制限対策 (1req/sec 程度に抑える)
        wait = 1.2 - (time.time() - self._last)
        if 0 < wait < 1.2:
            time.sleep(wait)
        params: dict[str, str | int] = {
            "applicationId": self.app_id,
            "accessKey": self.access_key,
            "keyword": keyword,
            "hits": max(1, min(hits, 30)),
            "page": page,
            "format": "json",
            "formatVersion": 2,
            "sort": sort,
            "imageFlag": 1,
            "availability": 1,
        }
        if min_price:
            params["minPrice"] = min_price
        if self.affiliate_id:
            params["affiliateId"] = self.affiliate_id
        r = requests.get(API_URL, params=params, headers=self._headers(), timeout=15)
        self._last = time.time()
        r.raise_for_status()
        payload = r.json()
        if "errors" in payload:
            raise RuntimeError(f"Rakuten API error: {payload['errors']}")
        items = payload.get("Items") or payload.get("items") or []
        # 旧形式 {"Item": {...}} 混在対応
        return [it.get("Item", it) for it in items if isinstance(it, dict)]
