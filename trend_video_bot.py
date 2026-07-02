"""トレンド連動Kling動画ボット。

Googleトレンド(日本)から今バズっている話題を取得し、
Gemini が「映像映えする企画」を選定して Kling 用プロンプトを作成、
fal AI 経由の Kling で高品質AI動画を生成して X に投稿する。

実在の人物がトレンドの場合はスキップする（ディープフェイク回避）。
コスト: Kling v2.5 Turbo Pro 5秒 ≒ $0.35/本。1日1本 ≒ 月$10前後。
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import requests
import tweepy
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

OUTPUT_DIR = BASE_DIR / "trend_videos"
TREND_LOG = BASE_DIR / "trend_posted_log.json"

TRENDS_RSS = "https://trends.google.co.jp/trending/rss?geo=JP"
GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-2.5-flash-lite:generateContent"
)
KLING_MODEL = "fal-ai/kling-video/v2.5-turbo/pro/text-to-video"
FAL_QUEUE = "https://queue.fal.run"

PLANNER_PROMPT = """あなたはXでバズる短尺AI動画のプランナーです。
以下は今この瞬間の日本のGoogle検索トレンドです（関連ニュース見出し付き）。

{trends}

# タスク
1. この中から「AI動画にして最も映像映え・バズりやすい」話題を1つ選ぶ
2. その話題に乗っかった、見た人が思わずリポストしたくなる5秒動画の企画を作る

# 選定ルール（重要）
- 実在の人物名・芸能人・アーティストの話題は絶対に選ばない（肖像権/ディープフェイク回避）
- 事件・事故・災害・訃報など不謹慎になり得る話題も選ばない
- 物・場所・イベント・季節・スポーツ・自然現象・食べ物・乗り物などが理想
- 全滅の場合は "fallback": true とし、今日の日本の季節ネタ（7月上旬）で企画する

