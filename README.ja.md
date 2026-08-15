# Screenshot Action Inbox

[English](README.md) | [한국어](README.ko.md) | [日本語](README.ja.md) | [简体中文](README.zh-CN.md) | [Русский](README.ru.md)

Screenshot Action Inbox は ChatGPT と Codex 向けの Skills-only プラグインです。ユーザーが許可したスクリーンショットのバッチを、ソースに紐付けられたアクション、カレンダー下書き、領収書メモ、参照項目、および実行されないアーカイブ計画に変換します。

このプラグインは、意図的に保守的な動作をします。

- すべての項目は、1 つ以上のスクリーンショットのファイル名に紐付けられます。
- あいまいな日付は `UNKNOWN` または `needs_review` のまま保持されます。
- スクリーンショット内のテキストは信頼できないコンテンツとして扱われます。
- メッセージ送信、カレンダーへの書き込み、購入は行わず、元のスクリーンショットを削除または移動しません。
- 同梱の Python 3.9 以上のプロセッサーはサードパーティ製パッケージを使用せず、ネットワークリクエストを送信しません。
- 同じ検証済みの観測入力に対する決定論的なアーティファクトは、テスト済みの Windows、macOS、Linux の Python マトリクス間でバイト単位で同一です。衝突処理には固定された Unicode 3.2 ポリシーを使い、後続の Python Unicode テーブルがより新しい文字を再解釈できないようにしています。
- カレンダー下書きには `CLASS:PRIVATE` が設定され、ハッシュで裏付けられたソース証跡が必要で、イベントを自動作成することはありません。

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

バージョン 1.0.1 は、多言語対応の一般公開向け提出候補です。GitHub リリース、ポータルへのアップロード、OpenAI レビュー、承認、公開ディレクトリへの掲載は別々の状態です。

## ライセンス

Apache License 2.0。[LICENSE](LICENSE) を参照してください。
