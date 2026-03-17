#!/usr/bin/env python3
"""
Convert WAV audio files to MP3 with ID3 metadata tags.
"""

import argparse
import os
import sys
from pydub import AudioSegment
from mutagen.id3 import ID3, TIT2, TPE1, TALB, ID3NoHeaderError


def convert_to_mp3(
    wav_path: str,
    mp3_path: str,
    title: str = None,
    artist: str = None,
    album: str = None,
) -> None:
    """Convert a WAV file to MP3 and optionally add ID3 tags.

    Args:
        wav_path: Path to the input WAV file
        mp3_path: Path for the output MP3 file
        title: Article title (ID3 TIT2)
        artist: Author name (ID3 TPE1)
        album: Publication name (ID3 TALB)

    Raises:
        FileNotFoundError: If the WAV file doesn't exist
    """
    if not os.path.exists(wav_path):
        raise FileNotFoundError(f"WAV file not found: {wav_path}")

    audio = AudioSegment.from_wav(wav_path)
    audio.export(mp3_path, format="mp3")

    if any([title, artist, album]):
        try:
            tags = ID3(mp3_path)
        except ID3NoHeaderError:
            tags = ID3()

        if title:
            tags.add(TIT2(encoding=3, text=title))
        if artist:
            tags.add(TPE1(encoding=3, text=artist))
        if album:
            tags.add(TALB(encoding=3, text=album))

        tags.save(mp3_path)


def main(argv=None):
    """CLI entry point for audio conversion."""
    parser = argparse.ArgumentParser(
        description="Convert WAV to MP3 with ID3 metadata tags"
    )
    parser.add_argument("wav_file", help="Path to the input WAV file")
    parser.add_argument("mp3_file", help="Path for the output MP3 file")
    parser.add_argument("--title", help="Article title")
    parser.add_argument("--artist", help="Author name")
    parser.add_argument("--album", help="Publication (e.g. journal name and issue)")

    args = parser.parse_args(argv)

    if not os.path.exists(args.wav_file):
        print(f"Error: WAV file '{args.wav_file}' not found.", file=sys.stderr)
        sys.exit(1)

    convert_to_mp3(
        args.wav_file, args.mp3_file,
        title=args.title,
        artist=args.artist,
        album=args.album,
    )
    print(f"Converted to: {args.mp3_file}")


if __name__ == "__main__":
    main()
