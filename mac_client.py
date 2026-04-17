#!/usr/bin/env python3
"""voice-input Mac client: Push-to-Talk → WebSocket → キーボード入力.

使い方:
  1. サーバー側: voice-input serve ws
  2. Mac側:     python3 mac_client.py --server ws://YOUR_SERVER_IP:8991

操作:
  F13(デフォルト)を1回押す → 録音開始/終了 → ペーストのみ
  終了時にCtrlを押す       → ペースト + 送信(Enter)

依存 (Mac側):
  pip3 install sounddevice numpy websockets pynput pyperclip

macOS設定:
  システム設定 > プライバシーとセキュリティ > マイク → ターミナルを許可
  システム設定 > プライバシーとセキュリティ > アクセシビリティ → ターミナルを許可
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import wave

import numpy as np
import sounddevice as sd
import websockets
from pynput import keyboard
from pynput.keyboard import Controller, Key

# --- 設定 ---
SAMPLE_RATE = 16000
CHANNELS = 1
DTYPE = "int16"

# 環境変数 "VOICE_INPUT_HOTKEY" からキー名を取得。指定がなければ "f13" をデフォルトに。
HOTKEY_NAME = os.environ.get("VOICE_INPUT_HOTKEY", "f13")

try:
    HOTKEY = getattr(keyboard.Key, HOTKEY_NAME)
except AttributeError:
    print(f"Warning: Invalid hotkey '{HOTKEY_NAME}'. Falling back to f13.")
    HOTKEY = keyboard.Key.f13
    HOTKEY_NAME = "f13"

# --- ステータスオーバーレイ（フローティングHUD） ---
OVERLAY_SCRIPT = r"""
import sys, threading, queue, time

TEXTS = {
    "recording":         "\U0001f534 Recording",  # 🔴
    "transcribing":      "\U0001f916 Processing", # 🤖
    "done":              "\u2705 Done!",
    "error":             "\u274c Error",
}

COLORS = {
    "recording":    (0.25, 0.05, 0.05, 0.60),
    "transcribing": (0.10, 0.10, 0.10, 0.60),
    "done":         (0.05, 0.20, 0.08, 0.60),
    "error":        (0.20, 0.05, 0.05, 0.60),
}

try:
    from AppKit import (
        NSApplication, NSWindow, NSTextField, NSColor, NSFont,
        NSBackingStoreBuffered, NSScreen, NSTimer, NSMakeRect,
        NSView, NSBezierPath, NSTextAlignmentCenter,
        NSMutableAttributedString, NSAttributedString,
        NSForegroundColorAttributeName, NSFontAttributeName,
        NSMutableParagraphStyle, NSParagraphStyleAttributeName # ★ 追加：段落スタイル
    )
    from Foundation import NSObject
    HAS_APPKIT = True
except ImportError:
    HAS_APPKIT = False

if not HAS_APPKIT:
    sys.exit(0)

_cmd_queue = queue.Queue()
_hud_window = None
_hud_label = None
_hud_bg = None
_hud_visible = False
_hide_at = 0
_current_stage = "idle"
_anim_frame = 0

def _stdin_reader():
    for line in sys.stdin:
        cmd = line.strip()
        if cmd:
            _cmd_queue.put(cmd)
    _cmd_queue.put("EXIT")

class _RoundedBG(NSView):
    def drawRect_(self, rect):
        r, g, b, a = COLORS.get(_current_stage, COLORS["transcribing"])
        NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, a).set()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            self.bounds(), self.bounds().size.height / 2, self.bounds().size.height / 2,
        ).fill()

