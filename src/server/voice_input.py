#!/usr/bin/env python3
"""voice-input: MLX Whisper文字起こし専用パイプライン.

mlx-whisper (Apple Silicon最適化) を使用し、
音声ファイルからテキストを高速生成する。

使い方:
  voice-input audio.mp3                    # 文字起こし
  voice-input serve                        # HTTPサーバーモード
  voice-input serve --port 8990            # ポート指定
"""

import argparse
import json
import os
import sys
import tempfile
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import mlx_whisper
import numpy as np

# MLX用にデフォルトモデル名を Hugging Face リポジトリ名に変更
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "mlx-community/whisper-large-v3-turbo")
DEFAULT_LANGUAGE = os.environ.get("DEFAULT_LANGUAGE", "ja")

# 無音判定の閾値 (dB)。この値以下のRMSレベルはWhisperに投げない。
SILENCE_THRESHOLD_DB = float(os.environ.get("SILENCE_THRESHOLD_DB", "-40"))

_is_whisper_loaded = False


def audio_rms_db(wav_data: bytes) -> float:
    """WAVバイナリデータのRMS音量をdBで返す."""
    import io
    import wave

    with wave.open(io.BytesIO(wav_data), "rb") as wf:
        frames = wf.readframes(wf.getnframes())

    samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32)
    if len(samples) == 0:
        return -float("inf")

    rms = np.sqrt(np.mean(samples**2))
    if rms < 1.0:
        return -float("inf")

    return 20.0 * np.log10(rms / 32768.0)


def _get_whisper_model():
    """MLX Whisperモデルを事前にロードしてメモリに定着させる."""
    global _is_whisper_loaded
    if not _is_whisper_loaded:
        import logging

        log = logging.getLogger("voice_input.mlx")
        log.info(f"Warming up MLX Whisper model ({WHISPER_MODEL})...")

        # 1秒分のダミー無音データ（16kHz float32）を作成して空回しする
        # これにより、Metal上でモデルがコンパイル・ロードされる
        dummy_audio = np.zeros(16000, dtype=np.float32)
        mlx_whisper.transcribe(
            dummy_audio, path_or_hf_repo=WHISPER_MODEL, word_timestamps=False
        )
        _is_whisper_loaded = True
        log.info("MLX Whisper warmup complete.")
    return None


def transcribe(
    audio_path: str, language: str | None = None, vad_filter: bool = False
) -> dict:
    """MLX Whisperで音声を文字起こしする."""
    t0 = time.time()
    _get_whisper_model()  # 初回ロード確認
    load_time = time.time() - t0

    t0 = time.time()

    # mlx-whisper に推論を実行させる
    result = mlx_whisper.transcribe(
        audio_path,
        path_or_hf_repo=WHISPER_MODEL,
        language=language,
        word_timestamps=False,
        initial_prompt="こんにちは。今日はいい天気ですね。このように、句読点をつけて適切に改行してください。",
    )

    transcribe_time = time.time() - t0

    # MLX は辞書で結果を返す
    raw_text = result.get("text", "").strip()
    detected_lang = result.get("language", language or DEFAULT_LANGUAGE)
    segments = result.get("segments", [])

    # 簡易的なduration算出
    duration = segments[-1]["end"] if segments and "end" in segments[-1] else 0.0

    return {
        "raw_text": raw_text,
        "language": detected_lang,
        "duration": duration,
        "load_time": load_time,
        "transcribe_time": transcribe_time,
        "speed": duration / transcribe_time if transcribe_time > 0 else 0,
        "segments": segments,
    }


def process_audio(
    audio_path: str,
    language: str | None = None,
    output_format: str = "text",
    quiet: bool = False,
) -> dict:
    """音声ファイルを文字起こしする完全パイプライン."""
    if not quiet:
        print(f"Transcribing: {audio_path}", file=sys.stderr)

    whisper_result = transcribe(audio_path, language=language)

    if not quiet:
        print(
            f"  → {whisper_result['duration']:.1f}s audio, "
            f"{whisper_result['speed']:.1f}x realtime, "
            f"lang={whisper_result['language']}",
            file=sys.stderr,
        )

    return whisper_result


# --- HTTP Server Mode ---


class VoiceInputHandler(BaseHTTPRequestHandler):
    """HTTP handler for pure voice-input API."""

    server_version = "voice-input/2.0"

    def do_GET(self):
        """Health check / usage info."""
        if self.path == "/health":
            self._json_response({"status": "ok"})
            return
        self._json_response(
            {
                "service": "voice-input",
                "usage": "POST /transcribe with audio file",
                "params": {
                    "language": "Language code (optional)",
                },
            }
        )

    def do_POST(self):
        """Process uploaded audio."""
        if self.path.split("?")[0] != "/transcribe":
            self._json_response({"error": "Use POST /transcribe"}, status=404)
            return

        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            self._json_response({"error": "No audio data"}, status=400)
            return

        from urllib.parse import parse_qs, urlparse

        params = parse_qs(urlparse(self.path).query)
        language = params.get("language", [None])[0]

        content_type = self.headers.get("Content-Type", "")
        ext = ".wav"
        if "mp3" in content_type or "mpeg" in content_type:
            ext = ".mp3"
        elif "ogg" in content_type:
            ext = ".ogg"
        elif "webm" in content_type:
            ext = ".webm"
        elif "m4a" in content_type or "mp4" in content_type:
            ext = ".m4a"

        audio_data = self.rfile.read(content_length)

        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as f:
            f.write(audio_data)
            tmp_path = f.name

        try:
            result = process_audio(
                tmp_path,
                language=language,
                quiet=True,
            )
            output = {
                "text": result["raw_text"],
                "language": result["language"],
                "duration": result["duration"],
                "processing_time": {
                    "transcribe": round(result["transcribe_time"], 2),
                    "total": round(result["load_time"] + result["transcribe_time"], 2),
                },
            }
            self._json_response(output)
        except Exception as e:
            self._json_response({"error": str(e)}, status=500)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def _json_response(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        print(f"[{self.log_date_time_string()}] {format % args}", file=sys.stderr)


def serve(host: str, port: int):
    """HTTPサーバーを起動."""
    server = HTTPServer((host, port), VoiceInputHandler)
    print(f"voice-input server listening on http://{host}:{port}", file=sys.stderr)
    print(f"  POST /transcribe  - Upload audio for transcription", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.", file=sys.stderr)
        server.shutdown()


# --- CLI ---


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "serve":
        if len(sys.argv) >= 3 and sys.argv[2] == "ws":
            parser = argparse.ArgumentParser(
                description="voice-input WebSocketサーバー"
            )
            parser.add_argument("_cmd", metavar="serve")
            parser.add_argument("_mode", metavar="ws")
            parser.add_argument("--host", default="0.0.0.0", help="Bind address")
            parser.add_argument("--port", type=int, default=8991, help="Port")
            args = parser.parse_args()
            import asyncio

            from ws_server import main as ws_main

            asyncio.run(ws_main(args.host, args.port))
            return
