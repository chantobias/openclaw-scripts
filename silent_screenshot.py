#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""靜默全螢幕截圖 - 眼鏡妹

擷取整個螢幕（預設為所有螢幕合併的虛擬桌面）。
過程完全靜默：不閃白光、不播放快門聲、不彈出視窗或通知、不移動滑鼠。

依賴：
    pip install -r requirements.txt

用法：
    python silent_screenshot.py
    python silent_screenshot.py -o /tmp/screen.png
    python silent_screenshot.py -q
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

try:
    import mss
    from mss.exception import ScreenShotError
    from mss.tools import to_png
except ImportError:  # pragma: no cover
    mss = None
    ScreenShotError = OSError  # type: ignore[misc, assignment]
    to_png = None

try:
    from PIL import ImageGrab
except ImportError:  # pragma: no cover
    ImageGrab = None


def default_output_path() -> Path:
    """預設存到使用者圖片目錄，檔名帶時間戳。"""
    pictures = Path.home() / "Pictures" / "Screenshots"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return pictures / f"screenshot_{stamp}.png"


def _resolve_dest(output: str | Path | None) -> Path:
    dest = Path(output) if output else default_output_path()
    dest = dest.expanduser().resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)
    return dest


def _capture_mss(dest: Path, monitor: int) -> Path:
    if mss is None or to_png is None:
        raise RuntimeError("mss 未安裝")

    # 新版 mss 建議用 MSS()；舊版則是 mss()
    factory = getattr(mss, "MSS", None) or mss.mss
    with factory() as sct:
        monitors = sct.monitors
        if monitor < 0 or monitor >= len(monitors):
            raise ValueError(f"無效的螢幕編號 {monitor}，可用範圍為 0–{len(monitors) - 1}")
        shot = sct.grab(monitors[monitor])
        to_png(shot.rgb, shot.size, output=str(dest))
    return dest


def _capture_pil(dest: Path) -> Path:
    if ImageGrab is None:
        raise RuntimeError("Pillow 未安裝")

    kwargs: dict = {}
    if sys.platform == "win32":
        kwargs["all_screens"] = True
    # macOS 的 ImageGrab 使用 screencapture -x（關閉快門聲）
    # Windows / Linux-XCB 走系統 API，不會閃光或發聲
    image = ImageGrab.grab(**kwargs)
    image.save(dest, format="PNG")
    return dest


def _x11_root_size() -> tuple[int, int] | None:
    if not shutil.which("xwininfo"):
        return None
    try:
        out = subprocess.check_output(["xwininfo", "-root"], text=True, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError):
        return None
    width = height = None
    for line in out.splitlines():
        stripped = line.strip()
        if stripped.startswith("Width:"):
            width = int(stripped.split(":", 1)[1])
        elif stripped.startswith("Height:"):
            height = int(stripped.split(":", 1)[1])
    if width and height:
        return width, height
    return None


def _capture_ffmpeg(dest: Path) -> Path:
    """Linux X11 後備：ffmpeg x11grab 單幀，不開視窗、不播聲音。"""
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg 未安裝")
    display = os.environ.get("DISPLAY")
    if not display:
        raise RuntimeError("沒有 DISPLAY，無法用 ffmpeg 擷取 X11 畫面")

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "x11grab",
        "-draw_mouse",
        "0",
    ]
    size = _x11_root_size()
    if size:
        cmd.extend(["-video_size", f"{size[0]}x{size[1]}"])
    cmd.extend(["-i", display, "-frames:v", "1", "-update", "1", str(dest)])

    completed = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if completed.returncode != 0 or not dest.is_file() or dest.stat().st_size == 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(detail or "ffmpeg 擷取失敗")
    return dest


def capture_fullscreen(output: str | Path | None = None, monitor: int = 0) -> Path:
    """靜默擷取全螢幕並存成 PNG。

    依序嘗試 mss → Pillow ImageGrab → ffmpeg x11grab，
    都不會閃光、發聲、彈窗或移動游標。

    Args:
        output: 儲存路徑。省略時寫入 ~/Pictures/Screenshots/screenshot_時間戳.png
        monitor: 0 代表所有螢幕合併；1 起為單一螢幕（僅 mss 支援）。

    Returns:
        實際寫入的檔案路徑。
    """
    dest = _resolve_dest(output)
    errors: list[str] = []

    try:
        return _capture_mss(dest, monitor)
    except ValueError:
        raise
    except Exception as exc:
        errors.append(f"mss: {exc}")

    # 後備後端只能擷取整塊虛擬桌面；monitor 1 在單螢幕上等同全螢幕
    if monitor > 1:
        raise RuntimeError(f"無法指定螢幕 {monitor}。省略 --monitor 可擷取全螢幕。")

    try:
        return _capture_pil(dest)
    except Exception as exc:
        errors.append(f"pillow: {exc}")

    if sys.platform.startswith("linux"):
        try:
            return _capture_ffmpeg(dest)
        except Exception as exc:
            errors.append(f"ffmpeg: {exc}")

    raise RuntimeError("無法擷取螢幕。" + "；".join(errors))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="靜默擷取全螢幕。過程不閃光、不發聲、不彈窗、不移動游標。",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="儲存路徑（預設 ~/Pictures/Screenshots/screenshot_時間戳.png）",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="不輸出任何文字",
    )
    parser.add_argument(
        "--monitor",
        type=int,
        default=0,
        help="螢幕編號：0=全部合併（預設），1 起為單一螢幕",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        path = capture_fullscreen(output=args.output, monitor=args.monitor)
    except (RuntimeError, ValueError, OSError, ScreenShotError) as exc:
        if not args.quiet:
            print(f"錯誤: {exc}", file=sys.stderr)
        return 1

    if not args.quiet:
        # 只印路徑，方便腳本串接；桌面上不會有任何動靜
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
