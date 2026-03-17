#!/usr/bin/env python3
"""
Tests that verify all CLI commands and options are documented in the README.
Uses pytest-subtests following the pattern from til.simonwillison.net/pytest/subtests
"""

import re
import subprocess
from pathlib import Path

README = Path(__file__).parent / "README.md"


def _extract_cli_options(script_name):
    """Run --help on a script and extract all option flags."""
    result = subprocess.run(
        ["uv", "run", "python", script_name, "--help"],
        capture_output=True, text=True
    )
    # Match long-form options like --output, --voice, --model
    return re.findall(r"(--[a-z][-a-z]*)", result.stdout)


def _unique(items):
    """Deduplicate while preserving order."""
    seen = set()
    return [x for x in items if not (x in seen or seen.add(x))]


def test_text_to_speech_options_documented(subtests):
    """Every text_to_speech.py CLI option should appear in the README."""
    readme_text = README.read_text()
    options = _unique(_extract_cli_options("text_to_speech.py"))
    for option in options:
        if option == "--help":
            continue
        with subtests.test(option=option):
            assert option in readme_text, f"{option} not documented in README"


def test_verify_audio_options_documented(subtests):
    """Every verify_audio.py CLI option should appear in the README."""
    readme_text = README.read_text()
    options = _unique(_extract_cli_options("verify_audio.py"))
    for option in options:
        if option == "--help":
            continue
        with subtests.test(option=option):
            assert option in readme_text, f"{option} not documented in README"


def test_verify_audio_command_documented():
    """The verify_audio.py command itself should appear in the README."""
    readme_text = README.read_text()
    assert "verify_audio.py" in readme_text, "verify_audio.py not mentioned in README"
