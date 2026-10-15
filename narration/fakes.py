"""Fake engines for tests and for `python -m narration --fake-engines`."""
from __future__ import annotations

from pathlib import Path


class FakeTTS:
    def __init__(self):
        self.calls = 0
        self.fail_next = False
        self.hold = None  # (started, release) Events: when set, a run blocks between them

    def record(self, text, out_path, voice, speed):
        self.calls += 1
        if self.fail_next:
            self.fail_next = False
            Path(out_path).write_bytes(b"RIFF partial")  # a crash mid-write
            raise RuntimeError("fake engine failure")
        if self.hold:
            started, release = self.hold
            started.set()
            release.wait()
        Path(out_path).write_bytes(b"RIFF fake wav")
        return 60.0


class FakeVerifier:
    def __init__(self):
        self.report = "No issues found."
        self.warnings: list[str] = []

    def check(self, audio_path, text):
        return self.report, list(self.warnings)


class FakeG2P:
    def phonemes(self, word):
        return "fəˈnɛt"


class FakeConverter:
    def __init__(self):
        self.calls = []

    def to_mp3(self, wav, mp3, title, artist, album):
        self.calls.append((title, artist, album))
        Path(mp3).write_bytes(b"ID3 fake mp3")
