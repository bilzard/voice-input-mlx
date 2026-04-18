# voice-input-mlx

## What is this?

A high-speed, fully local voice input tool exclusively for Mac, optimized for Apple Silicon (Metal).
This project is a streamlined rebuild of [xuiltul/voice-input](https://github.com/xuiltul/voice-input), focusing on pure performance and minimal inference latency by utilizing `mlx-whisper`.

## Demo Video

🔊 **Please unmute the player to hear the narration.**

<video src="https://github.com/user-attachments/assets/8bf99117-99e5-42ac-beb7-649566a07048" controls width="100%">
  Your browser does not support the video tag.
</video>

## Key Features

* **MLX & Metal Optimized**: Maximizes Apple Silicon's GPU performance, enabling faster processing than standard Whisper implementations.
* **Background Daemon**: Runs as a background service via `launchd`. Invoke it instantly from any application using a global hotkey.
* **No Fluff (Speed First)**: Focused on "straight-to-text" output without passing through a Language Model (LLM), significantly reducing wait times.
* **Secure & Private**: All processing happens 100% locally on your device. No audio data ever leaves your Mac.

## How to Use

Operation is **toggle-based** (not push-to-talk).

1.  Press the **Hotkey** (default: `F13`) to start recording.
2.  Speak your mind.
3.  Press the hotkey again to stop recording.
4.  After a brief processing moment, the text is automatically typed at your cursor position (it is also copied to your clipboard as a fallback -- in case you focus out text box).

## Tech Stack & Architecture

-   **Backend (`ws_server.py`)**:
    -   Uses `mlx-whisper` (default:  `large-v3-turbo`).
    -   Prevents initial recognition lag by running dummy inference on startup to complete Metal shader compilation (JIT).
-   **Frontend (`mac_client.py`)**:
    -   Monitors global hotkeys using `pynput`.
    -   Sets recognition results to the clipboard via `pbcopy` and emulates `Cmd+V` using `System Events` for automatic pasting.
-   **Infrastructure**:
    -   **Service Management**: Managed via `launchd` (auto-start at login, auto-restart on crash).
    -   **Log Management**: Managed via `newsyslog` (Automatic 1MB rotation with 5-generation retention).

## Performance

Achieves high processing efficiency on M1/M2/M3 chips:
-   **Inference Efficiency**: Processes audio in approximately **10% to 20%** of the actual audio length (e.g., a 10-second clip is processed in 1–2 seconds).
-   **Memory Usage**: Approx. 1.5 GB (when the `large-v3-turbo` model is loaded).

## Installation

### Prerequisites
-   macOS (Apple Silicon)
-   Python 3.11+
-   ffmpeg (`brew install ffmpeg`)
-   [uv](https://docs.astral.sh/uv/)

### Setup

```bash
git clone https://github.com/your-repo/voice-input-mlx
cd voice-input-mlx
# One-step installation including dependencies, auto-start, and log management
bash install.sh
```

### ⚠️ Permission Settings (Important)
This tool requires **Accessibility** permissions to monitor hotkeys and perform automatic typing. Depending on your environment, add the following applications to the "Accessibility" allowlist in System Settings:

-   **When running via the auto-start service (plist):**
    -   `/opt/homebrew/bin/uv` (or your specific `uv` path)
-   **When running directly from a terminal:**
    -   `iTerm.app` / `Terminal.app` / `Visual Studio Code.app`, etc.

> [!NOTE]
> **If it still doesn't work after granting permissions:**
> Due to macOS security policies, you may need to grant permissions directly to the Python binary within your virtual environment. In that case, run `open .venv/bin` and drag the `python` file into the Accessibility settings.

## Customization & Operation

### Changing the Hotkey
The default hotkey is `F13`. You can change this to any key (e.g., for use on a MacBook keyboard) by editing the `VOICE_INPUT_HOTKEY` environment variable.

1.  Edit the value in the `.env` file:
    ```bash
    # Example: Change to F10
    VOICE_INPUT_HOTKEY="f10"
    ```
2.  Restart the service to apply changes:
    ```bash
    # Either re-run install.sh or kill the process to trigger auto-restart
    pkill -f mac_client.py
    ```

Refer to `.env.example` for other customizable variables.

### Checking Logs (Troubleshooting)
You can monitor real-time processing and errors for the background service using:

```bash
tail -f ~/Library/Logs/voice-input-mlx/voice_input.log
```

## Benchmark

Comparison of inference speed and accuracy using a ~20-second technical audio sample.

**Test Environment:**
- **Hardware:** MacBook Air (M2, 2022) / 16GB RAM
- **Audio Source:** Real voice (Non-native accent)

**English**

| Model | Audio Length | Processing Time | RTF | Transcription Output (Accuracy Test) |
| :--- | :---: | :---: | :---: | :--- |
| **(Target Sentence)** | - | - | - | **This is a demo of voice input MLX. By using the MLX framework, we can achieve high-speed inference directly on the Mac's GPU. As you can see, this transcription is almost instantaneous and highly accurate.** |
| **`whisper-small`** | 20.8s | 0.9s | **0.043** | This is our demo of voice input MLX. By using the MLX framework, we can achieve high-speed inference directly on the **Max GPU**. As you can see, the transcription is almost **input instantaneous** and highly accurate. |
| **`whisper-large-v3-turbo`**| 23.4s | 2.1s | **0.089** | **This is a demo of voice input MLX. By using the MLX framework, we can achieve high-speed inference directly on the Mac's GPU. As you can see, this transcription is almost instantaneous and highly accurate.** |

**Japanese**

| Model | Audio Length | Processing Time | RTF | Transcription Output (比較) |
| :--- | :---: | :---: | :---: | :--- |
| **(Target Sentence)** | - | | | Whisper large-v3-turboは、従来のlarge-v2と比較してデコーダーの層数が大幅に削減されています。これにより、4bit量子化に頼らずとも、Apple Silicon上でリアルタイム係数0.1以下の驚異的な速度を実現可能です。 |
| **`small-mlx`** | 22.0s | 1.7s  | **0.077** | ウィスパーラージV3ターボは、従来のラージV2と比較して、デコーダーの総数が大幅に削減されています。これにより**4ビット量しか**に頼らずとも、アップルシリコン上で**リアルタイムケース**0.1以下の**脅威的な**速度を実現可能です。 |
| **`whisper-large-v3-turbo`** | 19.2s | 2.6s | **0.135** | **Wisper Large V3 Turbo**は、従来の**Large V2**と比較して、デコーダーの総数が大幅に削減されています。これにより、**4bit量子化**に頼らずとも、**Apple Silicon**上で**リアルタイム係数**0.1以下の**驚異的な**速度を実現可能です。 |

> [!NOTE]
> **RTF (Real Time Factor)**: Processing Time / Audio Length. Lower is faster.
> *Note: An RTF of ~0.1 (processing 20s of audio in 2s) is the benchmark for a seamless user experience.*

## Credits & License

### Acknowledgements
This project was built upon or inspired by:
-   [xuiltul/voice-input](https://github.com/xuiltul/voice-input) - As the original design and logic base.

### License
This project is licensed under the **MIT License**.
In accordance with the original project's license, copyright and permission notices are maintained in the `LICENSE` file.
