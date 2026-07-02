"""お得情報まとめページ (docs/index.html) を生成する。

GitHub Pages で公開し、X のプロフィールや投稿から誘導する受け皿。
ページ上の全カードがアフィリンクなので、どこをクリックされても成果になる。
"""

from __future__ import annotations

import html
import json
import os
from datetime import datetime

from deals import Deal

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOCS_DIR = os.path.join(BASE_DIR, "docs")
DEALS_JSON = os.path.join(DOCS_DIR, "deals.json")

CSS = """
:root{--bg:#0f1117;--card:#1a1e2a;--accent:#ff4757;--gold:#ffb400;--text:#eef0f6;--sub:#9aa3b5}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Hiragino Sans','Noto Sans JP',sans-serif;background:var(--bg);color:var(--text)}
header{padding:34px 18px 22px;text-align:center;background:linear-gradient(135deg,#bf0f30,#ff4757 60%,#ff7b54)}
header h1{font-size:1.5rem;letter-spacing:.02em}
header p{margin-top:8px;font-size:.85rem;opacity:.92}
.updated{text-align:center;color:var(--sub);font-size:.75rem;padding:12px 0 2px}
main{max-width:1080px;margin:0 auto;padding:18px;display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:14px}
.card{background:var(--card);border-radius:14px;overflow:hidden;display:flex;flex-direction:column;text-decoration:none;color:inherit;transition:transform .15s,box-shadow .15s;position:relative}
.card:hover{transform:translateY(-3px);box-shadow:0 8px 24px rgba(0,0,0,.45)}
.badge{position:absolute;top:10px;left:10px;background:var(--accent);color:#fff;font-weight:700;font-size:.85rem;padding:4px 10px;border-radius:999px}
.thumb{width:100%;aspect-ratio:1;object-fit:cover;background:#fff}
.body{padding:12px 14px 14px;display:flex;flex-direction:column;gap:6px;flex:1}
.name{font-size:.85rem;line-height:1.45;min-height:3.6em}
.price{font-size:1.25rem;font-weight:800;color:var(--gold)}
.price s{font-size:.8rem;color:var(--sub);font-weight:400;margin-left:6px}
.meta{font-size:.72rem;color:var(--sub)}
.cta{margin-top:auto;text-align:center;background:var(--accent);border-radius:8px;padding:8px;font-size:.85rem;font-weight:700}
footer{text-align:center;color:var(--sub);font-size:.72rem;padding:26px 16px 40px;line-height:1.8}
"""


def render_card(d: dict) -> str:
    e = html.escape
    saved = d["price_max"] - d["price"]
    return f"""
<a class="card" href="{e(d['affiliate_url'])}" target="_blank" rel="noopener sponsored">
  <span class="badge">-{d['discount_pct']:.0f}%</span>
  <img class="thumb" src="{e(d['image_url'])}" alt="{e(d['name'])}" loading="lazy">
  <div class="body">
    <div class="name">{e(d['name'])}</div>
    <div class="price">{d['price']:,}円<s>{d['price_max']:,}円</s></div>
    <div class="meta">⭐{d['review_avg']}（{d['review_count']:,}件）｜{saved:,}円お得｜{e(d['shop_name'])}</div>
    <div class="cta">楽天で見る ▶</div>
  </div>
</a>"""


def build_page(deals: list[dict]) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    cards = "\n".join(render_card(d) for d in deals)
    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>今日の楽天お得速報｜実売価格が下がった商品だけ</title>
<meta name="description" content="楽天市場で過去の販売価格より実際に値下がりした商品だけを毎日自動で発掘。レビュー高評価のものだけ厳選。">
<style>{CSS}</style>
</head>
<body>
<header>
  <h1>🔥 今日の楽天お得速報</h1>
  <p>過去の実売価格より値下がりした高評価商品だけを自動で厳選</p>
</header>
<div class="updated">最終更新: {now}（毎日自動更新）</div>
<main>
{cards}
</main>
<footer>
  当サイトは楽天アフィリエイトを利用しています（リンクはPRを含みます）。<br>
  価格・割引率は取得時点の楽天API情報に基づきます。最新の価格は遷移先でご確認ください。
</footer>
</body>
</html>"""


def write_site(deals: list[Deal], max_items: int = 60) -> str:
    os.makedirs(DOCS_DIR, exist_ok=True)
    data = [d.to_dict() for d in deals[:max_items]]
    with open(DEALS_JSON, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    path = os.path.join(DOCS_DIR, "index.html")
    with open(path, "w") as f:
        f.write(build_page(data))
    return path