class _Poller(NSObject):
    def tick_(self, timer):
        global _hud_visible, _hide_at, _current_stage, _anim_frame
        now = time.time()

        if _hide_at and now >= _hide_at:
            _hide_at = 0
            if _hud_window:
                _hud_window.orderOut_(None)
                _hud_visible = False

        if _current_stage in ("recording", "transcribing"):
            _anim_frame = (_anim_frame + 1) % 32
            dot_count = (int(_anim_frame / 8) % 4)

            attr_str = NSMutableAttributedString.alloc().init()
            current_font = _hud_label.font()

            # ★ 追加：文字列自身に「中央揃え」のスタイルを強制する
            p_style = NSMutableParagraphStyle.alloc().init()
            p_style.setAlignment_(NSTextAlignmentCenter)

            def add_part(text, is_visible):
                if not text: return
                color = NSColor.whiteColor() if is_visible else NSColor.clearColor()
                attrs = {
                    NSFontAttributeName: current_font,
                    NSForegroundColorAttributeName: color,
                    NSParagraphStyleAttributeName: p_style  # ★ 属性として中央揃えを付与
                }
                part = NSAttributedString.alloc().initWithString_attributes_(text, attrs)
                attr_str.appendAttributedString_(part)

            base_text = TEXTS.get(_current_stage, "")
            center_text = f"  {base_text}  "

            transparent_dots = 3 - dot_count
            visible_dots = dot_count

            add_part("." * transparent_dots, False)
            add_part("." * visible_dots, True)
            add_part(center_text, True)
            add_part("." * visible_dots, True)
            add_part("." * transparent_dots, False)

            _hud_label.setAttributedStringValue_(attr_str)

        try:
            while True:
                cmd = _cmd_queue.get_nowait()
                if cmd == "EXIT":
                    NSApplication.sharedApplication().terminate_(None)
                    return
                if cmd == "HIDE":
                    if _hud_window: _hud_window.orderOut_(None)
                    _hud_visible = False
                    continue

                parts = cmd.split(":", 1)
                _current_stage = parts[0].strip()
                msg = parts[1].strip() if (len(parts) > 1 and parts[1].strip()) else TEXTS.get(_current_stage, _current_stage)

                _hud_bg.setNeedsDisplay_(True)

                if _current_stage not in ("recording", "transcribing"):
                    _show_hud(msg)

                if _current_stage in ("recording", "transcribing") and not _hud_visible:
                    _show_hud("")

                if _current_stage in ("done", "error"):
                    _hide_at = now + 1.2
        except queue.Empty:
            pass

def _show_hud(text):
    global _hud_visible
    scr = NSScreen.mainScreen().frame()
    w = _hud_window.frame().size.width

    x = scr.origin.x + (scr.size.width - w) / 2
    y = scr.origin.y + 60
    _hud_window.setFrameOrigin_((x, y))

    if text:
        _hud_label.setStringValue_(text)

    if not _hud_visible:
        _hud_window.setAlphaValue_(0.0)
        _hud_window.orderFront_(None)
        _hud_window.animator().setAlphaValue_(1.0)
        _hud_visible = True

def main():
    global _hud_window, _hud_label, _hud_bg
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(2)

    W, H = 260, 36

    _hud_window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(0, 0, W, H), 0, NSBackingStoreBuffered, False,
    )
    _hud_window.setLevel_(25)
    _hud_window.setOpaque_(False)
    _hud_window.setBackgroundColor_(NSColor.clearColor())
    _hud_window.setIgnoresMouseEvents_(True)
    _hud_window.setHasShadow_(True)

    _hud_bg = _RoundedBG.alloc().initWithFrame_(NSMakeRect(0, 0, W, H))
    _hud_window.setContentView_(_hud_bg)

    _hud_label = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 8, W, 20))
    _hud_label.setEditable_(False)
    _hud_label.setBezeled_(False)
    _hud_label.setDrawsBackground_(False)
    _hud_label.setTextColor_(NSColor.whiteColor())
    _hud_label.setAlignment_(NSTextAlignmentCenter) # 通常テキスト用の中央揃え
    _hud_label.setFont_(NSFont.monospacedSystemFontOfSize_weight_(14, 0))

    _hud_bg.addSubview_(_hud_label)

    threading.Thread(target=_stdin_reader, daemon=True).start()
    poller = _Poller.alloc().init()
    NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        0.05, poller, b"tick:", None, True,
    )
    app.run()

if __name__ == "__main__":
    main()
