import asyncio
import json
import logging
import time

import websockets
from voice_input import preload_models, process_audio_bytes

# logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("ws_server")

# streaming status
client_configs: dict[str, dict] = {}


class StreamState:
    """manage streaming buffer"""

    __slots__ = ("active", "latest_audio")

    def __init__(self):
        self.active = False
        self.latest_audio: bytes | None = None


stream_states: dict[str, StreamState] = {}


async def handle_client(websocket):
    """
    Manage all interactions with a WebSocket client.
    The server's responsibility: only parsing and routing messages.
    """
    addr = websocket.remote_address
    client_id = f"{addr[0]}:{addr[1]}"
    log.info(f"Client connected: {client_id}")

    # Initial configuration
    client_configs[client_id] = {"language": "ja"}
    state = StreamState()
    stream_states[client_id] = state

    try:
        async for message in websocket:
            # 1. Handle text messages (control commands)
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
                    state.active = True
                    state.latest_audio = None
                    log.info(f"Stream started for {client_id}")
                    await send_json(websocket, {"type": "stream_ack"})

                elif msg_type == "stream_end":
                    await handle_stream_end(websocket, client_id)

                elif msg_type == "ping":
                    await send_json(websocket, {"type": "pong", "time": time.time()})

            # 2. Handle binary messages (audio data)
            elif isinstance(message, bytes):
                if state.active:
                    # Streaming mode: accumulate in buffer
                    state.latest_audio = message
                else:
                    # Legacy mode: process single audio data immediately
                    await handle_audio_oneshot(websocket, client_id, message)

    except websockets.exceptions.ConnectionClosed:
        log.info(f"Client disconnected: {client_id}")
    finally:
        # Cleanup
        client_configs.pop(client_id, None)
        stream_states.pop(client_id, None)


async def handle_stream_end(websocket, client_id: str):
    """Handle the end of a stream. Pass the accumulated audio to the processing layer."""
    state = stream_states.get(client_id)
    if not state or not state.active:
        return

    state.active = False
    log.info(f"Stream end for {client_id}. Processing buffer...")

    cfg = client_configs.get(client_id, {})
    raw_text = ""
    transcribe_time = 0
    duration = 0

    if state.latest_audio:
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: process_audio_bytes(state.latest_audio, cfg.get("language")),
            )
            raw_text = result.get("raw_text", "")
            duration = result.get("duration", 0)
            transcribe_time = result.get("transcribe_time", 0)
            detected_lang = result.get("language", cfg.get("language"))

            # ★ ここを元の詳細なフォーマットに戻す
            if raw_text:
                log.info(
                    f"Transcribe: {duration:.1f}s audio → "
                    f"{len(raw_text)} chars in {transcribe_time:.1f}s (lang={detected_lang})"
                )
            else:
                log.info("VAD: No speech detected (or empty result), skipped Whisper.")

        except Exception as e:
            log.error(f"Processing error in stream_end: {e}")
    await send_json(
        websocket,
        {
            "type": "result",
            "text": raw_text,
            "duration": duration,
            "transcribe_time": round(transcribe_time, 2),
        },
    )


async def handle_audio_oneshot(websocket, client_id: str, audio_data: bytes):
    """Handle a single audio binary input immediately."""
    cfg = client_configs.get(client_id, {})

    try:
        await send_json(websocket, {"type": "status", "stage": "transcribing"})

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, lambda: process_audio_bytes(audio_data, cfg.get("language"))
        )

        raw_text = result.get("raw_text", "")
        duration = result.get("duration", 0)
        transcribe_time = result.get("transcribe_time", 0)

        # ★ ここも詳細フォーマットに戻す
        if not raw_text:
            log.info("[legacy] VAD: No speech detected.")
            await send_json(
                websocket, {"type": "result", "raw_text": "", "duration": 0}
            )
            return

        log.info(
            f"Transcribed: {duration:.1f}s → "
            f"{len(raw_text)} chars in {transcribe_time:.1f}s"
        )
    except Exception as e:
        log.error(f"Processing error in oneshot: {e}")
        await send_json(websocket, {"type": "error", "message": str(e)})


async def send_json(websocket, data: dict):
    """Helper: Send JSON"""
    await websocket.send(json.dumps(data, ensure_ascii=False))


async def main(host: str = "0.0.0.0", port: int = 8991):
    """Start the server and preload models"""
    log.info(f"Starting WebSocket server on ws://{host}:{port}")

    # Load both VAD and Whisper at startup
    log.info("Warming up AI models...")
    t0 = time.time()
    preload_models()
    log.info(f"Models ready in {time.time() - t0:.1f}s")

    async with websockets.serve(
        handle_client,
        host,
        port,
        max_size=50 * 1024 * 1024,  # 50MB
    ):
        await asyncio.Future()  # Keep the server running


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8991)
    args = parser.parse_args()

    try:
        asyncio.run(main(args.host, args.port))
    except KeyboardInterrupt:
        log.info("Server stopped.")
