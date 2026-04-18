"""voice-input: Core AI module for audio-to-text pipeline with MLX Whisper and Silero VAD."""

import os
import tempfile
import time
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

    # Whisperのロード
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
    """サーバー(ws_server.py)から呼ばれる唯一のエントリーポイント"""
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
        return _transcribe(tmp_path, language=language)
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def _transcribe(audio_path: str, language: str | None = None) -> dict:
    """Whisperによる推論の内部処理（外部からは直接呼ばれない想定）"""
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
