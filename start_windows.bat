@echo off
setlocal
cd /d "%~dp0"

where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo FFmpeg was not found. Install it, restart this window, and try again.
  echo Download links: https://ffmpeg.org/download.html
  pause
  exit /b 1
)

where ffprobe >nul 2>nul
if errorlevel 1 (
  echo FFprobe was not found. Install FFmpeg with its ffprobe tool, restart this window, and try again.
  pause
  exit /b 1
)

where python >nul 2>nul
if errorlevel 1 (
  echo Python was not found. Install Python 3.11 or newer and enable Add Python to PATH.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" python -m venv .venv
if errorlevel 1 (
  echo Could not create the local Python environment.
  pause
  exit /b 1
)

".venv\Scripts\python.exe" -c "import faster_whisper, av, PIL; assert 11 <= int(av.__version__.split('.')[0]) < 19" >nul 2>nul
if errorlevel 1 (
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo Could not install the required packages.
    pause
    exit /b 1
  )
)

".venv\Scripts\python.exe" repurpose.py
pause
