#!/usr/bin/env python3
"""Watch inbox/ and turn long videos into short vertical clips with local captions."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parent
INBOX = ROOT / "inbox"
READY = ROOT / "ready"
STATE_FILE = ROOT / "processed.json"
MODEL_DIR = ROOT / "models"
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".mkv", ".webm"}
MODEL_NAME = "base"
TARGET_SECONDS = 32
MIN_SECONDS = 14
MAX_SECONDS = 55
MAX_CLIPS = 5
POLL_SECONDS = 5
STABLE_CHECKS = 3


@dataclass
class TranscriptLine:
    start: float
    end: float
    text: str


@dataclass
class ClipWindow:
    start: float
    end: float
    text: str


def run(command: list[str], *, capture: bool = False) -> str:
    result = subprocess.run(
        command,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
        stderr=subprocess.PIPE if capture else None,
    )
    return (result.stdout + (result.stderr or "")) if capture else ""


def load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logging.warning("Could not read %s; starting with an empty state.", STATE_FILE)
        return {}


def save_state(state: dict[str, Any]) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def file_key(path: Path) -> str:
    stat = path.stat()
    return f"{path.resolve()}::{stat.st_size}::{stat.st_mtime_ns}"


def stable_file(path: Path) -> bool:
    previous_size = -1
    same_count = 0
    while same_count < STABLE_CHECKS:
        try:
            current_size = path.stat().st_size
        except FileNotFoundError:
            return False
        if current_size > 0 and current_size == previous_size:
            same_count += 1
        else:
            same_count = 0
        previous_size = current_size
        time.sleep(POLL_SECONDS)
    return True


def media_duration(path: Path) -> float:
    raw = run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ],
        capture=True,
    ).strip()
    return float(raw)


def transcribe(path: Path) -> list[TranscriptLine]:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError(
            "Speech-to-text is not installed yet. Run start.command to install the free local package."
        ) from exc

    logging.info("Transcribing locally (first run downloads the free %s model).", MODEL_NAME)
    model = WhisperModel(
        MODEL_NAME,
        device="cpu",
        compute_type="int8",
        download_root=str(MODEL_DIR),
    )
    segments, info = model.transcribe(str(path), vad_filter=True, beam_size=5)
    logging.info("Detected language: %s", info.language)
    lines: list[TranscriptLine] = []
    for segment in segments:
        text = re.sub(r"\s+", " ", segment.text).strip()
        if text and segment.end > segment.start:
            lines.append(TranscriptLine(float(segment.start), float(segment.end), text))
    return lines


def build_windows(lines: list[TranscriptLine], duration: float) -> list[ClipWindow]:
    """Group transcript sentences into speech-centered clips up to 55 seconds."""
    windows: list[ClipWindow] = []
    index = 0
    while index < len(lines):
        first = lines[index]
        start = max(0.0, first.start - 0.25)
        end = first.end
        texts = [first.text]
        index += 1

        while index < len(lines):
            line = lines[index]
            proposed_end = min(duration, line.end + 0.25)
            if proposed_end - start > MAX_SECONDS:
                break
            # Prefer a natural pause as a cut when the clip is already long enough.
            if line.start - end > 2.5 and end - start >= MIN_SECONDS:
                break
            end = line.end
            texts.append(line.text)
            index += 1
            if end - start >= TARGET_SECONDS:
                break

        end = min(duration, end + 0.25)
        if end - start >= MIN_SECONDS:
            windows.append(ClipWindow(start, end, " ".join(texts)))
        elif index < len(lines):
            # Merge a very short remainder with the next transcript section.
            next_line = lines[index]
            start = max(0.0, min(start, next_line.start - 0.25))
            end = min(duration, next_line.end + 0.25)
            windows.append(ClipWindow(start, end, " ".join(texts + [next_line.text])))
            index += 1

    if len(windows) > MAX_CLIPS:
        # Spread the selected clips through the source instead of taking only its opening.
        picks = [round(i * (len(windows) - 1) / (MAX_CLIPS - 1)) for i in range(MAX_CLIPS)]
        windows = [windows[i] for i in picks]
    return windows


def srt_time(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, millis = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{whole_seconds:02},{millis:03}"


def write_srt(path: Path, lines: list[TranscriptLine], window: ClipWindow) -> None:
    entries: list[str] = []
    for line in lines:
        start = max(line.start, window.start)
        end = min(line.end, window.end)
        if end <= start:
            continue
        entries.append(
            f"{len(entries) + 1}\n"
            f"{srt_time(start - window.start)} --> {srt_time(end - window.start)}\n"
            f"{line.text}\n"
        )
    path.write_text("\n".join(entries), encoding="utf-8")


def wrap_caption(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    words = text.split()
    rows: list[str] = []
    current = ""
    probe = Image.new("RGBA", (2, 2))
    draw = ImageDraw.Draw(probe)
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and draw.textlength(candidate, font=font) > max_width:
            rows.append(current)
            current = word
        else:
            current = candidate
    if current:
        rows.append(current)
    return rows[:3]


def caption_image(text: str, path: Path) -> None:
    width, height = 940, 220
    font_candidates = [
        Path("C:/Windows/Fonts/Nirmala.ttc"),
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("C:/Windows/Fonts/arialbd.ttf"),
        Path("/System/Library/Fonts/Supplemental/Devanagari Sangam MN.ttc"),
        Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    ]
    font_path = next((candidate for candidate in font_candidates if candidate.exists()), None)
    font = ImageFont.truetype(str(font_path), 42) if font_path else ImageFont.load_default(size=42)
    rows = wrap_caption(text, font, width - 72)
    display_text = "\n".join(rows)
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    text_box = draw.multiline_textbbox((0, 0), display_text, font=font, spacing=6, align="center")
    text_width = text_box[2] - text_box[0]
    text_height = text_box[3] - text_box[1]
    left = max(20, (width - text_width) // 2 - 28)
    top = max(15, (height - text_height) // 2 - 18)
    right = min(width - 20, (width + text_width) // 2 + 28)
    bottom = min(height - 15, (height + text_height) // 2 + 18)
    draw.rounded_rectangle((left, top, right, bottom), radius=18, fill=(0, 0, 0, 175))
    draw.multiline_text(
        (width // 2, height // 2),
        display_text,
        font=font,
        fill=(255, 255, 255, 255),
        anchor="mm",
        align="center",
        spacing=6,
        stroke_width=1,
        stroke_fill=(0, 0, 0, 255),
    )
    image.save(path)


def make_clip(
    source: Path,
    output: Path,
    window: ClipWindow,
    captions: list[tuple[Path, float, float]],
) -> None:
    # Keep the full horizontal shot visible over a blurred 9:16 background.
    graph = (
        "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,boxblur=20:10[bg];"
        "[0:v]scale=1080:1920:force_original_aspect_ratio=decrease[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1"
    )
    graph += "[base]"
    input_index = 1
    current = "base"
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{window.start:.3f}", "-t", f"{window.end - window.start:.3f}", "-i", str(source),
    ]
    for index, (image_path, caption_start, caption_end) in enumerate(captions, start=1):
        command.extend(
            ["-loop", "1", "-framerate", "30", "-t", f"{window.end - window.start:.3f}", "-i", str(image_path)]
        )
        next_label = f"caption{index}"
        start = max(0.0, caption_start)
        end = min(window.end - window.start, caption_end)
        graph += (
            f";[{current}][{input_index}:v]overlay=(W-w)/2:H-h-250:"
            f"enable='between(t,{start:.3f},{end:.3f})'[{next_label}]"
        )
        current = next_label
        input_index += 1
    graph += f";[{current}]null[vout]"
    command.extend(
        [
            "-filter_complex", graph, "-map", "[vout]", "-map", "0:a?",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
            "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
            "-t", f"{window.end - window.start:.3f}", str(output),
        ]
    )
    run(command)


def safe_stem(path: Path) -> str:
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", path.stem).strip("_")
    return stem or "video"


def post_caption(text: str) -> str:
    clean = re.sub(r"\s+", " ", text).strip()
    if len(clean) > 220:
        clean = clean[:217].rsplit(" ", 1)[0] + "..."
    return clean


def process(path: Path) -> None:
    duration = media_duration(path)
    transcript = transcribe(path)
    windows = build_windows(transcript, duration)
    if not windows:
        raise RuntimeError("No speech was detected, so no clips were made.")

    source_revision = hashlib.sha256(file_key(path).encode("utf-8")).hexdigest()[:8]
    destination = READY / f"{safe_stem(path)}_{source_revision}"
    destination.mkdir(parents=True, exist_ok=True)
    queue_rows: list[dict[str, str]] = []

    for number, window in enumerate(windows, start=1):
        clip_name = f"clip_{number:02d}"
        video_path = destination / f"{clip_name}.mp4"
        subtitle_path = destination / f"{clip_name}.srt"
        caption_path = destination / f"{clip_name}_post.txt"
        write_srt(subtitle_path, transcript, window)
        clip_captions: list[tuple[Path, float, float]] = []
        with tempfile.TemporaryDirectory(prefix="repurpose-captions-") as caption_temp:
            caption_temp_path = Path(caption_temp)
            for line_number, line in enumerate(transcript, start=1):
                start = max(line.start, window.start)
                end = min(line.end, window.end)
                if end <= start:
                    continue
                image_path = caption_temp_path / f"caption_{line_number:04d}.png"
                caption_image(line.text, image_path)
                clip_captions.append((image_path, start - window.start, end - window.start))
            make_clip(path, video_path, window, clip_captions)
        caption = post_caption(window.text)
        caption_path.write_text(
            f"{caption}\n\n#shorts #reels #tiktok\n",
            encoding="utf-8",
        )
        queue_rows.append(
            {
                "video": str(video_path.resolve()),
                "subtitles": str(subtitle_path.resolve()),
                "caption": caption,
                "status": "ready_for_manual_platform_schedule",
            }
        )
        logging.info("Made %s", video_path.relative_to(ROOT))

    with (destination / "post_queue.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=["video", "subtitles", "caption", "status"])
        writer.writeheader()
        writer.writerows(queue_rows)

    (destination / "transcript.txt").write_text(
        "\n".join(line.text for line in transcript), encoding="utf-8"
    )
    logging.info(
        "Finished %s: %d clips. %s",
        path.name,
        len(windows),
        "Captions are burned into each video and saved as SRT files.",
    )
    try:
        from buffer_publish import schedule_directory

        if all(
            os.environ.get(name, "").strip()
            for name in ("BUFFER_API_KEY", "BUFFER_CHANNELS", "PUBLIC_MEDIA_BASE_URL")
        ) and os.environ.get("ALLOW_PUBLIC_MEDIA", "").strip().lower() == "true":
            count = schedule_directory(destination)
            logging.info("Scheduled %d posts through Buffer.", count)
            if count:
                for row in queue_rows:
                    row["status"] = "scheduled_via_buffer"
                with (destination / "post_queue.csv").open("w", newline="", encoding="utf-8-sig") as handle:
                    writer = csv.DictWriter(handle, fieldnames=["video", "subtitles", "caption", "status"])
                    writer.writeheader()
                    writer.writerows(queue_rows)
        else:
            logging.info("Buffer auto-scheduling is off; clips are ready in the local queue.")
    except Exception as exc:
        logging.exception("Buffer scheduling did not complete: %s", exc)
        if os.environ.get("ALLOW_PUBLIC_MEDIA", "").strip().lower() == "true":
            raise


def scan() -> list[Path]:
    INBOX.mkdir(parents=True, exist_ok=True)
    READY.mkdir(parents=True, exist_ok=True)
    return sorted(
        path for path in INBOX.iterdir()
        if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
    )


def main() -> int:
    from buffer_publish import load_local_env

    load_local_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Process existing inbox videos and exit.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    state = load_state()
    logging.info("Watching %s", INBOX)
    while True:
        found_work = False
        for path in scan():
            key = file_key(path)
            previous = state.get(key, {})
            if previous.get("result") == "success":
                continue
            if previous.get("result") == "error" and time.time() < previous.get("retry_after", 0):
                continue
            found_work = True
            logging.info("Waiting for file copy to finish: %s", path.name)
            if not stable_file(path):
                continue
            try:
                logging.info("Processing %s", path.name)
                process(path)
                state[key] = {"processed_at": time.strftime("%Y-%m-%d %H:%M:%S"), "result": "success"}
                save_state(state)
            except Exception as exc:  # Keep watching so one bad file does not stop the queue.
                logging.exception("Could not process %s: %s", path.name, exc)
                state[key] = {
                    "processed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "retry_after": time.time() + 60,
                    "result": "error",
                    "error": str(exc),
                }
                save_state(state)
        if args.once:
            return 0
        if not found_work:
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    sys.exit(main())