"""


class VoiceInputClient:
    def __init__(
        self,
        server_url: str,
        language: str = "ja",
        paste: bool = True,
    ):
        self.server_url = server_url
        self.language = language
        self.paste = paste

        self.recording = False
        self.audio_chunks: list[np.ndarray] = []
        self.stream = None
        self.ws = None
        self.loop = None
        self._connected = False
        self._overlay_proc = None
        self._overlay_script_path = None
        self._ctrl_pressed = False
        self._send_enter = True
        self.keyboard_controller = Controller()

    def start(self):
        """メインループを開始."""
        print(f"voice-input client (Pure Whisper Mode)")
        print(f"  Server:   {self.server_url}")
        print(f"  Language: {self.language}")
        print(f"  Paste:    {'clipboard+Cmd+V' if self.paste else 'clipboard only'}")
        print(f"")
        print(
            f"  [{HOTKEY_NAME}を1回押す]          → 録音開始/終了 → ペーストのみ（デフォルト）"
        )
        print(
            f"  [{HOTKEY_NAME}を押し、終了時にCtrl] → 録音開始/終了 → ペースト + 送信"
        )
        print(f"  [Ctrl+C] → 終了")
        print()

        self._start_overlay()

        self.loop = asyncio.new_event_loop()
        ws_thread = threading.Thread(target=self._run_event_loop, daemon=True)
        ws_thread.start()

        with keyboard.Listener(
            on_press=self._on_key_press,
            on_release=self._on_key_release,
        ) as listener:
            try:
                listener.join()
            except KeyboardInterrupt:
                print("\nShutting down.")
                self._stop_overlay()

    def _run_event_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._maintain_connection())

    async def _maintain_connection(self):
        """WebSocket接続を維持."""
        while True:
            try:
                async with websockets.connect(
                    self.server_url,
                    max_size=50 * 1024 * 1024,
                    ping_interval=30,
                ) as ws:
                    self.ws = ws
                    self._connected = True
                    print(f"  ✓ Connected to {self.server_url}")

                    config_msg = {
                        "type": "config",
                        "language": self.language,
                    }
                    await ws.send(json.dumps(config_msg))

                    async for msg in ws:
                        data = json.loads(msg)
                        self._handle_server_message(data)

            except (
                websockets.exceptions.ConnectionClosed,
                OSError,
                TimeoutError,
                asyncio.TimeoutError,
            ) as e:
                self._connected = False
                self.ws = None
                print(f"  ✗ Connection failed: {e}. Retrying in 3s...")
                await asyncio.sleep(3)

    def _handle_server_message(self, data: dict):
        """サーバーからの応答を処理."""
        msg_type = data.get("type", "")

        if msg_type == "status":
            stage = data.get("stage", "")
            if stage == "transcribing":
                print("  ⟳ Transcribing...", end="", flush=True)
                self._update_overlay("transcribing")

        elif msg_type == "result":
            text = data.get("text", "")
            t_trans = data.get("transcribe_time", 0)
            dur = data.get("duration", 0)

            send_enter = self._send_enter

            enter_label = "+Enter" if send_enter else ""
            print(
                f"\n  Done ({t_trans:.1f}s){' ' + enter_label if enter_label else ''}"
            )
            self._update_overlay("done")

            if text:
                self._output_text(text, send_enter=send_enter)
                print(
                    f"  → [{dur:.1f}s audio] {text[:80]}{'...' if len(text) > 80 else ''}"
                )
            else:
                print("  → (empty - no speech detected)")

        elif msg_type == "config_ack":
            pass

        elif msg_type == "error":
            print(f"\n  ✗ Error: {data.get('message', 'unknown')}")
            self._update_overlay("error", f"\u2717 {data.get('message', 'Error')[:40]}")

    def _output_text(self, text: str, send_enter: bool = False):
        """テキストをクリップボード経由でペースト."""
        try:
            proc = subprocess.Popen(
                ["pbcopy"],
                stdin=subprocess.PIPE,
            )
            proc.communicate(text.encode("utf-8"))

            if self.paste:
                time.sleep(0.05)
                with self.keyboard_controller.pressed(Key.cmd):
                    self.keyboard_controller.press("v")
                    self.keyboard_controller.release("v")

                if send_enter:
                    time.sleep(0.05)
                    self.keyboard_controller.press(Key.enter)
                    self.keyboard_controller.release(Key.enter)
        except FileNotFoundError:
            try:
                import pyperclip

                pyperclip.copy(text)
                print("  (clipboard only - paste manually with Cmd+V)")
            except ImportError:
                print(f"  [clipboard unavailable] {text}")

    def _on_key_press(self, key):
        """キー押下時."""
        if key == keyboard.Key.ctrl_l or key == keyboard.Key.ctrl_r:
            self._ctrl_pressed = True

        if key == HOTKEY:
            if getattr(self, "_hotkey_down", False):
                return
            self._hotkey_down = True

            if not self.recording:
                self._start_recording()
            else:
                self._send_enter = self._ctrl_pressed
                self._stop_recording()

    def _on_key_release(self, key):
        """キー離し時."""
        if key == keyboard.Key.ctrl_l or key == keyboard.Key.ctrl_r:
            self._ctrl_pressed = False

        if key == HOTKEY:
            self._hotkey_down = False

    def _start_recording(self):
        """録音開始."""
        if not self._connected:
            print("  ✗ Not connected to server")
            return

        self.recording = True
        self.audio_chunks = []
        print("  ● Recording...", end="", flush=True)
        self._update_overlay("recording")

        start_msg = {"type": "stream_start"}
        asyncio.run_coroutine_threadsafe(
            self.ws.send(json.dumps(start_msg)),
            self.loop,
        )

        self.stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype=DTYPE,
            callback=self._audio_callback,
            blocksize=1024,
        )
        self.stream.start()

    def _stop_recording(self):
        """録音停止 → 最終音声を一括送信."""
        if not self.recording:
            return

        self.recording = False

        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None

        if not self.audio_chunks:
            print(" (empty)")
            if self.ws and self._connected:
                asyncio.run_coroutine_threadsafe(
                    self.ws.send(json.dumps({"type": "stream_end"})),
                    self.loop,
                )
            return

        audio_data = np.concatenate(self.audio_chunks)
        duration = len(audio_data) / SAMPLE_RATE
        print(f" {duration:.1f}s", end="", flush=True)

        if duration < 0.3:
            print(" (too short, skipped)")
            if self.ws and self._connected:
                asyncio.run_coroutine_threadsafe(
                    self.ws.send(json.dumps({"type": "stream_end"})),
                    self.loop,
                )
            return

        wav_bytes = self._encode_wav(audio_data)
        print(f" ({len(wav_bytes) // 1024}KB)", end="", flush=True)

        if self.ws and self._connected:

            async def _send_final():
                await self.ws.send(wav_bytes)
                await self.ws.send(json.dumps({"type": "stream_end"}))

            asyncio.run_coroutine_threadsafe(_send_final(), self.loop)
            print(" → Sent.", end="", flush=True)
            self._update_overlay("transcribing")
        else:
            print(" ✗ Not connected")
            self._update_overlay("error", "\u2717 Not connected")

    def _audio_callback(self, indata, frames, time_info, status):
        """sounddevice録音コールバック."""
        if status:
            print(f"\n  Audio warning: {status}", file=sys.stderr)
        if self.recording:
            self.audio_chunks.append(indata.copy())

    @staticmethod
    def _encode_wav(audio: np.ndarray) -> bytes:
        """numpy配列をWAVバイト列にエンコード."""
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(audio.tobytes())
        return buf.getvalue()

    # --- ステータスオーバーレイ管理 ---

    def _start_overlay(self):
        try:
            fd, path = tempfile.mkstemp(suffix=".py", prefix="voice_overlay_")
            with os.fdopen(fd, "w") as f:
                f.write(OVERLAY_SCRIPT)
            self._overlay_script_path = path

            self._overlay_proc = subprocess.Popen(
                [sys.executable, path],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            print(f"  (overlay unavailable: {e})")
            self._overlay_proc = None

    def _update_overlay(self, stage: str, custom_msg: str | None = None):
        if not self._overlay_proc or not self._overlay_proc.stdin:
            return
        try:
            line = f"{stage}:{custom_msg}\n" if custom_msg else f"{stage}\n"
            self._overlay_proc.stdin.write(line.encode("utf-8"))
            self._overlay_proc.stdin.flush()
        except (BrokenPipeError, OSError):
            self._overlay_proc = None

    def _stop_overlay(self):
        if self._overlay_proc:
            try:
                self._overlay_proc.terminate()
                self._overlay_proc.wait(timeout=2)
            except Exception:
                pass
        if self._overlay_script_path:
            try:
                os.unlink(self._overlay_script_path)
            except Exception:
                pass


def main():
    parser = argparse.ArgumentParser(
        description="voice-input Mac client: Pure Whisper Mode",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    default_server = os.environ.get("VOICE_INPUT_SERVER", "ws://localhost:8991")
    parser.add_argument(
        "-s",
        "--server",
        default=default_server,
        help=f"WebSocket server URL (default: {default_server})",
    )
    parser.add_argument("-l", "--language", default="ja", help="Language (default: ja)")
    parser.add_argument(
        "--no-paste",
        action="store_true",
        help="Clipboard only, don't auto-paste with Cmd+V",
    )
    args = parser.parse_args()

    client = VoiceInputClient(
        server_url=args.server,
        language=args.language,
        paste=not args.no_paste,
    )
    client.start()


if __name__ == "__main__":
    main()
