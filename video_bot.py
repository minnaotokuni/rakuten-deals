"""フォロワー獲得用「財布の本音」動画ボット。

毎日1本、財布キャラが節約について説教する縦動画を自動生成してXに投稿する。
アフィリンクは貼らない（純粋なエンタメ枠でフォロワーを増やし、
プロフィールと通常投稿のお得情報に流す設計）。

パイプライン:
  Gemini(台本生成) → VOICEVOX(音声) → 既存TikTokパイプライン(動画合成) → X投稿
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
import tweepy
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

TIKTOK_DIR = Path("/Users/watanabetakuya/Desktop/全自動SNS TikTok")
VIDEO_PY = TIKTOK_DIR / "make_video.py"
VENV_PY = TIKTOK_DIR / ".venv" / "bin" / "python"
SCRIPTS_DIR = BASE_DIR / "video_scripts"
TOPICS_LOG = BASE_DIR / "video_topics_log.json"

VOICEVOX_URL = "http://127.0.0.1:50021"
VOICEVOX_ENGINE = "/Applications/VOICEVOX.app/Contents/Resources/vv-engine/run"

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-2.5-flash-lite:generateContent"
)

GEMINI_PROMPT = """あなたはTikTok/X向けのバズ動画台本作家です。
「財布」が持ち主に一人称で説教する30秒動画の台本を1本作ってください。

# 今回のテーマ
{topic}

# キャラ設定
- 一人称「俺」。持ち主を「お前」と呼ぶ、キレ気味だが根は持ち主想いの財布
- 具体的な金額を必ずセリフに入れる（リアリティが命）
- 説教だが最後は少し愛がある

# 出力形式（JSONのみ。コードブロック記号や説明文は一切不要）
{{
  "hook": "冒頭2秒で親指を止める強い一言（15字以内、疑問形か命令形）",
  "intro": "俺、お前の財布だ。",
  "body": ["セリフ1（40字前後）", "セリフ2", "セリフ3", "セリフ4"],
  "punchline": "オチの一言（皮肉＋愛）",
  "cta": "コメントを誘発する問いかけ"
}}

