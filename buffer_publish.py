#!/usr/bin/env python3
"""Optional Buffer API scheduling for clips served at a public HTTPS URL."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
READY = ROOT / "ready"
ENDPOINT = "https://api.buffer.com"


def load_local_env() -> None:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key:
            os.environ.setdefault(key, value)


def buffer_request(api_key: str, query: str) -> dict:
    body = json.dumps({"query": query}).encode("utf-8")
    request = Request(
        ENDPOINT,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise RuntimeError(f"Buffer API returned HTTP {exc.code}.") from exc
    except URLError as exc:
        raise RuntimeError(f"Could not reach Buffer API: {exc.reason}") from exc
    if payload.get("errors"):
        messages = "; ".join(error.get("message", "Unknown API error") for error in payload["errors"])
        raise RuntimeError(f"Buffer API error: {messages}")
    return payload.get("data", {})


def list_channels(api_key: str) -> list[dict[str, str]]:
    query = """query GetAccountChannels {
      account { organizations { id name } }
    }"""
    data = buffer_request(api_key, query)
    rows: list[dict[str, str]] = []
    for organization in data.get("account", {}).get("organizations", []):
        organization_id = organization["id"]
        channel_query = f"""query GetChannels {{
          channels(input: {{ organizationId: {json.dumps(organization_id)} }}) {{
            id name displayName service
          }}
        }}"""
        channels = buffer_request(api_key, channel_query).get("channels", [])
        for channel in channels:
            rows.append(
                {
                    "organization": organization.get("name", ""),
                    "id": channel["id"],
                    "name": channel.get("displayName") or channel.get("name", ""),
                    "service": channel.get("service", ""),
                }
            )
    return rows


def parse_channels(value: str) -> dict[str, str]:
    channels: dict[str, str] = {}
    for item in value.split(","):
        if not item.strip():
            continue
        service, separator, channel_id = item.partition(":")
        if not separator or not service.strip() or not channel_id.strip():
            raise ValueError("BUFFER_CHANNELS format must be service:ID,service:ID,service:ID")
        channels[service.strip().lower()] = channel_id.strip()
    return channels


def verify_public_video(url: str) -> None:
    request = Request(url, method="HEAD", headers={"User-Agent": "LocalVideoRepurposer/1.0"})
    try:
        with urlopen(request, timeout=15) as response:
            if response.status != 200:
                raise RuntimeError(f"Public media URL returned HTTP {response.status}: {url}")
    except HTTPError as exc:
        raise RuntimeError(f"Public media URL returned HTTP {exc.code}: {url}") from exc
    except URLError as exc:
        raise RuntimeError(f"Cannot reach public media URL {url}: {exc.reason}") from exc


def make_post_query(service: str, channel_id: str, text: str, video_url: str) -> str:
    metadata = ""
    if service == "instagram":
        metadata = "metadata: { instagram: { type: reel, shouldShareToFeed: true } }"
    elif service == "youtube":
        title = text.split("\n", 1)[0].strip()[:90] or "New short video"
        if "#shorts" not in title.lower():
            title = f"{title} #Shorts"
        metadata = (
            "metadata: { youtube: { "
            f"title: {json.dumps(title)}, categoryId: \"22\", type: short, privacy: public"
            " } }"
        )
    return f"""mutation ScheduleVideo {{
      createPost(input: {{
        text: {json.dumps(text)}
        channelId: {json.dumps(channel_id)}
        schedulingType: automatic
        mode: addToQueue
        assets: [{{ video: {{ url: {json.dumps(video_url)} }} }}]
        {metadata}
      }}) {{
        __typename
        ... on PostActionSuccess {{ post {{ id dueAt status }} }}
        ... on MutationError {{ message }}
      }}
    }}"""


def schedule_directory(directory: Path, *, dry_run: bool = False) -> int:
    api_key = os.environ.get("BUFFER_API_KEY", "").strip()
    channel_value = os.environ.get("BUFFER_CHANNELS", "").strip()
    media_base = os.environ.get("PUBLIC_MEDIA_BASE_URL", "").strip().rstrip("/")
    allow_public = os.environ.get("ALLOW_PUBLIC_MEDIA", "").strip().lower() == "true"
    if not api_key or not channel_value or not media_base:
        raise RuntimeError("Set BUFFER_API_KEY, BUFFER_CHANNELS, and PUBLIC_MEDIA_BASE_URL in .env first.")
    if not media_base.startswith("https://"):
        raise RuntimeError("PUBLIC_MEDIA_BASE_URL must start with https://")
    if not allow_public:
        raise RuntimeError("Set ALLOW_PUBLIC_MEDIA=true only after approving public video URLs before posting.")

    channels = parse_channels(channel_value)
    if not channels:
        raise RuntimeError("No Buffer channels configured.")
    service_names = {"instagram": "instagram", "tiktok": "tiktok", "youtube": "youtube"}
    unsupported = sorted(set(channels) - set(service_names))
    if unsupported:
        raise RuntimeError(f"Unsupported configured Buffer services: {', '.join(unsupported)}")

    queued = 0
    state_file = directory / ".buffer_schedule_state.json"
    try:
        schedule_state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else {}
    except (OSError, json.JSONDecodeError):
        schedule_state = {}
    for video_path in sorted(directory.glob("clip_*.mp4")):
        relative = video_path.resolve().relative_to(READY.resolve()).as_posix()
        video_url = f"{media_base}/{quote(relative, safe='/')}"
        if not dry_run:
            verify_public_video(video_url)
        caption_file = video_path.with_name(f"{video_path.stem}_post.txt")
        post_text = caption_file.read_text(encoding="utf-8").strip() if caption_file.exists() else "#shorts #reels #tiktok"
        for service, channel_id in channels.items():
            state_key = f"{video_path.name}:{service}:{channel_id}"
            if state_key in schedule_state and not dry_run:
                queued += 1
                print(f"Already queued {video_path.name} to {service}")
                continue
            query = make_post_query(service, channel_id, post_text, video_url)
            if dry_run:
                print(f"Would queue {video_path.name} to {service}: {video_url}")
                queued += 1
                continue
            data = buffer_request(api_key, query)
            result = data.get("createPost", {})
            if result.get("__typename") == "PostActionSuccess" or "post" in result:
                post = result.get("post", {})
                print(f"Queued {video_path.name} to {service} for {post.get('dueAt', 'Buffer queue')}")
                queued += 1
                schedule_state[state_key] = {
                    "post_id": post.get("id"),
                    "due_at": post.get("dueAt"),
                }
                state_file.write_text(json.dumps(schedule_state, indent=2), encoding="utf-8")
            elif result.get("__typename") == "MutationError" or "message" in result:
                raise RuntimeError(f"Buffer rejected {video_path.name} for {service}: {result.get('message')}")
            else:
                raise RuntimeError(f"Unexpected Buffer response for {service}: {result}")
    return queued


def main() -> int:
    load_local_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channels", action="store_true", help="Print connected Buffer channels and IDs.")
    parser.add_argument("--folder", type=Path, help="Schedule clips from one ready/<video-name> folder.")
    parser.add_argument("--dry-run", action="store_true", help="Show intended posts without sending them.")
    args = parser.parse_args()
    api_key = os.environ.get("BUFFER_API_KEY", "").strip()
    try:
        if args.channels:
            if not api_key:
                raise RuntimeError("Set BUFFER_API_KEY in .env first.")
            for channel in list_channels(api_key):
                print(f"{channel['service']}\t{channel['name']}\t{channel['id']}")
            return 0
        if not args.folder:
            parser.error("use --channels or provide --folder ready/<video-name>")
        folder = args.folder.resolve()
        folder.relative_to(READY.resolve())
        print(f"Queued {schedule_directory(folder, dry_run=args.dry_run)} channel posts.")
        return 0
    except (RuntimeError, ValueError) as exc:
        print(f"Buffer scheduling error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
