"""トレンド連動Kling動画ボット。

Googleトレンド(日本)から今バズっている話題を取得し、
Gemini が「映像映えする企画」を選定して動画プロンプトを作成、
fal AI 経由の text-to-video で高品質AI動画を生成して X に投稿する。

実在の人物がトレンドの場合はスキップする（ディープフェイク回避）。

モデル: Veo 3.1 Lite（メイン） / Kling 2.5 Turbo Pro（フォールバック）
  - Veo 3.1 Lite 720p 音声付き $0.05/秒 → 8秒 $0.40/本
  - Kling 2.5 Turbo Pro 無音 $0.07/秒 → 5秒 $0.35/本
  Veoの方が1秒あたり安く、環境音・効果音付きで尺も8秒取れる。
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
# Veo 3.1 Lite: 720p音声付き $0.05/秒（8秒=$0.40）。Kling 2.5比で安く、音声も出る。
VIDEO_MODEL = "fal-ai/veo3.1/lite"
FALLBACK_MODEL = "fal-ai/kling-video/v2.5-turbo/pro/text-to-video"
FAL_QUEUE = "https://queue.fal.run"

PLANNER_PROMPT = """あなたはXで数万リポストを狙う短尺AI動画の企画者です。

# 大原則
「綺麗な映像」は誰も見ない。見た人の感情が動いて初めて拡散される。
狙う感情は次のどれか:
  A. 笑い（シュール・ありえない状況を大真面目にやる）
  B. かわいい＋意外性（動物が人間くさいことを完璧にこなす）
  C. ゾクッとする没入感（ドキュメンタリー風のありえない世界）

# 実績のある鉄板フォーマット（この型に当てはめる）
- 動物の日常vlog風: カピバラが温泉旅館の女将をしている、猫がラーメン屋を営んでいる、
  柴犬がサラリーマンとして満員電車に乗っている、ペンギンがコンビニ夜勤をしている 等。
  手持ちスマホ撮影風・生活音つきだと「本物感」が出て伸びる
- 大真面目なNHKドキュメンタリー風ナレーション空間: 巨大な食べ物が街に鎮座している、
  ありえない生態の生き物を観察している 等
- 「最後まで見ちゃう」系: 何かが起こりそうで起こる寸前で終わる、綺麗なループになる

# 今日の日本のGoogle検索トレンド（味付け用・使わなくてよい）
{trends}

# 過去投稿の実績フィードバック（最重要の判断材料）
{feedback}

ヒット企画がある場合は、同じ主役キャラ・同じ世界観の「別エピソード」を作ること。
続き物になるとフォローする理由が生まれる。元プロンプトの主役の見た目・世界観の
記述をできるだけ再利用して、シリーズとしての一貫性を保て。
ただしエピソードの中身（やっている事・オチ）は毎回変えること。

# タスク
上の鉄板フォーマット（＋実績フィードバック）で8秒動画の企画を1本作る。
- トレンドの中に「面白く絡められる話題」があれば絡める（例: 花火大会がトレンド→
  カピバラ一家が浴衣で花火を見ている）。無理やり絡めるくらいなら無視して純粋に面白い企画にする
- 実在の人物・芸能人は絶対に出さない。事件・事故・災害も扱わない
- 主役は動物または無生物。人間の顔は出さない

