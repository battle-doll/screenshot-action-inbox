# Screenshot Action Inbox

[English](README.md) | [한국어](README.ko.md) | [日本語](README.ja.md) | [简体中文](README.zh-CN.md) | [Русский](README.ru.md)

散らばったスクリーンショットを、出典付きで確認可能な次のアクションに変換します。Screenshot Action Inbox は、会議のキャプチャ、招待状、領収書、リマインダー、参照カードを保存しており、重要な内容、重複、各提案の根拠画像を把握したい人向けです。

## 使い方

1. [ChatGPT の Screenshot Action Inbox](https://chatgpt.com/plugins/plugins_6a7cbf30f0208191b29866d20a69743a)を開きます。
2. ユーザーが許可したスクリーンショットのバッチ、フォルダー、または ZIP を渡します。
3. 出典付きアクション、日付、重複グループ、不確実性フラグ、または確認用カレンダー下書きを依頼します。

## 試してみる

- `この会議スクリーンショットを、元ファイル名付きのタスクリストにして。`
- `重複したアクションをまとめ、確認が必要な日付を示して。`
- `Turn these screenshots into sourced actions and show what needs review.`

## 重要な境界

- 各項目は 1 つ以上の元ファイル名に紐付き、あいまいな事実は `UNKNOWN` または `needs_review` のままです。
- 画像内のテキストは信頼できないコンテンツです。監視、本人推定、単一画像の創作編集、OCR のみの文字起こし、メール整理、コード影響分析、音声通知設定には使用しません。
- メッセージ送信、実際のカレンダーイベント作成、購入、画像削除、ファイル移動は行いません。カレンダーとアーカイブの出力は下書きだけです。
- 同梱の Python 3.9 以上のプロセッサーは第三者パッケージもネットワーク要求も使用せず、同じ検証入力からテスト済みの Windows、macOS、Linux マトリクスで同一バイトの結果を生成します。

## 出力

- `weekly-digest.md`
- `actions.csv`
- `calendar.ics`
- `archive-plan.json`
- `receipt.json`

## コードオントロジー

[インタラクティブなコードオントロジーグラフ](docs/code-ontology/index.html)でリポジトリ構造を探索できます。自己完結型のワークベンチは、検索、範囲を限定した 2D 構造ビュー、任意の 3D コンステレーション、ソース証拠の確認に対応しています。GitHub のファイルビューアは HTML を実行せずソースとして表示するため、ファイルをダウンロードしてローカルのブラウザーで開いてください。

このグラフは、ソースリビジョン `b42d168b6d45213edb886b683ac5c5ec06942454` を [Code Ontology Companion](https://github.com/battle-doll/code-ontology-companion) 0.5.2 で解析して生成しました（スナップショット `20260815T090018Z-49018a955a1c`）。解析警告なしで 940 ノード、2,756 リレーションを収録しています。

グラフにはシンボル識別子、リポジトリ相対パス、行範囲、定性的な静的解析の証拠が含まれます。ソース本文、コメント、ローカル絶対パス、ソースファイル単位のフィンガープリント、認証情報、モデル出力は含まれません。リレーションはコード探索のための証拠であり、ランタイムトレース、安全性の判定、因果関係の証明ではありません。

## ローカル開発

完全な検証スイートを実行します。

macOS/Linux:

```bash
python3 -X utf8 scripts/verify.py all
```

Windows:

```powershell
py -3 -X utf8 scripts/verify.py all
```

ポータルに安全な Skills-only ZIP をビルドするには、`all` の代わりに `build` を使用します。

macOS/Linux:

```bash
python3 -X utf8 scripts/verify.py build
```

Windows:

```powershell
py -3 -X utf8 scripts/verify.py build
```

プラグインのソースは [`plugins/screenshot-action-inbox`](plugins/screenshot-action-inbox) にあります。生成されたリリースは `dist/` に書き込まれます。

## プライバシー

パブリッシャーが運用するサーバー、コネクター、アカウント、テレメトリ、アナリティクスはありません。ホスト製品は、独自の条件と保持管理に従ってユーザー提供の画像を処理します。決定論的なプロセッサーは、画像ファイルではなく構造化 JSON を受け取ります。[PRIVACY.md](PRIVACY.md) を参照してください。

## ステータス

2026-08-29 の確認では、バージョン 1.0.2 は OpenAI Platform で **Published** となり、公開ディレクトリの完全一致名検索結果に表示されます。[直接ディレクトリ URL](https://chatgpt.com/plugins/plugins_6a7cbf30f0208191b29866d20a69743a)から開けます。これは公開と完全一致名での検索表示を確認したものであり、自動 selector 呼び出しや、より広いクエリでの routing 成功率は測定していません。

このリポジトリのバージョン 1.0.2 は、その公開済みアップデートの source version です。

## ライセンス

Apache License 2.0。[LICENSE](LICENSE) を参照してください。
