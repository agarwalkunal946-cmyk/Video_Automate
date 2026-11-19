#!/usr/bin/env python3
"""Serve only ready/*.mp4 files locally for an explicitly enabled public tunnel."""

from __future__ import annotations

import argparse
import mimetypes
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parent
READY = ROOT / "ready"


class VideoOnlyHandler(SimpleHTTPRequestHandler):
    server_version = "VideoOnlyServer/1.0"

    def translate_path(self, path: str) -> str:
        relative = unquote(urlsplit(path).path).lstrip("/")
        target = (READY / relative).resolve()
        try:
            target.relative_to(READY.resolve())
        except ValueError:
            return str(READY / "__not_found__")
        if target.suffix.lower() != ".mp4" or not target.is_file():
            return str(READY / "__not_found__")
        return str(target)

    def list_directory(self, path: str):
        self.send_error(404, "Not found")
        return None

    def send_head(self):
        path = Path(self.translate_path(self.path))
        if not path.is_file() or path.suffix.lower() != ".mp4":
            self.send_error(404, "Video not found")
            return None
        try:
            handle = path.open("rb")
        except OSError:
            self.send_error(404, "Video not found")
            return None
        stat = path.stat()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "video/mp4")
        self.send_header("Content-Length", str(stat.st_size))
        self.send_header("Last-Modified", self.date_time_string(stat.st_mtime))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        return handle

    def log_message(self, format: str, *args: object) -> None:
        print("video server:", format % args)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    READY.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), VideoOnlyHandler)
    print(f"Serving only MP4 clips from {READY} at http://127.0.0.1:{args.port}")
    print("Press Ctrl+C to stop. Directory listing and non-MP4 files are blocked.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping video server.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
