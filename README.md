# voice-input-mlx

Apple Silicon (Metal) に最適化された、Mac専用の超高速・完全ローカル音声入力ツール。
[xuiltul/voice-input](https://github.com/xuiltul/voice-input) をベースに、LLM による文章整形や画面解析などの機能をあえて排除し、`mlx-whisper` による推論速度の最小化と入力の即時性に特化して再構築されたプロジェクトです。

## コンセプト
- **No Fluff**: 精度向上や付加機能を削ぎ落とし、「喋った内容をそのまま、最速で入力する」ことだけに特化。
- **Mac Native**: Apple Siliconの統合メモリとGPU（Metal）をフル活用。
- **Privacy by Design**: 音声データはネットワークに一切送信されず、すべてローカルのRAM上で処理。
- **Infrastructure as a Tool**: 起動、バックグラウンド常駐、ログ管理までを自動化し、OSの機能の一部として動作。

## 技術スタックと仕組み
- **Backend (`ws_server.py`)**:
    - `mlx-whisper` (large-v3-turbo) を使用。
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

## 操作方法
- **[F13] を押す**: 録音開始。
- **[F13] を再度押す**: 録音終了 → サーバーへ送信 → 自動入力。
- **[Ctrl] を押しながら終了**: 入力後の Enter 送信をスキップ（チャットの改行などに便利）。

### ホットキーのカスタマイズ
デフォルトのホットキーは `F13` に設定されています。
MacBook単体で利用する場合や、他のキーに割り当てたい場合は、環境変数 `VOICE_INPUT_HOTKEY` で変更できます。

**変更例（インストール前/実行時に設定）：**
```bash
# 例: 右コマンドキーに変更する場合
export VOICE_INPUT_HOTKEY="cmd_r"
```

## Credits & License

### Acknowledgements
本プロジェクトは、以下のリポジトリをベース、または着想を得て開発されました。
- [xuiltul/voice-input](https://github.com/xuiltul/voice-input) - 元の設計およびロジックのベースとして。

### License
このプロジェクトは **MIT License** の下で公開されています。
元のプロジェクトのライセンスに基づき、著作権表示および許諾表示は `LICENSE` ファイルに維持されています。
