"""Engine interfaces and the real adapters over the existing toolkit.

Imports of the heavy toolkit modules happen inside methods, so this module
(and everything that imports it) stays importable without MLX or Gemini.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol


class TTSEngine(Protocol):
    def record(self, text: str, out_path: Path, voice: str, speed: float) -> float: ...


class Verifier(Protocol):
    def check(self, audio_path: Path, text: str) -> tuple[str, list[str]]: ...


class G2P(Protocol):
    def phonemes(self, word: str) -> str: ...


class Converter(Protocol):
    def to_mp3(self, wav: Path, mp3: Path, title: str, artist: str, album: str) -> None: ...


class KokoroEngine:
    def record(self, text, out_path, voice, speed):
        from pydub import AudioSegment
        from narration.words import normalize_spaces
        from text_to_speech import clean_markdown_text, generate_audio
        # The CLI's chain for a .md file: clean_markdown_text, then generate_audio
        # (which only runs prepare_for_tts). Without the cleaning, headings,
        # emphasis markers and link URLs are read aloud, and words.check can't
        # catch it because it strips exactly that markup. Between the two, every
        # space but a newline is made plain, as words.check reads the text: misaki
        # misplaces an override after a no-break, thin or ideographic space or a
        # tab earlier on its line.
        text = normalize_spaces(clean_markdown_text(text))
        generate_audio(text, str(out_path), voice=voice, speed=speed)
        return len(AudioSegment.from_file(out_path)) / 1000


class GeminiVerifier:
    def check(self, audio_path, text):
        from pydub import AudioSegment
        from verify_audio import precheck_warnings, verify_audio
        warnings = precheck_warnings(AudioSegment.from_file(audio_path), text)
        return verify_audio(str(audio_path), text), warnings


class MisakiG2P:
    def __init__(self):
        from misaki import en, espeak
        # The engine's own configuration (mlx_audio's Kokoro pipeline for American
        # English). A bare en.G2P() has no fallback and answers "❓" for every word
        # outside misaki's lexicon, which is most names, the words overrides are for.
        self._g2p = en.G2P(trf=False, british=False,
                           fallback=espeak.EspeakFallback(british=False), unk="")

    def phonemes(self, word):
        out = self._g2p(word)
        return out[0] if isinstance(out, tuple) else str(out)


class Mp3Converter:
    def to_mp3(self, wav, mp3, title, artist, album):
        from convert_audio import convert_to_mp3
        convert_to_mp3(str(wav), str(mp3), title=title, artist=artist, album=album)
