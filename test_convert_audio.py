#!/usr/bin/env python3
"""
Tests for WAV to MP3 conversion with ID3 tagging.
Run with: pytest test_convert_audio.py
"""

import pytest
import tempfile
import os
import numpy as np
import soundfile as sf
from mutagen.mp3 import MP3
from mutagen.id3 import ID3

from convert_audio import convert_to_mp3, main


@pytest.fixture
def sample_wav(tmp_path):
    """Create a short WAV file for testing."""
    wav_path = str(tmp_path / "test.wav")
    # 1 second of silence at 24kHz
    audio = np.zeros(24000, dtype=np.float32)
    sf.write(wav_path, audio, 24000)
    return wav_path


class TestConvertToMp3:
    """Test the convert_to_mp3 function."""

    def test_creates_mp3_file(self, sample_wav, tmp_path):
        """Test that an MP3 file is created."""
        mp3_path = str(tmp_path / "output.mp3")
        convert_to_mp3(sample_wav, mp3_path)
        assert os.path.exists(mp3_path)

    def test_mp3_is_valid(self, sample_wav, tmp_path):
        """Test that the output is a valid MP3 file."""
        mp3_path = str(tmp_path / "output.mp3")
        convert_to_mp3(sample_wav, mp3_path)
        mp3 = MP3(mp3_path)
        assert mp3.info.length > 0

    def test_sets_title_tag(self, sample_wav, tmp_path):
        """Test that the title ID3 tag is set."""
        mp3_path = str(tmp_path / "output.mp3")
        convert_to_mp3(sample_wav, mp3_path, title="My Article")
        tags = ID3(mp3_path)
        assert str(tags["TIT2"]) == "My Article"

    def test_sets_artist_tag(self, sample_wav, tmp_path):
        """Test that the artist ID3 tag is set from author."""
        mp3_path = str(tmp_path / "output.mp3")
        convert_to_mp3(sample_wav, mp3_path, artist="Jane Doe")
        tags = ID3(mp3_path)
        assert str(tags["TPE1"]) == "Jane Doe"

    def test_sets_album_tag(self, sample_wav, tmp_path):
        """Test that the album ID3 tag is set from publication."""
        mp3_path = str(tmp_path / "output.mp3")
        convert_to_mp3(sample_wav, mp3_path, album="The New Atlantis")
        tags = ID3(mp3_path)
        assert str(tags["TALB"]) == "The New Atlantis"

    def test_sets_multiple_tags(self, sample_wav, tmp_path):
        """Test setting title, artist, and album together."""
        mp3_path = str(tmp_path / "output.mp3")
        convert_to_mp3(
            sample_wav, mp3_path,
            title="The Cassandra of the Machine",
            artist="Charles Carman",
            album="The New Atlantis, No. 83 (Winter 2026)",
        )
        tags = ID3(mp3_path)
        assert str(tags["TIT2"]) == "The Cassandra of the Machine"
        assert str(tags["TPE1"]) == "Charles Carman"
        assert str(tags["TALB"]) == "The New Atlantis, No. 83 (Winter 2026)"

    def test_no_article_tags_if_none_provided(self, sample_wav, tmp_path):
        """Test that no article ID3 tags are written when none are provided."""
        mp3_path = str(tmp_path / "output.mp3")
        convert_to_mp3(sample_wav, mp3_path)
        tags = MP3(mp3_path).tags or {}
        assert "TIT2" not in tags
        assert "TPE1" not in tags
        assert "TALB" not in tags

    def test_raises_on_missing_input(self):
        """Test that a missing WAV file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            convert_to_mp3("/nonexistent/audio.wav", "/tmp/out.mp3")


class TestCLI:
    """Test the command-line interface."""

    def test_cli_converts_file(self, sample_wav, tmp_path):
        """Test basic CLI conversion."""
        mp3_path = str(tmp_path / "output.mp3")
        main([sample_wav, mp3_path])
        assert os.path.exists(mp3_path)

    def test_cli_with_metadata_flags(self, sample_wav, tmp_path):
        """Test CLI with --title, --artist, --album flags."""
        mp3_path = str(tmp_path / "output.mp3")
        main([
            sample_wav, mp3_path,
            "--title", "Test Title",
            "--artist", "Test Author",
            "--album", "Test Publication",
        ])
        tags = ID3(mp3_path)
        assert str(tags["TIT2"]) == "Test Title"
        assert str(tags["TPE1"]) == "Test Author"
        assert str(tags["TALB"]) == "Test Publication"

    def test_cli_missing_input_exits(self):
        """Test CLI exits with error for missing input file."""
        with pytest.raises(SystemExit):
            main(["/nonexistent/audio.wav", "/tmp/out.mp3"])
