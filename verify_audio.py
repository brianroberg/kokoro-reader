#!/usr/bin/env python3
"""
Audio verification using Gemini's multimodal capabilities.
Compares a TTS audio recording against source text to identify
mispronunciations, missing content, and other issues.
"""

import argparse
import os
import re
import sys
from pathlib import Path
from google import genai
from dotenv import load_dotenv
from pydub import AudioSegment
from pydub.silence import detect_silence

load_dotenv()

DEFAULT_MODEL = "gemini-flash-latest"

VERIFICATION_PROMPT = """Here is the source text of an article, and an audio recording of it being read aloud. Compare them carefully. Report:

- Words that are mispronounced or garbled
- Words or phrases that are missing from the audio
- Words or phrases in the audio that aren't in the source
- Pauses that are unnaturally long or short
- Any section where audio quality degrades

For each issue, give the approximate timestamp and quote the relevant source text.

If the recording is faithful to the source text with no issues, say "No issues found."

SOURCE TEXT:
{source_text}"""


def format_timestamp(ms: int) -> str:
    """Format a millisecond offset as M:SS (e.g. 754000 -> "12:34")."""
    total_seconds = int(ms // 1000)
    return f"{total_seconds // 60}:{total_seconds % 60:02d}"


def precheck_report(audio: AudioSegment, source_text: str) -> str:
    """Deterministic sanity checks that run before any model call.

    Reports audio duration, source word count, and effective words per
    minute. Costs nothing — no API call involved.
    """
    duration_ms = len(audio)
    word_count = len(re.sub(r"\[BREAK\]", " ", source_text).split())
    wpm = word_count / (duration_ms / 60000) if duration_ms else 0

    lines = [
        "## Pre-check (deterministic)",
        f"Duration: {format_timestamp(duration_ms)} | "
        f"{word_count} words | {wpm:.0f} WPM",
    ]
    if not 100 <= wpm <= 250:
        lines.append(
            f"WARNING: pace of {wpm:.0f} WPM is outside the normal speech "
            "range (100-250) — audio may be truncated or have extra content."
        )
    for start_ms, end_ms in detect_silence(
        audio, min_silence_len=5000, silence_thresh=-40, seek_step=100
    ):
        lines.append(
            f"WARNING: {format_timestamp(end_ms - start_ms)} of silence at "
            f"{format_timestamp(start_ms)} — far longer than a [BREAK] pause."
        )
    return "\n".join(lines)


def find_section_gaps(
    audio: AudioSegment,
    min_silence_len: int = 1500,
    silence_thresh: int = -40,
) -> list:
    """Find the silence gaps inserted at [BREAK] section boundaries.

    text_to_speech.py inserts 2000ms of silence at each [BREAK] and only
    300ms between ordinary chunks, so a 1500ms minimum cleanly separates
    the two. Returns a list of (start_ms, end_ms) tuples.
    """
    return [
        (start_ms, end_ms)
        for start_ms, end_ms in detect_silence(
            audio,
            min_silence_len=min_silence_len,
            silence_thresh=silence_thresh,
            seek_step=50,
        )
    ]


def verify_audio(audio_path: str, source_text: str, model: str = DEFAULT_MODEL) -> str:
    """Verify a TTS audio recording against its source text using Gemini.

    Args:
        audio_path: Path to the audio file (.wav)
        source_text: The original text that was read aloud
        model: Gemini model to use for verification

    Returns:
        The model's assessment as a string

    Raises:
        FileNotFoundError: If the audio file doesn't exist
    """
    if not os.path.exists(audio_path):
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    audio = AudioSegment.from_wav(audio_path)
    precheck = precheck_report(audio, source_text)

    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

    audio_file = client.files.upload(file=audio_path)

    prompt = VERIFICATION_PROMPT.format(source_text=source_text)

    response = client.models.generate_content(
        model=model,
        contents=[prompt, audio_file],
    )

    return f"{precheck}\n\n{response.text}"


def main(argv=None):
    """CLI entry point for audio verification."""
    parser = argparse.ArgumentParser(
        description="Verify a TTS audio recording against source text using Gemini"
    )
    parser.add_argument("audio_file", help="Path to the audio file (.wav)")
    parser.add_argument("text_file", help="Path to the source text file")
    parser.add_argument(
        "--model", default=DEFAULT_MODEL,
        help=f"Gemini model to use (default: {DEFAULT_MODEL})"
    )

    args = parser.parse_args(argv)

    if not os.path.exists(args.audio_file):
        print(f"Error: Audio file '{args.audio_file}' not found.", file=sys.stderr)
        sys.exit(1)

    if not os.path.exists(args.text_file):
        print(f"Error: Text file '{args.text_file}' not found.", file=sys.stderr)
        sys.exit(1)

    source_text = Path(args.text_file).read_text()
    try:
        result = verify_audio(args.audio_file, source_text, model=args.model)
    except Exception as e:
        if "404" in str(e) or "NOT_FOUND" in str(e):
            print(
                f"Error: model '{args.model}' is not available — "
                f"try --model {DEFAULT_MODEL}. ({e})",
                file=sys.stderr,
            )
            sys.exit(1)
        raise
    print(result)


if __name__ == "__main__":
    main()
