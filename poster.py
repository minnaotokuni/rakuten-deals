"""X (Twitter) への投稿文生成と自動投稿。

戦略:
  - 本文はお得情報として完結させ、アフィリンクは商品画像付きで直接添付
  - 「PR」表記を必ず入れる（ステマ規制対応）
  - 投稿済み商品は posted_log.json に記録して重複投稿を防ぐ
"""

from __future__ import annotations

import io
import json
import os
from datetime import datetime

import requests
import tweepy
from dotenv import load_dotenv

from deals import Deal, POST_LOG

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

SITE_URL = os.environ.get("SITE_URL", "")


def build_tweet(deal: Deal) -> str:
    """140文字（URLはt.coで23字換算）に収まる投稿文を組み立てる。"""
    saved = deal.price_max - deal.price
    lines = [
        f"【{deal.discount_pct:.0f}%オフ】{deal.name}",
        f"{deal.price_max:,}円 → {deal.price:,}円（{saved:,}円お得）",
        f"⭐{deal.review_avg} レビュー{deal.review_count:,}件",
        "",
        deal.affiliate_url,
        "#楽天セール #PR",
    ]
    text = "\n".join(lines)
    # URL は23字換算なので実文字数ベースで粗く調整
    budget = 140 - 23 - (len(text) - len(deal.affiliate_url))
    if budget < 0:
        short = deal.name[: max(10, len(deal.name) + budget - 1)] + "…"
        lines[0] = f"【{deal.discount_pct:.0f}%オフ】{short}"
        text = "\n".join(lines)
    return text


def make_clients() -> tuple[tweepy.Client, tweepy.API]:
    auth = tweepy.OAuth1UserHandler(
        os.environ["X_API_KEY"],
        os.environ["X_API_SECRET"],
        os.environ["X_ACCESS_TOKEN"],
        os.environ["X_ACCESS_TOKEN_SECRET"],
    )
    return tweepy.Client(
        consumer_key=os.environ["X_API_KEY"],
        consumer_secret=os.environ["X_API_SECRET"],
        access_token=os.environ["X_ACCESS_TOKEN"],
        access_token_secret=os.environ["X_ACCESS_TOKEN_SECRET"],
    ), tweepy.API(auth)


def post_deal(deal: Deal, dry_run: bool = False) -> str | None:
    """1件投稿してツイートIDを返す。dry_run 時は本文表示のみ。"""
    text = build_tweet(deal)
    if dry_run:
        print("----- DRY RUN -----")
        print(text)
        return None
    client, api_v1 = make_clients()
    media_ids: list[str] = []
    if deal.image_url:
        try:
            img = requests.get(deal.image_url, timeout=10)
            img.raise_for_status()
            media = api_v1.media_upload(filename="deal.jpg", file=io.BytesIO(img.content))
            media_ids.append(media.media_id_string)
        except Exception as e:
            print(f"[WARN] image upload failed: {e}")
    resp = client.create_tweet(text=text, media_ids=media_ids or None)
    tweet_id = resp.data["id"]
    log_posted(deal, tweet_id)
    return tweet_id


def log_posted(deal: Deal, tweet_id: str) -> None:
    entries = []
    if os.path.exists(POST_LOG):
        with open(POST_LOG) as f:
            entries = json.load(f)
    entry = deal.to_dict()
    entry["tweet_id"] = tweet_id
    entry["posted_at"] = datetime.now().isoformat(timespec="seconds")
    entries.append(entry)
    with open(POST_LOG, "w") as f:
        json.dump(entries, f, ensure_ascii=False, indent=1)
