# vad_engine.py
import io
import logging
import os

import torch
import torchaudio
import torchaudio.transforms as T

log = logging.getLogger("vad_engine")

_vad_model = None
_get_speech_timestamps = None
VAD_THRESHOLD = float(os.environ.get("VAD_THRESHOLD", 0.5))


def preload_vad_model():
    """サーバー起動時に呼ばれる事前ロード処理"""
    global _vad_model, _get_speech_timestamps
    if _vad_model is None:
        log.info("Loading Silero VAD model...")
        model, utils = torch.hub.load(
            repo_or_dir="snakers4/silero-vad",
            model="silero_vad",
        )
        _vad_model = model
        _get_speech_timestamps = utils[0]
        log.info("Silero VAD warmup complete.")


def has_speech(audio_data: bytes, threshold: float = VAD_THRESHOLD) -> bool:
    """音声バイナリを受け取り、声が含まれるか判定する"""
    preload_vad_model()
    try:
        wav_tensor, sample_rate = torchaudio.load(io.BytesIO(audio_data))
        wav_tensor = wav_tensor[0]

        if sample_rate != 16000:
            resampler = T.Resample(sample_rate, 16000)
            wav_tensor = resampler(wav_tensor)

        timestamps = _get_speech_timestamps(
            wav_tensor, _vad_model, sampling_rate=16000, threshold=threshold
        )
        return len(timestamps) > 0
    except Exception as e:
        log.warning(f"VAD checking error: {e}. Falling back to Whisper.")
        return True
