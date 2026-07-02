"""お得品発掘エンジン。

楽天APIの itemPriceMax3（過去の最高値ベース）と現在価格を比較し、
「実際に値下がりしている」商品だけを抽出してスコアリングする。
"""

from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass, field, asdict

from rakuten import RakutenClient

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
POST_LOG = os.path.join(BASE_DIR, "posted_log.json")

# 購買意欲が高く単価もそこそこあるジャンルを日替わりでローテーション
KEYWORD_POOL = [
    "ワイヤレスイヤホン",
    "モバイルバッテリー",
    "ロボット掃除機",
    "空気清浄機",
    "電気圧力鍋",
    "ドライヤー",
    "スマートウォッチ",
    "布団セット",
    "コーヒーメーカー",
    "加湿器",
    "炊飯器",
    "美顔器",
    "電動歯ブラシ",
    "ゲーミングチェア",
    "アウトドアチェア",
    "リュック メンズ",
    "スニーカー",
    "プロテイン",
    "米 10kg",
    "ビール 24本",
    # 高単価・高報酬ジャンル
    "ふるさと納税 肉",
    "ふるさと納税 米",
    "マットレス",
    "ソファ",
    "テレビ 50インチ",
    "ノートパソコン",
    "タブレット",
    "冷蔵庫",
    "洗濯機",
]

# 月ごとの季節キーワード（実行時の月で自動選択）
SEASONAL_KEYWORDS: dict[int, list[str]] = {
    1: ["福袋", "加湿器", "電気毛布", "こたつ", "スキーウェア"],
    2: ["チョコレート ギフト", "花粉症 マスク", "空気清浄機", "新生活 家電セット"],
    3: ["新生活 家電セット", "ランドセル", "スーツ", "炊飯器 一人暮らし"],
    4: ["日傘", "UVカット", "レインコート", "母の日 ギフト"],
    5: ["扇風機", "母の日 ギフト", "父の日 ギフト", "冷感 敷きパッド"],
    6: ["除湿機", "父の日 ギフト", "日傘", "冷感 敷きパッド", "サーキュレーター"],
    7: ["扇風機", "サーキュレーター", "冷感 敷きパッド", "うなぎ", "そうめん",
        "日傘", "虫除け", "水着 レディース", "クーラーボックス", "お中元"],
    8: ["ハンディファン", "冷感タオル", "浮き輪", "帰省 手土産", "防災セット"],
    9: ["防災セット", "敬老の日 ギフト", "秋物 ジャケット", "加湿器"],
    10: ["ハロウィン", "電気毛布", "こたつ", "加湿器", "ヒーター"],
    11: ["ブラックフライデー", "こたつ", "ヒーター", "クリスマスプレゼント", "おせち"],
    12: ["クリスマスプレゼント", "おせち", "福袋", "年越しそば", "カニ"],
}


def active_keywords() -> list[str]:
    """定番プール + 今月の季節キーワード。"""
    from datetime import date
    return KEYWORD_POOL + SEASONAL_KEYWORDS.get(date.today().month, [])

MIN_PRICE = 1980          # 安すぎると報酬が小さい
MIN_REVIEWS = 50          # 信頼できる商品だけ
MIN_RATING = 4.0
MIN_DISCOUNT_PCT = 15.0   # 過去最高値から15%以上下がっているもののみ
MAX_DISCOUNT_PCT = 60.0   # これ以上はバリエーション価格差の可能性が高く誇大表示になる

# 「500g or 1kg」「選べるカラー」のようにページ内に複数価格帯のSKUがある商品は
# min/max が実質の値引きではなくサイズ違いの価格差になるため除外する。
VARIANT_NAME_PATTERN = re.compile(r"選べる|えらべる|\bor\b|よりどり|組み合わせ自由", re.IGNORECASE)


@dataclass
class Deal:
    item_code: str
    name: str
    price: int
    price_max: int
    discount_pct: float
    review_count: int
    review_avg: float
    affiliate_rate: float
    affiliate_url: str
    image_url: str
    shop_name: str
    keyword: str
    score: float = field(default=0.0)

    def to_dict(self) -> dict:
        return asdict(self)


def clean_name(name: str, limit: int = 45) -> str:
    """楽天特有の【】装飾や重複スペースを除いて表示用に短縮する。"""
    name = re.sub(r"【[^】]*】", "", name)
    name = re.sub(r"[\\\u3000\s]+", " ", name).strip()
    return name[:limit] + ("…" if len(name) > limit else "")


def item_to_deal(item: dict, keyword: str) -> Deal | None:
    raw_name = str(item.get("itemName") or "")
    if VARIANT_NAME_PATTERN.search(raw_name):
        return None
    price = int(item.get("itemPrice") or 0)
    # itemPriceMax3 = 直近集計期間の最高値。現在価格との差が実質の値引き幅。
    price_max = max(
        int(item.get("itemPriceMax3") or 0),
        int(item.get("itemPriceMax2") or 0),
    )
    if price < MIN_PRICE or price_max <= price:
        return None
    discount = (price_max - price) / price_max * 100
    reviews = int(item.get("reviewCount") or 0)
    rating = float(item.get("reviewAverage") or 0)
    aff_url = str(item.get("affiliateUrl") or "")
    if (
        not MIN_DISCOUNT_PCT <= discount <= MAX_DISCOUNT_PCT
        or reviews < MIN_REVIEWS
        or rating < MIN_RATING
        or not aff_url
    ):
        return None
    images = item.get("mediumImageUrls") or []
    image = images[0] if images else ""
    if isinstance(image, dict):  # 旧formatVersion対策
        image = image.get("imageUrl", "")
    deal = Deal(
        item_code=str(item.get("itemCode") or ""),
        name=clean_name(str(item.get("itemName") or "")),
        price=price,
        price_max=price_max,
        discount_pct=round(discount, 1),
        review_count=reviews,
        review_avg=rating,
        affiliate_rate=float(item.get("affiliateRate") or 0),
        affiliate_url=aff_url,
        image_url=str(image).replace("128x128", "300x300"),
        shop_name=str(item.get("shopName") or ""),
        keyword=keyword,
    )
    # 割引率を主軸に、レビューの厚みと報酬率を加点
    deal.score = (
        deal.discount_pct
        + math.log10(max(reviews, 10)) * 6
        + deal.affiliate_rate * 3
        + (rating - 4.0) * 10
    )
    return deal


def load_posted() -> set[str]:
    if os.path.exists(POST_LOG):
        with open(POST_LOG) as f:
            return {e["item_code"] for e in json.load(f)}
    return set()


def find_deals(keywords: list[str] | None = None, per_keyword: int = 30) -> list[Deal]:
    """キーワード群を検索し、スコア降順の Deal リストを返す（投稿済みは除外）。"""
    client = RakutenClient()
    posted = load_posted()
    seen: set[str] = set()
    deals: list[Deal] = []
    for kw in keywords or KEYWORD_POOL:
        try:
            items = client.search(kw, hits=per_keyword, sort="-reviewCount")
        except Exception as e:
            print(f"[WARN] search failed for {kw!r}: {e}")
            continue
        for item in items:
            deal = item_to_deal(item, kw)
            if deal and deal.item_code not in posted and deal.item_code not in seen:
                seen.add(deal.item_code)
                deals.append(deal)
    deals.sort(key=lambda d: d.score, reverse=True)
    return deals
