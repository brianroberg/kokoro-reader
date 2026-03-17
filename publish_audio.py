#!/usr/bin/env python3
"""
Publish audio files to an Audiobookshelf server.
"""

import argparse
import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv()


def _find_library(abs_url, headers, media_type, library_name=None, libraries=None):
    """Find a library by media type and optional name.

    Args:
        abs_url: Audiobookshelf server URL
        headers: Auth headers dict
        media_type: "podcast" or "book"
        library_name: Optional library name to match (case-insensitive)
        libraries: Pre-fetched library list (for testing). If None, fetches from API.

    Returns:
        Tuple of (library_id, folder_id)

    Raises:
        ValueError: If no matching library found
        RuntimeError: If the API call fails
    """
    if libraries is None:
        resp = requests.get(f"{abs_url}/api/libraries", headers=headers)
        if resp.status_code != 200:
            raise RuntimeError(f"Failed to list libraries: {resp.status_code} {resp.text}")
        libraries = resp.json().get("libraries", [])

    matches = [lib for lib in libraries if lib.get("mediaType") == media_type]

    if library_name:
        matches = [lib for lib in matches if lib["name"].lower() == library_name.lower()]

    if not matches:
        available = [lib["name"] for lib in libraries]
        raise ValueError(
            f"No {media_type} library found"
            + (f" named '{library_name}'" if library_name else "")
            + f". Available: {', '.join(available)}"
        )

    lib = matches[0]
    folders = lib.get("folders", [])
    if not folders:
        raise ValueError(f"Library '{lib['name']}' has no folders configured")

    return lib["id"], folders[0]["id"]


def _upload(abs_url, headers, audio_path, library_id, folder_id, title, author=None):
    """Upload an audio file to Audiobookshelf.

    Args:
        abs_url: Audiobookshelf server URL
        headers: Auth headers dict
        audio_path: Path to the audio file
        library_id: Target library ID
        folder_id: Target folder ID
        title: Item title (podcast name for podcasts, book title for books)
        author: Author name (used for book directory structure)

    Raises:
        RuntimeError: If the upload fails
    """
    data = {
        "title": title,
        "library": library_id,
        "folder": folder_id,
    }
    if author:
        data["author"] = author

    filename = os.path.basename(audio_path)
    ext = os.path.splitext(filename)[1].lower()
    content_types = {
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".m4a": "audio/mp4",
        ".m4b": "audio/mp4",
    }
    content_type = content_types.get(ext, "application/octet-stream")

    with open(audio_path, "rb") as f:
        files = {"0": (filename, f, content_type)}
        resp = requests.post(
            f"{abs_url}/api/upload",
            headers=headers,
            data=data,
            files=files,
        )

    if resp.status_code != 200:
        raise RuntimeError(f"Upload failed: {resp.status_code} {resp.text}")


def publish_audio(
    audio_path: str,
    title: str,
    mode: str,
    author: str = None,
    library_name: str = None,
    abs_url: str = None,
    abs_api_key: str = None,
) -> None:
    if mode not in ("podcast", "book"):
        raise ValueError(f"mode must be 'podcast' or 'book', got '{mode}'")

    if not os.path.exists(audio_path):
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    url = abs_url or os.getenv("AUDIOBOOKSHELF_URL")
    if not url:
        raise ValueError("AUDIOBOOKSHELF_URL is required (pass abs_url or set env var)")

    token = abs_api_key or os.getenv("AUDIOBOOKSHELF_API_KEY")
    if not token:
        raise ValueError("AUDIOBOOKSHELF_API_KEY is required (pass abs_api_key or set env var)")

    if mode == "book" and not author:
        raise ValueError("author is required for book mode")

    url = url.rstrip("/")
    headers = {"Authorization": f"Bearer {token}"}
    media_type = "podcast" if mode == "podcast" else "book"

    library_id, folder_id = _find_library(url, headers, media_type, library_name)
    _upload(url, headers, audio_path, library_id, folder_id, title, author)


def main(argv=None):
    """CLI entry point for audio publishing."""
    parser = argparse.ArgumentParser(
        description="Publish audio files to an Audiobookshelf server"
    )
    subparsers = parser.add_subparsers(dest="mode", required=True)

    podcast_parser = subparsers.add_parser("podcast", help="Upload a podcast episode")
    podcast_parser.add_argument("audio_file", help="Path to the audio file")
    podcast_parser.add_argument("--podcast", required=True, help="Podcast name")
    podcast_parser.add_argument("--library", help="Library name (default: first podcast library)")
    podcast_parser.add_argument("--url", help="Server URL (overrides AUDIOBOOKSHELF_URL)")
    podcast_parser.add_argument("--api-key", help="API key (overrides AUDIOBOOKSHELF_API_KEY)")

    book_parser = subparsers.add_parser("book", help="Upload an audiobook file")
    book_parser.add_argument("audio_file", help="Path to the audio file")
    book_parser.add_argument("--title", required=True, help="Book title")
    book_parser.add_argument("--author", required=True, help="Author name")
    book_parser.add_argument("--library", help="Library name (default: first book library)")
    book_parser.add_argument("--url", help="Server URL (overrides AUDIOBOOKSHELF_URL)")
    book_parser.add_argument("--api-key", help="API key (overrides AUDIOBOOKSHELF_API_KEY)")

    args = parser.parse_args(argv)

    if not os.path.exists(args.audio_file):
        print(f"Error: Audio file '{args.audio_file}' not found.", file=sys.stderr)
        sys.exit(1)

    title = args.podcast if args.mode == "podcast" else args.title

    try:
        publish_audio(
            args.audio_file,
            title=title,
            mode=args.mode,
            author=getattr(args, "author", None),
            library_name=args.library,
            abs_url=args.url,
            abs_api_key=args.api_key,
        )
        print(f"Published: {os.path.basename(args.audio_file)}")
    except (ValueError, RuntimeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
