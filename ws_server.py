#!/usr/bin/env python3
"""voice-input WebSocket server (Pure Whisper Mode).

Mac等のクライアントから音声データを受信し、
MLX Whisperによる文字起こし結果のみを高速に返却する。
"""

import asyncio
import json
import logging
import tempfile
import time
from pathlib import Path

import websockets

from voice_input import SILENCE_THRESHOLD_DB, audio_rms_db, transcribe

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("ws_server")

# クライアント別設定
client_configs: dict[str, dict] = {}


class StreamState:
    """バッチ処理モードのクライアント状態."""

    __slots__ = ("active", "latest_audio")

    def __init__(self):
        self.active = False
        self.latest_audio: bytes | None = None


stream_states: dict[str, StreamState] = {}


async def handle_client(websocket):
    """WebSocketクライアントを処理."""
    addr = websocket.remote_address
    client_id = f"{addr[0]}:{addr[1]}"
    log.info(f"Client connected: {client_id}")

    client_configs[client_id] = {"language": "ja"}
    state = StreamState()
    stream_states[client_id] = state

    try:
        async for message in websocket:
            if isinstance(message, str):
                try:
                    data = json.loads(message)
                except json.JSONDecodeError:
                    await send_json(
                        websocket, {"type": "error", "message": "Invalid JSON"}
                    )
                    continue

                msg_type = data.get("type", "")

                if msg_type == "config":
                    cfg = client_configs[client_id]
                    if "language" in data:
                        cfg["language"] = data["language"]
                    await send_json(websocket, {"type": "config_ack", **cfg})
                    log.info(f"Config updated for {client_id}: {cfg}")

                elif msg_type == "stream_start":
                    await handle_stream_start(websocket, client_id, data)

                elif msg_type == "stream_end":
                    await handle_stream_end(websocket, client_id)

                elif msg_type == "ping":
                    await send_json(websocket, {"type": "pong", "time": time.time()})

            elif isinstance(message, bytes):
                if state.active:
                    # 音声データを受信して保存するだけ
                    state.latest_audio = message
                    size_kb = len(message) / 1024
                    log.info(f"Received audio chunk: {size_kb:.1f}KB")
                else:
                    # レガシーモード（一括送信）
                    await handle_audio(websocket, client_id, message)

    except websockets.exceptions.ConnectionClosed:
        log.info(f"Client disconnected: {client_id}")
    finally:
        client_configs.pop(client_id, None)
        stream_states.pop(client_id, None)


async def handle_stream_start(websocket, client_id: str, data: dict):
    """録音開始."""
    state = stream_states[client_id]
    state.active = True
    state.latest_audio = None
    log.info(f"Stream started for {client_id}")
    await send_json(websocket, {"type": "stream_ack"})


async def handle_stream_end(websocket, client_id: str):
    """録音終了: 蓄積した音声でWhisperを実行."""
    state = stream_states.get(client_id)
    if not state:
        return

    state.active = False
    log.info(f"Stream end for {client_id}")

    cfg = client_configs.get(client_id, {})
    raw_text = ""
    transcribe_time = 0
    duration = 0
    detected_lang = cfg.get("language", "ja")

    if state.latest_audio:
        rms_db = audio_rms_db(state.latest_audio)
        if rms_db < SILENCE_THRESHOLD_DB:
            log.info(f"Audio silent ({rms_db:.1f} dB), skipping Whisper")
        else:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                f.write(state.latest_audio)
                tmp_path = f.name
            try:
                loop = asyncio.get_event_loop()
                t0 = time.time()

                # VADなしの純粋なWhisper実行
                result = await loop.run_in_executor(
                    None,
                    lambda: transcribe(tmp_path, cfg.get("language"), vad_filter=False),
                )
                transcribe_time = time.time() - t0
                raw_text = result["raw_text"]
                duration = result.get("duration", 0)
                detected_lang = result.get("language", detected_lang)

                log.info(
                    f"Transcribe ({rms_db:.1f} dB): {duration:.1f}s audio → "
                    f"{len(raw_text)} chars in {transcribe_time:.1f}s (lang={detected_lang})"
                )
            except Exception as e:
                log.error(f"Transcribe error: {e}")
            finally:
                Path(tmp_path).unlink(missing_ok=True)

    # クライアントへ結果を即返却 (LLM整形を挟まない)
    await send_json(
        websocket,
        {
            "type": "result",
            "text": raw_text,  # クライアント側でペーストされるテキスト
            "raw_text": raw_text,
            "duration": duration,
            "transcribe_time": round(transcribe_time, 2),
        },
    )


async def handle_audio(websocket, client_id: str, audio_data: bytes):
    """レガシー: バイナリ音声データを一括処理."""
    cfg = client_configs.get(client_id, {})
    size_kb = len(audio_data) / 1024
    log.info(f"[legacy] Audio from {client_id}: {size_kb:.1f}KB")

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(audio_data)
        tmp_path = f.name

    try:
        loop = asyncio.get_event_loop()
        rms_db = audio_rms_db(audio_data)
        if rms_db < SILENCE_THRESHOLD_DB:
            log.info(f"[legacy] Audio silent ({rms_db:.1f} dB), skipping Whisper")
            await send_json(websocket, {"type": "result", "text": "", "duration": 0})
            return

        await send_json(websocket, {"type": "status", "stage": "transcribing"})
        t0 = time.time()
        result = await loop.run_in_executor(
            None, lambda: transcribe(tmp_path, cfg.get("language"), vad_filter=False)
        )
        transcribe_time = time.time() - t0
        raw_text = result["raw_text"]

        log.info(
            f"Transcribed ({rms_db:.1f} dB): {result.get('duration', 0):.1f}s → "
            f"{len(raw_text)} chars in {transcribe_time:.1f}s"
        )

        await send_json(
            websocket,
            {
                "type": "result",
                "text": raw_text,
                "raw_text": raw_text,
                "language": result.get("language", ""),
                "duration": result.get("duration", 0),
                "transcribe_time": round(transcribe_time, 2),
            },
        )
    except Exception as e:
        log.error(f"Processing error: {e}")
        await send_json(websocket, {"type": "error", "message": str(e)})
    finally:
        Path(tmp_path).unlink(missing_ok=True)


async def send_json(websocket, data: dict):
    """JSON応答を送信."""
    await websocket.send(json.dumps(data, ensure_ascii=False))


async def main(host: str = "0.0.0.0", port: int = 8991):
    """WebSocketサーバーを起動."""
    log.info(f"Starting Pure Whisper WebSocket server on ws://{host}:{port}")
    log.info(f"Silence threshold: {SILENCE_THRESHOLD_DB} dB")

    # Whisperモデルを事前ロード
    from voice_input import _get_whisper_model

    log.info("Pre-loading MLX Whisper model...")
    t0 = time.time()
    _get_whisper_model()
    log.info(f"Whisper model loaded in {time.time() - t0:.1f}s")

    async with websockets.serve(
        handle_client,
        host,
        port,
        max_size=50 * 1024 * 1024,  # 50MB
        ping_interval=30,
        ping_timeout=10,
    ):
        await asyncio.Future()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Pure Whisper WebSocket server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8991)
    args = parser.parse_args()

    try:
        asyncio.run(main(args.host, args.port))
    except KeyboardInterrupt:
        log.info("Shutdown")
