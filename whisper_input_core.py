#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Whisper 語音輸入共用邏輯（唔依賴 Tk）。"""

from __future__ import annotations

import sys
from typing import Iterable

SAMPLE_RATE = 16000
MAX_RECORD_SECONDS = 5 * 60
MIN_RECORD_SECONDS = 0.35

MODELS = ("tiny", "base", "small", "medium", "turbo")

LANGUAGES = (
    ("自動偵測", None),
    ("中文", "zh"),
    ("英文", "en"),
    ("日文", "ja"),
)

KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_KEYUP = 0x0002
VK_RETURN = 0x0D
VK_CONTROL = 0x11
VK_V = 0x56


def concat_audio(frames: Iterable) -> "object":
    import numpy as np

    chunks = [np.asarray(frame, dtype=np.float32).reshape(-1) for frame in frames]
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    audio = np.concatenate(chunks)
    return np.clip(audio, -1.0, 1.0).astype(np.float32)


def rms_db(samples) -> float:
    import numpy as np

    data = np.asarray(samples, dtype=np.float64).reshape(-1)
    if data.size == 0:
        return -120.0
    rms = float(np.sqrt(np.mean(np.square(data))))
    if rms <= 1e-9:
        return -120.0
    return 20.0 * float(np.log10(rms))


def level_ratio(db_value: float, min_db: float = -50.0, max_db: float = -6.0) -> float:
    if max_db <= min_db:
        return 0.0
    return min(1.0, max(0.0, (db_value - min_db) / (max_db - min_db)))


def format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    return f"{total // 60:02d}:{total % 60:02d}"


def language_code(label: str) -> str | None:
    for name, code in LANGUAGES:
        if name == label:
            return code
    return None


def build_key_events(text: str):
    """把文字轉成 Win32 SendInput 事件：(wVk, wScan, dwFlags)。"""
    events = []
    for char in text.replace("\r\n", "\n").replace("\r", "\n"):
        if char == "\n":
            events.append((VK_RETURN, 0, 0))
            events.append((VK_RETURN, 0, KEYEVENTF_KEYUP))
            continue
        code = ord(char)
        events.append((0, code, KEYEVENTF_UNICODE))
        events.append((0, code, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))
    return events


def is_windows() -> bool:
    return sys.platform == "win32"


def list_input_devices():
    """回傳 [(index, label), ...]；sounddevice 未裝時回空清單。"""
    try:
        import sounddevice as sd
    except ImportError:
        return []

    devices = []
    try:
        host_devices = sd.query_devices()
    except Exception:
        return []

    for index, info in enumerate(host_devices):
        if int(info.get("max_input_channels") or 0) <= 0:
            continue
        name = str(info.get("name") or f"裝置 {index}")
        devices.append((index, f"{index}: {name}"))
    return devices


def default_input_device_index():
    try:
        import sounddevice as sd

        default = sd.default.device
        if isinstance(default, (list, tuple)) and default[0] is not None:
            return int(default[0])
    except Exception:
        return None
    return None
