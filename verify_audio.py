#!/usr/bin/env python3
"""
Audio verification using Gemini's multimodal capabilities.
Compares a TTS audio recording against source text to identify
mispronunciations, missing content, and other issues.
"""

import os
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