# 制約
- 全セリフ合計250〜330字（30秒尺）
- 話し言葉。書き言葉禁止
- 誇張はいいが嘘の統計・数字は禁止
"""

TOPIC_POOL = [
    "コンビニの「ついで買い」",
    "サブスクの解約忘れ",
    "ATMの時間外手数料",
    "セールで買った着ない服",
    "コンビニATMとキャッシュレスの使い分け",
    "「ポイント貯まるから」で増える無駄遣い",
    "自販機で毎日買う飲み物",
    "深夜のネットショッピング",
    "送料無料のためのついで買い",
    "ガチャ・課金",
    "「疲れたから」のタクシー",
    "飲み会の二次会",
    "福袋の中身",
    "使ってないジムの会費",
    "「限定」という言葉への弱さ",
    "キャッシュレスで金銭感覚が消える話",
    "冷蔵庫にあるのに買う調味料",
    "傘を何本も買う話",
]


def ensure_voicevox() -> None:
    """VOICEVOXエンジンが落ちていたらヘッドレス起動して待つ。"""
    try:
        requests.get(f"{VOICEVOX_URL}/version", timeout=3)
        return
    except requests.RequestException:
        pass
    print("[voicevox] engine not running, starting headless...")
    subprocess.Popen(
        [VOICEVOX_ENGINE, "--host", "127.0.0.1", "--port", "50021"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    for _ in range(24):
        time.sleep(5)
        try:
            requests.get(f"{VOICEVOX_URL}/version", timeout=3)
            print("[voicevox] engine ready")
            return
        except requests.RequestException:
            continue
    raise RuntimeError("VOICEVOX engine failed to start within 120s")


def load_used_topics() -> list[str]:
    if TOPICS_LOG.exists():
        return json.loads(TOPICS_LOG.read_text())
    return []


def pick_topic() -> str:
    used = load_used_topics()
    remaining = [t for t in TOPIC_POOL if t not in used]
    if not remaining:  # 一周したらリセット
        TOPICS_LOG.write_text("[]")
        remaining = TOPIC_POOL
    return remaining[0]


def mark_topic_used(topic: str) -> None:
    used = load_used_topics()
    used.append(topic)
    TOPICS_LOG.write_text(json.dumps(used, ensure_ascii=False, indent=1))


def generate_script(topic: str) -> dict:
    """Geminiで台本を生成し、動画パイプライン形式のJSONにして返す。"""
    resp = None
    for attempt in range(5):  # 503等の一時エラーはバックオフ付きで再試行
        resp = requests.post(
            f"{GEMINI_URL}?key={os.environ['GEMINI_API_KEY']}",
            json={
                "contents": [{"parts": [{"text": GEMINI_PROMPT.format(topic=topic)}]}],
                "generationConfig": {"temperature": 1.0},
            },
            timeout=60,
        )
        if resp.status_code < 500:
            break
        wait = 2 ** attempt * 5
        print(f"[gemini] HTTP {resp.status_code}, retry in {wait}s")
        time.sleep(wait)
    resp.raise_for_status()
    text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"Gemini returned no JSON: {text[:200]}")
    generated = json.loads(match.group(0))
    return {
        "character": "wallet",
        "label": "財布",
        "char_color": [193, 122, 70, 255],
        "bg_color": [20, 26, 38],
        "voice": "kurono_tsungire",
        "speed": 1.05,
        **{k: generated[k] for k in ("hook", "intro", "body", "punchline", "cta")},
    }


def render_video(script: dict, name: str) -> Path:
    """既存TikTokパイプラインで動画合成。台本は両プロジェクトに保存する。"""
    SCRIPTS_DIR.mkdir(exist_ok=True)
    local_path = SCRIPTS_DIR / f"{name}.json"
    local_path.write_text(json.dumps(script, ensure_ascii=False, indent=2))
    # make_video.py は scripts/ 配下でなくても動くのでローカルパスを渡す
    result = subprocess.run(
        [str(VENV_PY), str(VIDEO_PY), str(local_path)],
        cwd=TIKTOK_DIR,
        capture_output=True,
        text=True,
        timeout=900,
    )
    if result.returncode != 0:
        raise RuntimeError(f"video render failed:\n{result.stderr[-2000:]}")
    out = TIKTOK_DIR / "output" / f"{name}.mp4"
    if not out.exists():
        raise FileNotFoundError(f"expected output not found: {out}")
    return out


def post_video(video_path: Path, script: dict, topic: str, dry_run: bool = False) -> str | None:
    text = "\n".join([
        f"【財布の本音】{topic}編",
        "",
        script["hook"],
        "",
        "#節約 #お金の勉強 #財布の本音",
    ])
    if dry_run:
        print("----- DRY RUN -----")
        print(text)
        print(f"video: {video_path}")
        return None
    auth = tweepy.OAuth1UserHandler(
        os.environ["X_API_KEY"],
        os.environ["X_API_SECRET"],
        os.environ["X_ACCESS_TOKEN"],
        os.environ["X_ACCESS_TOKEN_SECRET"],
    )
    api_v1 = tweepy.API(auth)
    media = api_v1.media_upload(
        filename=str(video_path),
        media_category="tweet_video",
        chunked=True,
    )
    # 動画は非同期処理されるので完了を待つ
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
    resp = client.create_tweet(text=text, media_ids=[media.media_id_string])
    return resp.data["id"]


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    topic = pick_topic()
    print(f"[video] topic: {topic}")
    name = f"wallet_{datetime.now():%Y%m%d}"
    cached = SCRIPTS_DIR / f"{name}.json"
    video = TIKTOK_DIR / "output" / f"{name}.mp4"
    if cached.exists() and video.exists():
        # 同日の再実行（dry-run後の本番など）はレンダリング済みを再利用
        script = json.loads(cached.read_text())
        print(f"[video] reusing today's render: {video}")
    else:
        script = generate_script(topic)
        print(f"[video] hook: {script['hook']}")
        ensure_voicevox()
        video = render_video(script, name)
        print(f"[video] rendered: {video}")
    tweet_id = post_video(video, script, topic, dry_run=dry_run)
    if tweet_id:
        mark_topic_used(topic)
        print(f"[video] posted: https://x.com/i/status/{tweet_id}")


if __name__ == "__main__":
    main()
