# 楽天お得速報ボット

楽天市場APIで「過去の実売価格より実際に値下がりした高評価商品」を自動発掘し、

1. アフィリンク付きでXに自動投稿（1日3回）
2. GitHub Pages のまとめサイトを自動更新

する完全自動の収益化パイプライン。

## 仕組み

```
deals.py     … 楽天API検索 → itemPriceMax3 と現在価格の差で"本当の値下げ"だけ抽出・スコアリング
poster.py    … 140字投稿文の生成 + 画像付きツイート + 重複投稿防止ログ
site_builder.py … docs/index.html（全カードがアフィリンクのまとめページ）生成
main.py      … CLI（run / scan / site）
```

## コマンド

```bash
python3 main.py scan           # 候補一覧を見るだけ
python3 main.py run --dry-run  # 投稿文プレビュー
python3 main.py run            # 発掘→X投稿→サイト更新→push（フル実行）
python3 main.py site           # サイト更新のみ
```

## 自動実行

launchd（`com.rakuten.deals.plist`）で毎日 7:30 / 12:30 / 20:30 に `main.py run` を実行。

```bash
launchctl load ~/Library/LaunchAgents/com.rakuten.deals.plist   # 有効化
launchctl unload ~/Library/LaunchAgents/com.rakuten.deals.plist # 停止
```

ログ: `logs/run.log`

## 選定基準（deals.py）

- 過去最高値（itemPriceMax2/3）から **15%以上値下がり**
- 価格 1,980円以上（報酬単価の確保）
- レビュー50件以上・評価4.0以上
- スコア = 割引率 + log10(レビュー数)×6 + アフィ報酬率×3 + (評価-4.0)×10

## セットアップ

`.env` に楽天API・XのAPIキーを設定（`.env` は git 管理外）。
