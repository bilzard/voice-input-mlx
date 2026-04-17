# voice-input-mlx

## これは何？

* Apple Silicon (Metal) に最適化された、Mac専用の高速・完全ローカル音声入力ツール。

> [!NOTE]
> [xuiltul/voice-input](https://github.com/xuiltul/voice-input) をベースに、LLM による文章整形や画面解析などの機能をあえて排除し、`mlx-whisper` による推論速度の最小化と入力の即時性に特化して再構築されたプロジェクトです。

## 主な特徴

* **MLX & Metal対応**: Apple SiliconのGPU性能を最大限に引き出すことで、従来のWhisperよりも高速な処理が可能。
* **バックグラウンド常駐**: launchd を利用したデーモンとして動作。ホットキー一つで、どのアプリからでも即座に呼び出せる。
* **スピード特化（No Fluff）**: 解釈用の言語モデル（LLM）を通さない「ストレートな出力」に特化し、待ち時間を短縮。
* **セキュア**: 全ての処理がローカルで完結するため、音声データが外部に流出する心配がない。

## 操作方法

押しっぱなしではなくトグル操作です。

1. Hotkey (デフォルト: `F13`; 変更可) を1回押すと録音開始。
2. 喋り終わってもう一度押すと録音終了。
3. 数秒待つとテキストがカーソル位置に自動入力される（カーソルが外れた時のためにクリップボードにもコピーされます）。

## 技術スタックと仕組み
- **Backend (`ws_server.py`)**:
    - **デフォルトで** `mlx-whisper` (large-v3-turbo) を使用（設定で変更可能）。
    - 初回起動時にダミーデータを推論させ、Metalシェーダーのコンパイル（JIT）を済ませることで、初回の認識遅延を防止。
- **Frontend (`mac_client.py`)**:
    - `pynput` によるグローバルホットキー監視（デフォルト: F13）。
    - 認識結果は `pbcopy` 経由でクリップボードにセットし、`System Events` で `Cmd+V` をエミュレートして貼り付け。
- **Infrastructure**:
    - `launchd` によるサービス管理（ログイン時自動起動、異常終了時の自動再起動）。
    - `newsyslog` によるログ管理（1MBごとの自動圧縮・5世代ローテーション）。

## パフォーマンス
M1/M2/M3 以降のチップにおいて、以下の処理効率を実現しています。
- **推論効率**: 音声の長さに対して約 **10% 〜 20%** の時間で処理完了（10秒の音声なら1〜2秒）。
- **メモリ使用量**: 約 1.5 GB（large-v3-turbo モデルロード時）。

## 導入方法

### 依存関係
- macOS (Apple Silicon)
- Python 3.11+
- ffmpeg (`brew install ffmpeg`)
- [uv](https://docs.astral.sh/uv/)

### セットアップ

```bash
git clone https://github.com/your-repo/voice-input-mlx
cd voice-input-mlx
# 依存関係・自動起動・ログ管理を含む一括インストール
bash install.sh
```

### ⚠️ 権限設定（重要）
本ツールはホットキーの監視と自動入力を行うため、macOSの「アクセシビリティ」権限が必要です。実行環境に合わせて、以下のアプリケーションを許可リストに追加してください。

- **自動起動（plist）で利用する場合**
  - `/opt/homebrew/bin/uv` (またはお使いの `uv` のパス)
- **ターミナルから直接実行する場合**
  - `iTerm.app` / `Terminal.app` / `Visual Studio Code.app` など

> [!NOTE]
> **もし許可しても動かない場合：**
> macOSの仕様により、仮想環境内のPython本体に権限が必要な場合があります。その際は `open .venv/bin` で開いたフォルダにある `python` ファイルをアクセシビリティ設定に直接追加してください。

## カスタマイズと運用

### ホットキーの変更
デフォルトのホットキーは `F13` に設定されています。
MacBook単体で利用する場合などは、環境変数 `VOICE_INPUT_HOTKEY` を書き換えることで任意のキーに変更可能です。

1. `.env` ファイル内の値を編集します。
  ```bash
  # 例: F10 キーに変更する場合
  VOICE_INPUT_HOTKEY="f10"
  ```
2. 設定を反映させるため、サービスを再起動します。
  ```bash
  # install.sh を再実行するか、プロセスを kill すれば自動再起動されます
  pkill -f mac_client.py
  ```

その他のカスタマイズ可能な環境変数は`.env.example`を参照してください。

### ログの確認（トラブルシューティング）
バックグラウンドで動作している際のリアルタイムな処理状況やエラーは、以下のコマンドで確認できます。

```bash
tail -f ~/Library/Logs/voice-input-mlx/voice_input.log
```

## Benchmark

技術用語を含む約20秒の音声入力における、推論速度と認識精度の比較データです。

**Test Environment:**
- **Hardware:** MacBook Air (M2, 2022) / 16GB RAM
- **Sample Audio:** 以下の「正解文」を読み上げた音声

| Model | Audio Length | Processing Time | RTF | Transcription Output (比較) |
| :--- | :---: | :---: | :---: | :--- |
| **(正解文)** | - | | | Whisper large-v3-turboは、従来のlarge-v2と比較してデコーダーの層数が大幅に削減されています。これにより、4bit量子化に頼らずとも、Apple Silicon上でリアルタイム係数0.1以下の驚異的な速度を実現可能です。 |
| **`mlx-community/whisper-small-mlx`** | 22.0s | 1.7s  | **0.077** | ウィスパーラージV3ターボは、従来のラージV2と比較して、デコーダーの総数が大幅に削減されています。これにより**4ビット量しか**に頼らずとも、アップルシリコン上で**リアルタイムケース**0.1以下の**脅威的な**速度を実現可能です。 |
| **`mlx-community/whisper-large-v3-turbo`** | 19.2s | 2.6s | **0.135** | **Wisper Large V3 Turbo**は、従来の**Large V2**と比較して、デコーダーの総数が大幅に削減されています。これにより、**4bit量子化**に頼らずとも、**Apple Silicon**上で**リアルタイム係数**0.1以下の**驚異的な**速度を実現可能です。 |

> [!NOTE]
> **RTF (Real Time Factor)**: 処理時間 ÷ 音声の長さ。数値が低いほど高速。
> ※ RTF 0.1 前後（20秒の音声を2秒で処理）がストレスなく利用できる目安です。

## Credits & License

### Acknowledgements
本プロジェクトは、以下のリポジトリをベース、または着想を得て開発されました。
- [xuiltul/voice-input](https://github.com/xuiltul/voice-input) - 元の設計およびロジックのベースとして。

### License
このプロジェクトは **MIT License** の下で公開されています。
元のプロジェクトのライセンスに基づき、著作権表示および許諾表示は `LICENSE` ファイルに維持されています。