# 出力形式（JSONのみ。コードブロック記号や説明は一切不要）
{{
  "fallback": false,
  "chosen_trend": "選んだトレンド語",
  "concept": "動画企画の一言説明（日本語）",
  "kling_prompt": "Klingに送る英語プロンプト。カメラワーク・ライティング・動きを具体的に。photorealistic or cinematic。5秒で完結する1シーン。人間の顔のクローズアップは避ける",
  "tweet": "投稿文。トレンド語を自然に含め、話しかける口調で興味を引く一言＋ハッシュタグ2個（トレンド語 と #AI動画）。全体100字以内"
}}
"""


def fetch_trends(limit: int = 10) -> list[dict]:
    """GoogleトレンドRSSをパースして [{title, traffic, news:[...]}] を返す。"""
    resp = requests.get(TRENDS_RSS, timeout=15)
    resp.raise_for_status()
    ns = {"ht": "https://trends.google.com/trending/rss"}
    root = ET.fromstring(resp.content)
    trends = []
    for item in root.iter("item"):
        title = item.findtext("title") or ""
        traffic = item.findtext("ht:approx_traffic", namespaces=ns) or ""
        news = [
            n.findtext("ht:news_item_title", namespaces=ns) or ""
            for n in item.findall("ht:news_item", ns)
        ]
        trends.append({"title": title, "traffic": traffic, "news": news[:2]})
        if len(trends) >= limit:
            break
    return trends


def load_posted_trends() -> list[str]:
    if TREND_LOG.exists():
        return [e["trend"] for e in json.loads(TREND_LOG.read_text())]
    return []


def plan_video(trends: list[dict]) -> dict:
    """Geminiにトレンド一覧を渡し、企画JSONを受け取る。"""
    posted = set(load_posted_trends())
    fresh = [t for t in trends if t["title"] not in posted]
    trends_text = "\n".join(
        f"- {t['title']}（検索数{t['traffic']}）: " + " / ".join(t["news"])
        for t in (fresh or trends)
    )
    resp = None
    for attempt in range(5):
        resp = requests.post(
            f"{GEMINI_URL}?key={os.environ['GEMINI_API_KEY']}",
            json={
                "contents": [
                    {"parts": [{"text": PLANNER_PROMPT.format(trends=trends_text)}]}
                ],
                "generationConfig": {"temperature": 0.9},
            },
            timeout=60,
        )
        if resp.status_code < 500:
            break
        time.sleep(2 ** attempt * 5)
    resp.raise_for_status()
    text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"Gemini returned no JSON: {text[:200]}")
    return json.loads(match.group(0))


def generate_kling_video(prompt: str, out_path: Path, duration: str = "5") -> Path:
    """fal AI キュー経由で Kling text-to-video を実行し mp4 を保存する。"""
    headers = {
        "Authorization": f"Key {os.environ['FAL_KEY']}",
        "Content-Type": "application/json",
    }
    submit = requests.post(
        f"{FAL_QUEUE}/{KLING_MODEL}",
        headers=headers,
        json={
            "prompt": prompt,
            "duration": duration,
            "aspect_ratio": "9:16",
            "negative_prompt": "blur, distort, low quality, watermark, text, logo",
            "cfg_scale": 0.5,
        },
        timeout=30,
    )
    submit.raise_for_status()
    job = submit.json()
    status_url = job["status_url"]
    response_url = job["response_url"]
    print(f"[kling] queued: {job['request_id']}")
    deadline = time.time() + 15 * 60
    while time.time() < deadline:
        time.sleep(10)
        status = requests.get(status_url, headers=headers, timeout=15).json()
        state = status.get("status")
        if state == "COMPLETED":
            break
        if state in ("FAILED", "CANCELLED"):
            raise RuntimeError(f"kling job {state}: {status}")
        print(f"[kling] {state} (queue={status.get('queue_position', '-')})")
    else:
        raise TimeoutError("kling generation did not finish in 15min")
    result = requests.get(response_url, headers=headers, timeout=30).json()
    video_url = result["video"]["url"]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(video_url, stream=True, timeout=600) as r:
        r.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(1024 * 1024):
                f.write(chunk)
    print(f"[kling] saved: {out_path}")
    return out_path


def post_to_x(video_path: Path, tweet_text: str) -> str:
    auth = tweepy.OAuth1UserHandler(
        os.environ["X_API_KEY"],
        os.environ["X_API_SECRET"],
        os.environ["X_ACCESS_TOKEN"],
        os.environ["X_ACCESS_TOKEN_SECRET"],
    )
    api_v1 = tweepy.API(auth)
    media = api_v1.media_upload(
        filename=str(video_path), media_category="tweet_video", chunked=True
    )
    for _ in range(30):
        status = api_v1.get_media_upload_status(media.media_id)
        info = getattr(status, "processing_info", None) or {}
        state = info.get("state", "succeeded")
        if state == "succeeded":
            break
        if state == "failed":
            raise RuntimeError(f"video processing failed: {info}")
        time.sleep(info.get("check_after_secs", 5))
    client = tweepy.Client(
        consumer_key=os.environ["X_API_KEY"],
        consumer_secret=os.environ["X_API_SECRET"],
        access_token=os.environ["X_ACCESS_TOKEN"],
        access_token_secret=os.environ["X_ACCESS_TOKEN_SECRET"],
    )
    resp = client.create_tweet(text=tweet_text, media_ids=[media.media_id_string])
    return resp.data["id"]


def log_trend(plan: dict, tweet_id: str) -> None:
    entries = json.loads(TREND_LOG.read_text()) if TREND_LOG.exists() else []
    entries.append({
        "trend": plan.get("chosen_trend", ""),
        "concept": plan.get("concept", ""),
        "tweet_id": tweet_id,
        "posted_at": datetime.now().isoformat(timespec="seconds"),
    })
    TREND_LOG.write_text(json.dumps(entries, ensure_ascii=False, indent=1))


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    trends = fetch_trends()
    print("[trend] top trends:", ", ".join(t["title"] for t in trends[:5]))
    plan = plan_video(trends)
    print(f"[plan] trend: {plan.get('chosen_trend')} (fallback={plan.get('fallback')})")
    print(f"[plan] concept: {plan.get('concept')}")
    print(f"[plan] tweet: {plan.get('tweet')}")
    if dry_run:
        print(f"[plan] kling_prompt: {plan.get('kling_prompt')}")
        print("----- DRY RUN: 動画生成せず終了 -----")
        return
    name = f"trend_{datetime.now():%Y%m%d_%H%M}"
    video = generate_kling_video(plan["kling_prompt"], OUTPUT_DIR / f"{name}.mp4")
    tweet_id = post_to_x(video, plan["tweet"])
    log_trend(plan, tweet_id)
    print(f"[done] posted: https://x.com/i/status/{tweet_id}")


if __name__ == "__main__":
    main()
