@echo off
chcp 65001 >nul
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo 搵唔到 python。請先安裝 Python 3.10+，安裝時剔選 "Add python.exe to PATH"。
    pause
    exit /b 1
)

python -c "import whisper, sounddevice" >nul 2>nul
if errorlevel 1 (
    echo 第一次使用，正在安裝套件...
    python -m pip install -U -r requirements-whisper-gui.txt
    if errorlevel 1 (
        echo 套件安裝失敗。
        pause
        exit /b 1
    )
)

echo 啟動 Whisper 語音輸入...
python whisper_gui.py
if errorlevel 1 pause
