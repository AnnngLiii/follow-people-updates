#!/usr/bin/env python3

import argparse
import json
import re
import sys
from urllib.parse import parse_qs, urlparse

from youtube_transcript_api import YouTubeTranscriptApi


def extract_video_id(value: str) -> str:
    if re.fullmatch(r"[\w-]{11}", value):
        return value

    parsed = urlparse(value)
    if parsed.netloc.endswith("youtu.be"):
        video_id = parsed.path.strip("/").split("/", 1)[0]
        if video_id:
            return video_id

    if "youtube.com" in parsed.netloc:
        query_id = parse_qs(parsed.query).get("v", [])
        if query_id:
            return query_id[0]
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2 and parts[0] in {"embed", "shorts", "live"}:
            return parts[1]

    raise ValueError(f"Could not extract a YouTube video ID from '{value}'.")


def main():
    parser = argparse.ArgumentParser(description="Fetch a YouTube transcript by video URL or ID.")
    parser.add_argument("--url", help="YouTube video URL.")
    parser.add_argument("--video-id", help="YouTube video ID.")
    parser.add_argument("--languages", default="en,en-US", help="Comma-separated language preference list.")
    parser.add_argument("--json", action="store_true", help="Emit JSON.")
    args = parser.parse_args()

    if not args.url and not args.video_id:
        print("Error: provide --url or --video-id.", file=sys.stderr)
        return 1

    try:
        video_id = args.video_id or extract_video_id(args.url)
        languages = [item.strip() for item in args.languages.split(",") if item.strip()]
        api = YouTubeTranscriptApi()
        transcript = api.fetch(video_id, languages=languages, preserve_formatting=False)
        segments = [
            {
                "start": item.start,
                "duration": item.duration,
                "text": item.text,
            }
            for item in transcript
        ]
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps({"video_id": video_id, "segments": segments}, ensure_ascii=False, indent=2))
        return 0

    print(f"video_id: {video_id}")
    for segment in segments:
        print(f"[{segment['start']:.2f}] {segment['text']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
