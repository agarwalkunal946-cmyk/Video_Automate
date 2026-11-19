# Video Automate

A small local workflow that turns a long spoken video into short vertical clips with burned-in captions. Drop an MP4 or MOV into `inbox/`; clips, subtitle files, suggested captions, and a posting CSV appear in `ready/`.

## What it does

1. Watches `inbox/` for a finished video.
2. Transcribes speech locally with the free Whisper `base` model.
3. Picks up to five speech sections, usually around 32 seconds each and up to 55 seconds.
4. Creates vertical 9:16 MP4 clips and burns subtitles into each clip.
5. Writes an SRT file, a suggested post caption, and `post_queue.csv` beside the clips.

Clip selection uses speech timing and pauses. It does not choose moments based on virality. Review the output before posting. The original source video stays in `inbox/`; generated files stay in `ready/`.

## Requirements

- Python 3.11 or newer
- FFmpeg and FFprobe available on your command line
- Internet for first setup, to install Python packages and download the Whisper model; transcription then runs locally

## Get the project

```sh
git clone https://github.com/agarwalkunal946-cmyk/Video_Automate.git
cd Video_Automate
```

## macOS setup and run

1. Install [Homebrew](https://brew.sh/) if needed, then open Terminal and install FFmpeg:

   ```sh
   brew install ffmpeg
   ```

2. Open the project folder in Terminal and start the watcher:

   ```sh
   chmod +x start.command
   ./start.command
   ```

3. Keep the Terminal window open. Put a video in `inbox/`. Finished clips will appear in `ready/`.

On first run, `start.command` creates `.venv`, installs the free packages, and downloads the Whisper model. Those files stay on your computer and are excluded from Git.

## Windows setup and run

1. Install Python 3.11 or newer from [python.org](https://www.python.org/downloads/windows/). In the installer, enable **Add Python to PATH**.
2. Install Git and FFmpeg. FFmpeg's [download page](https://ffmpeg.org/download.html) links to Windows builds; after installation, confirm `ffmpeg` and `ffprobe` work in a new Command Prompt or PowerShell window.
3. Open the project folder in File Explorer and double-click `start_windows.bat`. Keep its window open while it watches for videos.
4. Put an MP4 or MOV in `inbox/`; clips appear in `ready/`.

The first run creates `.venv`, installs the free packages, and downloads the Whisper model. You only need internet for that initial setup.

## Process the current inbox once

If you do not want a watcher to keep running, open Terminal/Command Prompt in the project folder and run:

macOS:

```sh
.venv/bin/python repurpose.py --once
```

Windows:

```powershell
.\.venv\Scripts\python.exe repurpose.py --once
```

## Buffer: schedule posts

Buffer is optional. You can upload a clip and schedule it in Buffer yourself. Buffer's Free plan currently supports up to three social channels and 10 scheduled posts waiting per channel: [plans and limits](https://buffer.com/pricing).

### Automatically queue new clips in Buffer

The project includes an optional Buffer API connector. To turn it on:

1. Connect your Instagram, TikTok, or YouTube channels in Buffer and create an API key in Buffer's API settings.
2. Copy `.env.example` to `.env` (`cp .env.example .env` on macOS; `copy .env.example .env` on Windows). Add the API key and Buffer channel IDs. Never share or commit `.env`.
3. Set `PUBLIC_MEDIA_BASE_URL` to a stable HTTPS base URL that serves files from `ready/`.
4. Set `ALLOW_PUBLIC_MEDIA=true` only if you accept that anyone with a clip's direct URL can view it before the scheduled post.
5. Set posting times in Buffer. When the project finishes new clips, it sends them to the configured channel queues.

Buffer's API needs each video at a direct, publicly reachable URL. It cannot upload a local file or fetch a private Google Drive share link. The URL must remain available until the post goes out: [Buffer media requirements](https://developers.buffer.com/guides/hosting-media.html). A free Cloudinary or Cloudflare R2 tier may work as a host, but you must configure it to serve stable public MP4 links.

For a temporary demo, `serve_ready.py` serves only MP4 files locally:

```sh
.venv/bin/python serve_ready.py
```

You can expose port 8765 with a temporary [Cloudflare Quick Tunnel](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/), but it is for testing and has no uptime guarantee. Keep the tunnel and local server running until Buffer fetches the scheduled video. Use stable hosting for posts scheduled later.

Without a Buffer API key, channel IDs, a public media URL, and `ALLOW_PUBLIC_MEDIA=true`, this project only creates clips locally; it does not schedule them.

### Find Buffer channel IDs

After adding `BUFFER_API_KEY` to `.env`, run:

macOS:

```sh
.venv/bin/python buffer_publish.py --channels
```

Windows:

```powershell
.\.venv\Scripts\python.exe buffer_publish.py --channels
```

## Privacy and files

Speech transcription runs locally. The optional Buffer path sends the generated clip URL and caption to Buffer so it can schedule a post. Anyone who obtains a public clip URL may view that clip before it is published. Keep Buffer credentials in your local `.env` file; never paste them into a chat or commit them.
