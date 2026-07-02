"""楽天お得速報ボット — エントリポイント

使い方:
  python3 main.py run            # 発掘 → サイト更新 → X投稿1件（フル実行）
  python3 main.py run --dry-run  # 投稿せず本文プレビューのみ
  python3 main.py scan           # 発掘結果の一覧表示のみ
  python3 main.py site           # サイト生成のみ（投稿なし）
"""

from __future__ import annotations

import argparse
import random
import subprocess
import sys
import os

from deals import active_keywords, find_deals
from poster import build_tweet, build_reply, post_deal
from site_builder import write_site

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def pick_keywords(n: int = 6) -> list[str]:
    """API呼び出し数を抑えるため、実行毎に一部キーワードだけ検索する。"""
    pool = active_keywords()
    rnd = random.Random()  # 実行毎にランダム（時間帯で商品が変わる）
    return rnd.sample(pool, min(n, len(pool)))


def publish_site() -> None:
    """docs/ を git commit & push して GitHub Pages を更新する。"""
    try:
        subprocess.run(["git", "add", "docs/"], cwd=BASE_DIR, check=True)
        diff = subprocess.run(
            ["git", "diff", "--cached", "--quiet"], cwd=BASE_DIR
        )
        if diff.returncode == 0:
            print("[site] no changes to publish")
            return
        subprocess.run(
            ["git", "commit", "-m", "chore: update deals page"],
            cwd=BASE_DIR, check=True,
        )
        subprocess.run(["git", "push"], cwd=BASE_DIR, check=True)
        print("[site] published to GitHub Pages")
    except subprocess.CalledProcessError as e:
        print(f"[WARN] site publish failed: {e}")


def cmd_scan() -> None:
    deals = find_deals(pick_keywords())
    for d in deals[:15]:
        print(
            f"score={d.score:5.1f} -{d.discount_pct:4.1f}% "
            f"{d.price:>7,}円 (最高{d.price_max:,}円) "
            f"rev{d.review_count}({d.review_avg}) | {d.name}"
        )
    print(f"\n{len(deals)} candidates")


def cmd_site() -> None:
    deals = find_deals(active_keywords())
    path = write_site(deals)
    print(f"[site] generated: {path} ({min(len(deals), 60)} items)")
    publish_site()


def cmd_run(dry_run: bool) -> None:
    deals = find_deals(pick_keywords())
    if not deals:
        print("[run] no deals found this time")
        return
    best = deals[0]
    # 同一商品の別ショップ出品が2位に来がちなので、名前が明確に違うものを選ぶ
    runner_up = next(
        (d for d in deals[1:] if d.name[:12] != best.name[:12]), None
    )
    print(f"[run] best deal: -{best.discount_pct}% {best.name}")
    if dry_run:
        print(build_tweet(best))
        print(build_reply(runner_up))
        return
    tweet_id = post_deal(best, runner_up)
    print(f"[run] posted: https://x.com/i/status/{tweet_id}")
    # サイトも同時更新（全キーワードで再検索すると重いので今回の結果を使う）
    write_site(deals)
    publish_site()


def main() -> None:
    parser = argparse.ArgumentParser(description="楽天お得速報ボット")
    parser.add_argument("command", choices=["run", "scan", "site"])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.command == "scan":
        cmd_scan()
    elif args.command == "site":
        cmd_site()
    else:
        cmd_run(args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