# 出力形式（JSONのみ。コードブロック記号や説明は一切不要）
{{
  "chosen_trend": "絡めたトレンド語（絡めない場合は空文字）",
  "series": "続編を作った場合は元企画の一言説明をそのまま書く。新規企画なら空文字",
  "concept": "企画の一言説明（日本語）",
  "video_prompt": "動画生成AIへの英語プロンプト。①主役と状況 ②具体的な動作の流れ（8秒で起承転結） ③カメラワーク（handheld smartphone vlog style 等） ④環境音・効果音 を必ず含める。photorealistic",
  "tweet": "投稿文。状況を一言でツッコむ日本語（例:『温泉旅館の女将、カピバラだった』）。説明しすぎない。ハッシュタグは #AI動画 と、トレンドを絡めた場合のみそのタグ。全体80字以内"
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


def load_log() -> list[dict]:
    if TREND_LOG.exists():
        return json.loads(TREND_LOG.read_text())
    return []


def save_log(entries: list[dict]) -> None:
    TREND_LOG.write_text(json.dumps(entries, ensure_ascii=False, indent=1))


def load_posted_trends() -> list[str]:
    return [e["trend"] for e in load_log() if e.get("trend")]


def engagement_score(m: dict) -> float:
    """拡散に直結する指標を重めに評価した総合スコア。"""
    return (
        m.get("retweet_count", 0) * 5.0
        + m.get("quote_count", 0) * 5.0
        + m.get("reply_count", 0) * 4.0
        + m.get("like_count", 0) * 3.0
        + m.get("bookmark_count", 0) * 3.0
        + m.get("impression_count", 0) * 0.01
    )


def update_engagement() -> list[dict]:
    """過去投稿のメトリクスを1回のAPI呼び出しで回収してログに書き戻す。

    X Freeプランは読み取り回数が月100回程度と少ないため、
    実行毎に最大100件をまとめて1リクエストで取る。
    """
    entries = load_log()
    targets = [e for e in entries if e.get("tweet_id")][-100:]
    if not targets:
        return entries
    client = tweepy.Client(
        consumer_key=os.environ["X_API_KEY"],
        consumer_secret=os.environ["X_API_SECRET"],
        access_token=os.environ["X_ACCESS_TOKEN"],
        access_token_secret=os.environ["X_ACCESS_TOKEN_SECRET"],
    )
    try:
        resp = client.get_tweets(
            [e["tweet_id"] for e in targets],
            tweet_fields=["public_metrics"],
            user_auth=True,
        )
    except Exception as e:
        print(f"[WARN] engagement fetch failed: {e}")
        return entries
    by_id = {str(t.id): t.public_metrics for t in (resp.data or [])}
    for e in entries:
        m = by_id.get(str(e.get("tweet_id")))
        if m:
            e["metrics"] = m
            e["score"] = round(engagement_score(m), 2)
    save_log(entries)
    return entries


def build_feedback(entries: list[dict]) -> str:
    """ヒット/不発の実績をGeminiへのフィードバック文にする。

    投稿から6時間未満のものはまだ数字が育っていないので評価対象外。
    """
    cutoff = datetime.now().timestamp() - 6 * 3600
    evaluated = [
        e for e in entries
        if e.get("score") is not None
        and datetime.fromisoformat(e["posted_at"]).timestamp() < cutoff
    ]
    if not evaluated:
        return "（まだ実績データなし。鉄板フォーマットから自由に企画してよい）"
    evaluated.sort(key=lambda e: e["score"], reverse=True)
    lines = []
    hits = [e for e in evaluated if e["score"] >= 10][:3]
    flops = [e for e in evaluated if e["score"] < 1][-3:]
    if hits:
        lines.append("## ヒットした企画（この世界観の続編・別エピソードを最優先で作れ）")
        for e in hits:
            lines.append(f"- 「{e['concept']}」 score={e['score']}")
            if e.get("video_prompt"):
                lines.append(f"  元プロンプト: {e['video_prompt'][:200]}")
    if flops:
        lines.append("## 反応が無かった企画（似た方向は避けろ）")
        for e in flops:
            lines.append(f"- 「{e['concept']}」")
    return "\n".join(lines) if lines else "（まだ明確なヒットなし。新しい切り口を試せ）"


def plan_video(trends: list[dict], feedback: str) -> dict:
    """Geminiにトレンド一覧と実績フィードバックを渡し、企画JSONを受け取る。"""
    posted = set(load_posted_trends())
    fresh = [t for t in trends if t["title"] not in posted]
    trends_text = "\n".join(
        f"- {t['title']}（検索数{t['traffic']}）: " + " / ".join(t["news"])
        for t in (fresh or trends)
    )
    prompt = PLANNER_PROMPT.format(trends=trends_text, feedback=feedback)
    resp = None
    for attempt in range(5):
        resp = requests.post(
            f"{GEMINI_URL}?key={os.environ['GEMINI_API_KEY']}",
            json={
                "contents": [{"parts": [{"text": prompt}]}],
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


def _model_arguments(model: str, prompt: str) -> dict:
    if "veo" in model:
        return {
            "prompt": prompt,
            "aspect_ratio": "9:16",
            "duration": "8s",
            "resolution": "720p",
            "generate_audio": True,
        }
    return {  # Kling系
        "prompt": prompt,
        "duration": "5",
        "aspect_ratio": "9:16",
        "negative_prompt": "blur, distort, low quality, watermark, text, logo",
        "cfg_scale": 0.5,
    }


def generate_video(prompt: str, out_path: Path) -> Path:
    """fal AI キュー経由で text-to-video を実行し mp4 を保存する。

    メインモデル（Veo 3.1 Lite）が失敗した場合は Kling にフォールバック。
    """
    headers = {
        "Authorization": f"Key {os.environ['FAL_KEY']}",
        "Content-Type": "application/json",
    }
    job = None
    for model in (VIDEO_MODEL, FALLBACK_MODEL):
        submit = requests.post(
            f"{FAL_QUEUE}/{model}",
            headers=headers,
            json=_model_arguments(model, prompt),
            timeout=30,
        )
        if submit.ok:
            job = submit.json()
            print(f"[video] model: {model}")
            break
        print(f"[WARN] {model} submit failed: HTTP {submit.status_code} {submit.text[:200]}")
    if job is None:
        raise RuntimeError("all video models failed to accept the job")
    status_url = job["status_url"]
    response_url = job["response_url"]
    print(f"[video] queued: {job['request_id']}")
    deadline = time.time() + 15 * 60
    while time.time() < deadline:
        time.sleep(10)
        status = requests.get(status_url, headers=headers, timeout=15).json()
        state = status.get("status")
        if state == "COMPLETED":
            break
        if state in ("FAILED", "CANCELLED"):
            raise RuntimeError(f"video job {state}: {status}")
        print(f"[video] {state} (queue={status.get('queue_position', '-')})")
    else:
        raise TimeoutError("video generation did not finish in 15min")
    result = requests.get(response_url, headers=headers, timeout=30).json()
    video_url = result["video"]["url"]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(video_url, stream=True, timeout=600) as r:
        r.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(1024 * 1024):
                f.write(chunk)
    print(f"[video] saved: {out_path}")
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
    entries = load_log()
    entries.append({
        "trend": plan.get("chosen_trend", ""),
        "concept": plan.get("concept", ""),
        "series": plan.get("series", ""),
        "video_prompt": plan.get("video_prompt", ""),
        "tweet_id": tweet_id,
        "posted_at": datetime.now().isoformat(timespec="seconds"),
    })
    save_log(entries)


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    trends = fetch_trends()
    print("[trend] top trends:", ", ".join(t["title"] for t in trends[:5]))
    entries = update_engagement()
    feedback = build_feedback(entries)
    print(f"[feedback]\n{feedback}")
    plan = plan_video(trends, feedback)
    print(f"[plan] trend: {plan.get('chosen_trend') or '(なし)'} series: {plan.get('series') or '(新規)'}")
    print(f"[plan] concept: {plan.get('concept')}")
    print(f"[plan] tweet: {plan.get('tweet')}")
    prompt = plan.get("video_prompt") or plan.get("kling_prompt")
    if dry_run:
        print(f"[plan] video_prompt: {prompt}")
        print("----- DRY RUN: 動画生成せず終了 -----")
        return
    name = f"trend_{datetime.now():%Y%m%d_%H%M}"
    video = generate_video(prompt, OUTPUT_DIR / f"{name}.mp4")
    tweet_id = post_to_x(video, plan["tweet"])
    log_trend(plan, tweet_id)
    print(f"[done] posted: https://x.com/i/status/{tweet_id}")


if __name__ == "__main__":
    main()
