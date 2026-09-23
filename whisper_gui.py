#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Windows 本機語音輸入 - openai-whisper - 眼鏡妹

獨立 GUI，唔依賴 OpenClaw。喺 Windows 錄音，用本機 Whisper 轉文字，
可以複製，或者直接輸入去你先前聚焦嘅視窗（記事本、瀏覽器、Word 等）。

安裝（喺命令提示字元 / PowerShell）：
  pip install -U -r requirements-whisper-gui.txt
  # Whisper 轉檔建議一併安裝 ffmpeg，並加到 PATH

啟動：
  雙擊 start_whisper_gui.bat
  或：python whisper_gui.py
"""

from __future__ import annotations

import argparse
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

from whisper_input_core import (
    LANGUAGES,
    MAX_RECORD_SECONDS,
    MIN_RECORD_SECONDS,
    MODELS,
    SAMPLE_RATE,
    concat_audio,
    default_input_device_index,
    format_duration,
    is_windows,
    language_code,
    level_ratio,
    list_input_devices,
    rms_db,
)

HOTKEY_ID = 1
MOD_CONTROL = 0x0002
MOD_ALT = 0x0001
VK_R = 0x52
WM_HOTKEY = 0x0312
SW_RESTORE = 9


class Recorder:
    def __init__(self, samplerate=SAMPLE_RATE):
        self.samplerate = samplerate
        self.frames = []
        self.stream = None
        self.started_at = 0.0
        self.last_level = -120.0
        self._lock = threading.Lock()

    def start(self, device=None):
        import sounddevice as sd

        self.stop()
        with self._lock:
            self.frames = []
            self.last_level = -120.0
        self.started_at = time.monotonic()
        self.stream = sd.InputStream(
            samplerate=self.samplerate,
            channels=1,
            dtype="float32",
            device=device,
            callback=self._on_audio,
        )
        self.stream.start()

    def _on_audio(self, indata, frames, audio_time, status):
        chunk = indata.copy()
        with self._lock:
            self.frames.append(chunk)
            self.last_level = rms_db(chunk)

    def elapsed(self) -> float:
        if not self.started_at:
            return 0.0
        return time.monotonic() - self.started_at

    def peek_level(self) -> float:
        with self._lock:
            return self.last_level

    def stop(self):
        stream = self.stream
        self.stream = None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
        with self._lock:
            audio = concat_audio(self.frames)
            self.frames = []
        self.started_at = 0.0
        return audio


class WhisperEngine:
    def __init__(self):
        self._model = None
        self._name = None
        self._lock = threading.Lock()

    def transcribe(self, audio, model_name: str, language: str | None) -> str:
        import numpy as np

        if audio is None or len(audio) == 0:
            raise ValueError("沒有錄到聲音")

        with self._lock:
            if self._model is None or self._name != model_name:
                import whisper

                self._model = whisper.load_model(model_name)
                self._name = model_name
            model = self._model

        options = {"task": "transcribe"}
        if language:
            options["language"] = language
        samples = np.asarray(audio, dtype="float32").reshape(-1)
        result = model.transcribe(samples, **options)
        return (result.get("text") or "").strip()


def _win_user32():
    import ctypes

    return ctypes.windll.user32


def get_foreground_hwnd():
    if not is_windows():
        return None
    return _win_user32().GetForegroundWindow()


def focus_window(hwnd) -> bool:
    if not hwnd or not is_windows():
        return False
    import ctypes

    user32 = _win_user32()
    user32.ShowWindow(hwnd, SW_RESTORE)
    return bool(user32.SetForegroundWindow(hwnd))


def inject_text(text: str) -> None:
    if not is_windows():
        raise RuntimeError("直接輸入只支援 Windows")
    if not text:
        return

    import ctypes

    from whisper_input_core import build_key_events

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = (
            ("wVk", ctypes.c_ushort),
            ("wScan", ctypes.c_ushort),
            ("dwFlags", ctypes.c_ulong),
            ("time", ctypes.c_ulong),
            ("dwExtraInfo", ctypes.c_void_p),
        )

    class INPUT_UNION(ctypes.Union):
        _fields_ = (("ki", KEYBDINPUT),)

    class INPUT(ctypes.Structure):
        _fields_ = (("type", ctypes.c_ulong), ("union", INPUT_UNION))

    events = build_key_events(text)
    payload = (INPUT * len(events))()
    for index, (vk, scan, flags) in enumerate(events):
        payload[index].type = 1  # INPUT_KEYBOARD
        payload[index].union.ki = KEYBDINPUT(vk, scan, flags, 0, None)

    sent = ctypes.windll.user32.SendInput(len(events), payload, ctypes.sizeof(INPUT))
    if sent != len(events):
        raise RuntimeError("Windows SendInput 失敗，可能被目標程式攔截")


class HotkeyWatcher(threading.Thread):
    def __init__(self, callback):
        super().__init__(daemon=True)
        self.callback = callback
        self._stop = threading.Event()
        self.ok = False
        self.error = ""

    def stop(self):
        self._stop.set()

    def run(self):
        if not is_windows():
            self.error = "快捷鍵只喺 Windows 生效"
            return
        import ctypes
        from ctypes import wintypes

        user32 = _win_user32()
        if not user32.RegisterHotKey(None, HOTKEY_ID, MOD_CONTROL | MOD_ALT, VK_R):
            self.error = "無法登記 Ctrl+Alt+R，可能已被其他程式使用"
            return
        self.ok = True
        msg = wintypes.MSG()
        try:
            while not self._stop.is_set():
                has_msg = user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1)
                if has_msg and msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                    self.callback()
                else:
                    self._stop.wait(0.05)
        finally:
            user32.UnregisterHotKey(None, HOTKEY_ID)


class WhisperInputApp(tk.Tk):
    def __init__(self, model="base", language_label="中文"):
        super().__init__()
        self.title("Whisper 語音輸入")
        self.geometry("520x560")
        self.minsize(460, 500)
        self.recorder = Recorder()
        self.engine = WhisperEngine()
        self.ui_queue = queue.Queue()
        self.recording = False
        self.busy = False
        self.last_hwnd = None
        self.own_hwnd = None
        self.hotkey = None

        self.model_var = tk.StringVar(value=model if model in MODELS else "base")
        self.language_var = tk.StringVar(value=language_label)
        self.device_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="準備就緒")
        self.timer_var = tk.StringVar(value="00:00")
        self.auto_copy_var = tk.BooleanVar(value=True)
        self.auto_paste_var = tk.BooleanVar(value=False)
        self.topmost_var = tk.BooleanVar(value=True)

        self._build_ui()
        self._refresh_devices()
        self.attributes("-topmost", True)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(200, self._capture_own_hwnd)
        self.after(250, self._poll_foreground)
        self.after(80, self._drain_queue)
        self._start_hotkey()

    def _build_ui(self):
        root = ttk.Frame(self, padding=14)
        root.pack(fill=tk.BOTH, expand=True)

        ttk.Label(root, text="本機 Whisper 語音輸入", font=("Segoe UI", 14, "bold")).pack(
            anchor="w"
        )
        ttk.Label(
            root,
            text="按錄音講嘢，轉寫後可複製或輸入到上一隻視窗。首次載入模型會較慢。",
            wraplength=470,
        ).pack(anchor="w", pady=(4, 10))

        form = ttk.Frame(root)
        form.pack(fill=tk.X)
        ttk.Label(form, text="模型").grid(row=0, column=0, sticky="w")
        ttk.Combobox(
            form, textvariable=self.model_var, values=MODELS, state="readonly", width=10
        ).grid(row=0, column=1, sticky="w", padx=(6, 16))
        ttk.Label(form, text="語言").grid(row=0, column=2, sticky="w")
        ttk.Combobox(
            form,
            textvariable=self.language_var,
            values=[name for name, _code in LANGUAGES],
            state="readonly",
            width=10,
        ).grid(row=0, column=3, sticky="w", padx=(6, 0))

        ttk.Label(form, text="咪高峰").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.device_combo = ttk.Combobox(form, textvariable=self.device_var, state="readonly")
        self.device_combo.grid(row=1, column=1, columnspan=3, sticky="ew", padx=(6, 0), pady=(8, 0))
        form.columnconfigure(1, weight=1)
        form.columnconfigure(3, weight=1)

        controls = ttk.Frame(root)
        controls.pack(fill=tk.X, pady=14)
        self.record_btn = ttk.Button(controls, text="開始錄音", command=self.toggle_record)
        self.record_btn.pack(side=tk.LEFT)
        ttk.Label(controls, textvariable=self.timer_var, font=("Consolas", 12)).pack(
            side=tk.LEFT, padx=12
        )
        self.level = ttk.Progressbar(controls, maximum=100, mode="determinate")
        self.level.pack(side=tk.LEFT, fill=tk.X, expand=True)

        ttk.Label(root, text="快捷鍵：Ctrl+Alt+R（Windows）開始／停止錄音").pack(anchor="w")

        self.text = tk.Text(root, height=12, wrap=tk.WORD, font=("Microsoft JhengHei UI", 11))
        self.text.pack(fill=tk.BOTH, expand=True, pady=(10, 8))

        buttons = ttk.Frame(root)
        buttons.pack(fill=tk.X)
        ttk.Button(buttons, text="複製", command=self.copy_text).pack(side=tk.LEFT)
        ttk.Button(buttons, text="輸入到上一視窗", command=self.paste_to_last_window).pack(
            side=tk.LEFT, padx=8
        )
        ttk.Button(buttons, text="清空", command=self.clear_text).pack(side=tk.LEFT)
        ttk.Button(buttons, text="使用說明", command=self.show_help).pack(side=tk.RIGHT)

        options = ttk.Frame(root)
        options.pack(fill=tk.X, pady=(10, 4))
        ttk.Checkbutton(options, text="轉寫後自動複製", variable=self.auto_copy_var).pack(
            side=tk.LEFT
        )
        ttk.Checkbutton(options, text="轉寫後自動輸入", variable=self.auto_paste_var).pack(
            side=tk.LEFT, padx=10
        )
        ttk.Checkbutton(
            options, text="視窗置頂", variable=self.topmost_var, command=self._toggle_topmost
        ).pack(side=tk.LEFT)

        ttk.Label(root, textvariable=self.status_var).pack(anchor="w", pady=(8, 0))

    def _refresh_devices(self):
        devices = list_input_devices()
        labels = [label for _index, label in devices]
        self._device_map = {label: index for index, label in devices}
        if labels:
            self.device_combo["values"] = labels
            current = default_input_device_index()
            chosen = labels[0]
            for index, label in devices:
                if current is not None and index == current:
                    chosen = label
                    break
            self.device_var.set(chosen)
        else:
            self.device_combo["values"] = ["未偵測到咪高峰／未安裝 sounddevice"]
            self.device_var.set("未偵測到咪高峰／未安裝 sounddevice")

    def _selected_device(self):
        return self._device_map.get(self.device_var.get())

    def _capture_own_hwnd(self):
        if is_windows():
            try:
                self.own_hwnd = int(str(self.wm_frame()), 0)
                return
            except Exception:
                pass
        self.own_hwnd = get_foreground_hwnd()

    def _poll_foreground(self):
        hwnd = get_foreground_hwnd()
        if hwnd and hwnd != self.own_hwnd:
            self.last_hwnd = hwnd
        if self.recording:
            elapsed = self.recorder.elapsed()
            self.timer_var.set(format_duration(elapsed))
            self.level["value"] = int(level_ratio(self.recorder.peek_level()) * 100)
            if elapsed >= MAX_RECORD_SECONDS:
                self._stop_and_transcribe()
        else:
            self.level["value"] = 0
        self.after(250, self._poll_foreground)

    def _drain_queue(self):
        while True:
            try:
                item = self.ui_queue.get_nowait()
            except queue.Empty:
                break
            kind = item[0]
            if kind == "status":
                self.status_var.set(item[1])
            elif kind == "text":
                self._set_result(item[1])
            elif kind == "error":
                self.status_var.set("轉寫失敗")
                messagebox.showerror("Whisper 語音輸入", item[1])
            elif kind == "idle":
                self.busy = False
                self.record_btn.configure(state=tk.NORMAL, text="開始錄音")
            elif kind == "hotkey":
                self.toggle_record()
        self.after(80, self._drain_queue)

    def _start_hotkey(self):
        self.hotkey = HotkeyWatcher(lambda: self.ui_queue.put(("hotkey",)))
        self.hotkey.start()
        self.after(400, self._hotkey_status)

    def _hotkey_status(self):
        if self.hotkey and self.hotkey.ok:
            self.status_var.set("準備就緒。可用 Ctrl+Alt+R 錄音")
        elif self.hotkey and self.hotkey.error and not is_windows():
            self.status_var.set("準備就緒。快捷鍵只喺 Windows 生效，請撳錄音掣")
        elif self.hotkey and self.hotkey.error:
            self.status_var.set(self.hotkey.error)

    def _toggle_topmost(self):
        self.attributes("-topmost", bool(self.topmost_var.get()))

    def toggle_record(self):
        if self.busy:
            return
        if self.recording:
            self._stop_and_transcribe()
            return
        self._start_record()

    def _start_record(self):
        try:
            import sounddevice  # noqa: F401
        except ImportError:
            messagebox.showerror(
                "缺少套件",
                "未安裝 sounddevice。\n請執行：pip install -U -r requirements-whisper-gui.txt",
            )
            return
        if self._selected_device() is None and list_input_devices():
            messagebox.showerror("咪高峰", "請先揀一個咪高峰裝置")
            return
        try:
            self.recorder.start(device=self._selected_device())
        except Exception as exc:
            messagebox.showerror("無法開始錄音", str(exc))
            return
        self.recording = True
        self.timer_var.set("00:00")
        self.record_btn.configure(text="停止並轉寫")
        self.status_var.set("錄音中… 再按一次或 Ctrl+Alt+R 停止")

    def _stop_and_transcribe(self):
        if not self.recording:
            return
        self.recording = False
        audio = self.recorder.stop()
        duration = len(audio) / float(SAMPLE_RATE) if audio is not None else 0
        self.timer_var.set(format_duration(duration))
        self.record_btn.configure(text="開始錄音")
        if duration < MIN_RECORD_SECONDS:
            self.status_var.set("錄音太短，請再試")
            return
        self.busy = True
        self.record_btn.configure(state=tk.DISABLED, text="轉寫中…")
        model = self.model_var.get()
        language = language_code(self.language_var.get())
        self.status_var.set(f"轉寫中（{model}）… 第一次會下載模型")
        threading.Thread(
            target=self._transcribe_worker,
            args=(audio, model, language),
            daemon=True,
        ).start()

    def _transcribe_worker(self, audio, model, language):
        try:
            text = self.engine.transcribe(audio, model, language)
        except Exception as exc:
            self.ui_queue.put(("error", str(exc)))
            self.ui_queue.put(("idle",))
            return
        self.ui_queue.put(("text", text))
        self.ui_queue.put(("status", "完成" if text else "沒有辨識到內容"))
        self.ui_queue.put(("idle",))

    def _set_result(self, text: str):
        self.text.delete("1.0", tk.END)
        if text:
            self.text.insert("1.0", text)
        if text and self.auto_copy_var.get():
            self.copy_text(silent=True)
        if text and self.auto_paste_var.get():
            self.after(150, self.paste_to_last_window)

    def current_text(self) -> str:
        return self.text.get("1.0", tk.END).strip()

    def copy_text(self, silent=False):
        text = self.current_text()
        if not text:
            if not silent:
                self.status_var.set("沒有文字可複製")
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update_idletasks()
        self.status_var.set("已複製到剪貼簿")

    def paste_to_last_window(self):
        text = self.current_text()
        if not text:
            self.status_var.set("沒有文字可輸入")
            return
        if not is_windows():
            self.copy_text(silent=True)
            messagebox.showinfo(
                "直接輸入",
                "呢個功能只支援 Windows。\n文字已複製，請喺目標程式按 Ctrl+V。",
            )
            return
        if not self.last_hwnd:
            self.copy_text(silent=True)
            messagebox.showinfo(
                "搵唔到上一視窗",
                "請先撳一下目標程式（例如記事本），再返嚟按「輸入到上一視窗」。\n文字已複製。",
            )
            return
        self.copy_text(silent=True)
        if not focus_window(self.last_hwnd):
            self.status_var.set("無法切換到上一視窗，已複製文字")
            return
        self.after(180, lambda: self._send_to_foreground(text))

    def _send_to_foreground(self, text: str):
        try:
            inject_text(text)
            self.status_var.set("已輸入到上一視窗")
        except Exception as exc:
            messagebox.showerror("輸入失敗", f"{exc}\n文字已喺剪貼簿，可手動 Ctrl+V。")

    def clear_text(self):
        self.text.delete("1.0", tk.END)
        self.status_var.set("已清空")

    def show_help(self):
        messagebox.showinfo(
            "使用說明",
            "1. pip install -U -r requirements-whisper-gui.txt\n"
            "2. 安裝 ffmpeg 並加到 PATH（Whisper 官方建議）\n"
            "3. 雙擊 start_whisper_gui.bat\n"
            "4. 揀模型：CPU 用 tiny/base/small；有獨立顯示卡可用 turbo\n"
            "5. 撳「開始錄音」或 Ctrl+Alt+R，講完再按一次\n"
            "6. 「輸入到上一視窗」會把字打入你先前聚焦嘅程式\n\n"
            "首次使用會下載模型，需要網絡同較多磁碟空間。",
        )

    def _on_close(self):
        self.recording = False
        try:
            self.recorder.stop()
        except Exception:
            pass
        if self.hotkey:
            self.hotkey.stop()
        self.destroy()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Windows Whisper 語音輸入 GUI")
    parser.add_argument("--model", default="base", choices=MODELS)
    parser.add_argument(
        "--language",
        default="中文",
        choices=[name for name, _code in LANGUAGES],
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        app = WhisperInputApp(model=args.model, language_label=args.language)
    except tk.TclError as exc:
        print("無法開啟視窗。Windows 請用 python 啟動；呢個環境可能冇顯示。", exc)
        return 1
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
