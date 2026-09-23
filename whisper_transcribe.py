#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本機語音轉文字 - openai-whisper - 眼鏡妹

openai-whisper 係 OpenAI 開源嘅本機語音轉文字工具，唔使 API key。
裝好之後可以轉寫 mp3 / wav / m4a / flac 等音訊。

安裝：
  pip install -U openai-whisper
  # Ubuntu/Debian
  sudo apt update && sudo apt install ffmpeg
  # macOS
  brew install ffmpeg
  # 或一次過裝 CLI：brew install openai-whisper

用法：
  python whisper_transcribe.py audio.mp3
  python whisper_transcribe.py audio.m4a --model small --language Chinese
  python whisper_transcribe.py audio.wav --task translate --out ./out
  python whisper_transcribe.py --help
"""

import argparse
import os
import shutil
import subprocess
import sys

MODELS = (
    "tiny",
    "tiny.en",
    "base",
    "base.en",
    "small",
    "small.en",
    "medium",
    "medium.en",
    "large",
    "large-v2",
    "large-v3",
    "turbo",
)


def find_whisper_cli():
    return shutil.which("whisper")


def transcribe_with_cli(audio, model, language, task, output_format, output_dir):
    cmd = [
        find_whisper_cli(),
        audio,
        "--model",
        model,
        "--task",
        task,
        "--output_format",
        output_format,
        "--output_dir",
        output_dir,
    ]
    if language:
        cmd.extend(["--language", language])

    print("🎤 用 Whisper CLI 轉寫")
    print("指令:", " ".join(cmd))
    return subprocess.call(cmd)


def transcribe_with_python(audio, model, language, task, output_format, output_dir):
    try:
        import whisper
    except ImportError:
        print("未找到 whisper。請先安裝：")
        print("  pip install -U openai-whisper")
        print("  並安裝 ffmpeg（例如：sudo apt install ffmpeg 或 brew install ffmpeg）")
        return 1

    print(f"🎤 用 Python API 載入模型：{model}")
    loaded = whisper.load_model(model)
    options = {"task": task}
    if language:
        options["language"] = language

    result = loaded.transcribe(audio, **options)
    text = (result.get("text") or "").strip()
    detected = result.get("language", "")

    os.makedirs(output_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(audio))[0]
    out_path = os.path.join(output_dir, f"{base}.{output_format}")

    if output_format == "json":
        import json

        with open(out_path, "w", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2)
    elif output_format == "srt":
        with open(out_path, "w", encoding="utf-8") as handle:
            handle.write(_segments_to_srt(result.get("segments") or []))
    else:
        with open(out_path, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")

    if detected:
        print(f"偵測語言: {detected}")
    print(text)
    print(f"已寫入: {out_path}")
    return 0


def _segments_to_srt(segments):
    lines = []
    for index, segment in enumerate(segments, start=1):
        start = _format_srt_time(segment.get("start", 0))
        end = _format_srt_time(segment.get("end", 0))
        text = (segment.get("text") or "").strip()
        lines.append(f"{index}\n{start} --> {end}\n{text}\n")
    return "\n".join(lines)


def _format_srt_time(seconds):
    total_ms = int(round(float(seconds) * 1000))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def transcribe(audio, model="base", language=None, task="transcribe",
               output_format="txt", output_dir="."):
    if not os.path.isfile(audio):
        print(f"找不到音訊檔：{audio}")
        return 1

    if find_whisper_cli():
        return transcribe_with_cli(
            audio, model, language, task, output_format, output_dir
        )
    return transcribe_with_python(
        audio, model, language, task, output_format, output_dir
    )


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="用 openai-whisper 本機把音訊轉成文字（唔使 API key）"
    )
    parser.add_argument("audio", help="音訊檔路徑，例如 recording.m4a")
    parser.add_argument(
        "--model",
        default="base",
        choices=MODELS,
        help="模型：細=快、大=準。CPU 建議 tiny/base/small，有 GPU 可用 turbo",
    )
    parser.add_argument(
        "--language",
        default=None,
        help="指定語言可加快速度，例如 Chinese、English、Japanese。不指定則自動偵測",
    )
    parser.add_argument(
        "--task",
        default="transcribe",
        choices=("transcribe", "translate"),
        help="transcribe=原語言轉寫；translate=譯成英文（turbo 不適合翻譯）",
    )
    parser.add_argument(
        "--format",
        dest="output_format",
        default="txt",
        choices=("txt", "srt", "json"),
        help="輸出格式",
    )
    parser.add_argument(
        "--out",
        dest="output_dir",
        default=".",
        help="輸出資料夾，預設係而家呢個目錄",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    return transcribe(
        audio=args.audio,
        model=args.model,
        language=args.language,
        task=args.task,
        output_format=args.output_format,
        output_dir=args.output_dir,
    )


if __name__ == "__main__":
    sys.exit(main())
