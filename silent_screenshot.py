#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""靜默全螢幕截圖 - 眼鏡妹

擷取整個螢幕（預設為所有螢幕合併的虛擬桌面）。
過程完全靜默：不閃白光、不播放快門聲、不彈出視窗或通知、不移動滑鼠。

依賴：
    pip install mss pillow

用法：
    python silent_screenshot.py
    python silent_screenshot.py -o /tmp/screen.png
    python silent_screenshot.py -q
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

try:
    import mss
    from mss.tools import to_png
except ImportError:  # pragma: no cover - 執行時才需要
    mss = None
    to_png = None


def default_output_path() -> Path:
    """預設存到使用者圖片目錄，檔名帶時間戳。"""
    pictures = Path.home() / "Pictures" / "Screenshots"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return pictures / f"screenshot_{stamp}.png"


def capture_fullscreen(output: str | Path | None = None, monitor: int = 0) -> Path:
    """靜默擷取全螢幕並存成 PNG。

    Args:
        output: 儲存路徑。省略時寫入 ~/Pictures/Screenshots/screenshot_時間戳.png
        monitor: 0 代表所有螢幕合併；1 起為單一螢幕。

    Returns:
        實際寫入的檔案路徑。
    """
    if mss is None:
        raise RuntimeError("缺少套件 mss，請先執行：pip install mss pillow")

    dest = Path(output) if output else default_output_path()
    dest = dest.expanduser().resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)

    with mss.mss() as sct:
        monitors = sct.monitors
        if monitor < 0 or monitor >= len(monitors):
            raise ValueError(f"無效的螢幕編號 {monitor}，可用範圍為 0–{len(monitors) - 1}")
        shot = sct.grab(monitors[monitor])
        # mss 直接寫檔，不開視窗、不播聲音、不移動游標
        to_png(shot.rgb, shot.size, output=str(dest))

    return dest


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
    except (RuntimeError, ValueError, OSError) as exc:
        if not args.quiet:
            print(f"錯誤: {exc}", file=sys.stderr)
        return 1

    if not args.quiet:
        # 只印路徑，方便腳本串接；桌面上不會有任何動靜
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
