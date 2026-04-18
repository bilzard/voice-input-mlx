#!/usr/bin/env python3
"""voice-input: a faster audio-to-text pipeline with MLX Whisper and Silero VAD.

Usage:
  voice-input audio.mp3                    # process audio file
  voice-input serve                        # start HTTP server
  voice-input serve --port 8990            # with port
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
import vad_engine

WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "mlx-community/whisper-large-v3-turbo")
DEFAULT_LANGUAGE = os.environ.get("DEFAULT_LANGUAGE", "ja")

_is_whisper_loaded = False

# 「総数」を「層数」にするなど、専門用語を正しく認識させるためのプロンプト
INITIAL_PROMPT_MAP = dict(
    ja="句読点を適切に使い、自然な日本語で記述してください。また、技術的な専門用語は原則、英語表記で記載してください。",
    en="Hello. This is a clear recording with proper punctuation and capitalization.",
)


def preload_models():
    """サーバー起動時に呼ばれる、すべてのモデルの事前ロード"""
    global _is_whisper_loaded

    # VADのロードを委譲
    vad_engine.preload_vad_model()

    # Whisperのロード（重複していた _get_whisper_model をここに統合）
    if not _is_whisper_loaded:
        import logging

        log = logging.getLogger("voice_input.mlx")
        log.info(f"Warming up MLX Whisper model ({WHISPER_MODEL})...")

        dummy = np.zeros(16000, dtype=np.float32)
        mlx_whisper.transcribe(
            dummy, path_or_hf_repo=WHISPER_MODEL, word_timestamps=False
        )
        _is_whisper_loaded = True
        log.info("MLX Whisper warmup complete.")


def process_audio_bytes(audio_data: bytes, language: str | None = None) -> dict:
    """サーバー(WebSocket)から呼ばれるバイナリ処理用のエントリーポイント"""
    # VAD判定を外部モジュールに委譲
    if not vad_engine.has_speech(audio_data):
        return {
            "raw_text": "",
            "language": language or DEFAULT_LANGUAGE,
            "duration": 0.0,
            "transcribe_time": 0.0,
            "speed": 0.0,
            "segments": [],
        }

    # 声があれば一時ファイルに書いて Whisper に流す
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(audio_data)
        tmp_path = f.name

    try:
        return transcribe(tmp_path, language=language)
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def transcribe(
    audio_path: str, language: str | None = None, vad_filter: bool = False
) -> dict:
    t0 = time.time()
    preload_models()  # 初回ロード確認
    load_time = time.time() - t0

    t0 = time.time()

    result = mlx_whisper.transcribe(
        audio_path,
        path_or_hf_repo=WHISPER_MODEL,
        language=language,
        word_timestamps=False,
        initial_prompt=INITIAL_PROMPT_MAP.get(language, ""),
    )

    transcribe_time = time.time() - t0

    raw_text = result.get("text", "").strip()
    detected_lang = result.get("language", language or DEFAULT_LANGUAGE)
    segments = result.get("segments", [])
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
    """CLIやHTTPサーバから呼ばれるファイル処理用のエントリーポイント"""
    if not quiet:
        print(f"Transcribing: {audio_path}", file=sys.stderr)

    # HTTP/CLIルートでもVADで足切りする処理を追加
    with open(audio_path, "rb") as f:
        audio_bytes = f.read()
    if not vad_engine.has_speech(audio_bytes):
        if not quiet:
            print("  → VAD: No speech detected, skipping.", file=sys.stderr)
        return {
            "raw_text": "",
            "language": language or DEFAULT_LANGUAGE,
            "duration": 0.0,
            "load_time": 0.0,
            "transcribe_time": 0.0,
            "speed": 0.0,
            "segments": [],
        }

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
        if self.path == "/health":
            self._json_response({"status": "ok"})
            return
        self._json_response(
            {
                "service": "voice-input",
                "usage": "POST /transcribe with audio file",
                "params": {"language": "Language code (optional)"},
            }
        )

    def do_POST(self):
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
            result = process_audio(tmp_path, language=language, quiet=True)
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
            parser = argparse.ArgumentParser(description="voice-input WebSocket Server")
            parser.add_argument("_cmd", metavar="serve")
            parser.add_argument("_mode", metavar="ws")
            parser.add_argument("--host", default="0.0.0.0", help="Bind address")
            parser.add_argument("--port", type=int, default=8991, help="Port")
            args = parser.parse_args()
            import asyncio

            from ws_server import main as ws_main

            asyncio.run(ws_main(args.host, args.port))
            return

        # HTTP Server fallback
        parser = argparse.ArgumentParser(description="voice-input HTTP Server")
        parser.add_argument("--host", default="0.0.0.0", help="Bind address")
        parser.add_argument("--port", type=int, default=8990, help="Port")
        args = parser.parse_args(sys.argv[2:])
        serve(args.host, args.port)
        return

    # CLI fallback
    parser = argparse.ArgumentParser(description="Transcribe audio file")
    parser.add_argument("audio", help="Path to audio file")
    args = parser.parse_args()
    process_audio(args.audio)


if __name__ == "__main__":
    main()
