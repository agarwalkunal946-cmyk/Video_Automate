#!/bin/zsh
set -e
cd "$(dirname "$0")"

if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
  echo "FFmpeg is required. On macOS, install it with: brew install ffmpeg"
  read -r "?Press Return to close..."
  exit 1
fi

if [ ! -d .venv ]; then
  python3 -m venv .venv
fi

if ! .venv/bin/python -c 'import faster_whisper, av, PIL; assert 11 <= int(av.__version__.split(".")[0]) < 19' >/dev/null 2>&1; then
  .venv/bin/python -m pip install -r requirements.txt
fi
exec .venv/bin/python repurpose.py
