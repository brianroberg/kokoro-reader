#!/usr/bin/env python3
"""
Audio verification using Gemini's multimodal capabilities.
Compares a TTS audio recording against source text to identify
mispronunciations, missing content, and other issues.
"""

import argparse
import os
import sys
from pathlib import Path
from google import genai
from dotenv import load_dotenv

load_dotenv()

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


def verify_audio(audio_path: str, source_text: str, model: str = "gemini-2.5-flash") -> str:
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

    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

    audio_file = client.files.upload(file=audio_path)

    prompt = VERIFICATION_PROMPT.format(source_text=source_text)

    response = client.models.generate_content(
        model=model,
        contents=[prompt, audio_file],
    )

    return response.text


def main(argv=None):
    """CLI entry point for audio verification."""
    parser = argparse.ArgumentParser(
        description="Verify a TTS audio recording against source text using Gemini"
    )
    parser.add_argument("audio_file", help="Path to the audio file (.wav)")
    parser.add_argument("text_file", help="Path to the source text file")
    parser.add_argument(
        "--model", default="gemini-2.5-flash",
        help="Gemini model to use (default: gemini-2.5-flash)"
    )

    args = parser.parse_args(argv)

    if not os.path.exists(args.audio_file):
        print(f"Error: Audio file '{args.audio_file}' not found.", file=sys.stderr)
        sys.exit(1)

    if not os.path.exists(args.text_file):
        print(f"Error: Text file '{args.text_file}' not found.", file=sys.stderr)
        sys.exit(1)

    source_text = Path(args.text_file).read_text()
    result = verify_audio(args.audio_file, source_text, model=args.model)
    print(result)


if __name__ == "__main__":
    main()
