#!/usr/bin/env python3
"""
Unit tests for verify_audio.py script.
Run with: pytest test_verify_audio.py
"""

import pytest
from unittest.mock import patch, MagicMock

from pydub import AudioSegment
from pydub.generators import Sine

from verify_audio import verify_audio, main, precheck_report, format_timestamp


def make_segment(spec):
    """Build an AudioSegment from a spec like [("tone", 1000), ("silence", 2000)].

    Durations are in milliseconds. Tones are full-scale 440 Hz sine waves,
    well above any silence-detection threshold.
    """
    segment = AudioSegment.empty()
    for kind, duration_ms in spec:
        if kind == "tone":
            piece = Sine(440).to_audio_segment(duration=duration_ms)
            piece = piece.set_frame_rate(24000).set_channels(1)
        elif kind == "silence":
            piece = AudioSegment.silent(duration=duration_ms, frame_rate=24000)
        else:
            raise ValueError(f"Unknown spec kind: {kind}")
        segment += piece
    return segment


def make_wav(path, spec=(("tone", 500),)):
    """Write a small real WAV file built from a spec. Returns the path as str."""
    make_segment(spec).export(str(path), format="wav")
    return str(path)


def mock_gemini_client(mock_genai, response_text="No issues found."):
    """Wire up a mocked genai module and return the mocked client."""
    mock_client = MagicMock()
    mock_genai.Client.return_value = mock_client
    mock_response = MagicMock()
    mock_response.text = response_text
    mock_client.models.generate_content.return_value = mock_response
    return mock_client


class TestVerifyAudio:
    """Test the verify_audio function."""

    @patch("verify_audio.genai")
    def test_returns_model_response_text(self, mock_genai, tmp_path):
        """Test that verify_audio returns the model's response as a string."""
        mock_gemini_client(mock_genai, "No issues found.")
        audio_path = make_wav(tmp_path / "audio.wav")

        result = verify_audio(audio_path, "Some source text.")
        assert "No issues found." in result

    @patch("verify_audio.genai")
    def test_sends_source_text_in_prompt(self, mock_genai, tmp_path):
        """Test that the source text is included in the prompt sent to Gemini."""
        mock_client = mock_gemini_client(mock_genai, "All good.")
        audio_path = make_wav(tmp_path / "audio.wav")

        verify_audio(audio_path, "The quick brown fox.")
        call_args = mock_client.models.generate_content.call_args
        # The source text should appear somewhere in the contents sent
        contents = str(call_args)
        assert "The quick brown fox." in contents

    @patch("verify_audio.genai")
    def test_uploads_audio_file(self, mock_genai, tmp_path):
        """Test that the audio file is uploaded via the client."""
        mock_client = mock_gemini_client(mock_genai, "All good.")
        audio_path = make_wav(tmp_path / "audio.wav")

        verify_audio(audio_path, "Some text.")
        mock_client.files.upload.assert_called_once()
        upload_args = mock_client.files.upload.call_args
        assert audio_path in str(upload_args)

    @patch("verify_audio.genai")
    def test_uses_specified_model(self, mock_genai, tmp_path):
        """Test that the specified model is passed to generate_content."""
        mock_client = mock_gemini_client(mock_genai, "All good.")
        audio_path = make_wav(tmp_path / "audio.wav")

        verify_audio(audio_path, "Some text.", model="gemini-2.5-pro")
        call_args = mock_client.models.generate_content.call_args
        assert call_args.kwargs.get("model") == "gemini-2.5-pro" or "gemini-2.5-pro" in str(call_args)

    @patch("verify_audio.genai")
    def test_default_model_is_maintained_alias(self, mock_genai, tmp_path):
        """Test that the default model is the maintained gemini-flash-latest alias."""
        mock_client = mock_gemini_client(mock_genai)
        audio_path = make_wav(tmp_path / "audio.wav")

        verify_audio(audio_path, "Some text.")
        call_args = mock_client.models.generate_content.call_args
        assert call_args.kwargs.get("model") == "gemini-flash-latest"

    def test_raises_on_missing_audio_file(self):
        """Test that a missing audio file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            verify_audio("/nonexistent/audio.wav", "Some text.")


class TestFormatTimestamp:
    """Test millisecond-to-MM:SS formatting."""

    def test_formats_minutes_and_seconds(self):
        assert format_timestamp(754000) == "12:34"

    def test_pads_seconds(self):
        assert format_timestamp(4000) == "0:04"

    def test_formats_over_an_hour(self):
        assert format_timestamp(3723000) == "62:03"


class TestPrecheckReport:
    """Test the deterministic pre-check that runs before any model call."""

    def test_reports_duration_word_count_and_pace(self):
        """Test the report includes duration, word count, and words per minute."""
        audio = make_segment([("tone", 4000)])
        text = "one two three four five six seven eight nine ten"

        report = precheck_report(audio, text)

        assert "0:04" in report
        assert "10 words" in report
        assert "150" in report  # 10 words / 4s = 150 WPM

    def test_break_markers_not_counted_as_words(self):
        """Test [BREAK] markers are excluded from the word count."""
        audio = make_segment([("tone", 4000)])
        text = "one two three four five\n[BREAK]\nsix seven eight nine ten"

        report = precheck_report(audio, text)

        assert "10 words" in report

    def test_warns_when_pace_is_implausibly_slow(self):
        """Test a WPM far below speech pace (suggesting missing text) warns."""
        audio = make_segment([("tone", 60000)])
        text = " ".join(["word"] * 10)  # 10 WPM

        report = precheck_report(audio, text)

        assert "WARNING" in report

    def test_no_warning_at_normal_speech_pace(self):
        """Test a normal reading pace produces no warning."""
        audio = make_segment([("tone", 60000)])
        text = " ".join(["word"] * 150)  # 150 WPM

        report = precheck_report(audio, text)

        assert "WARNING" not in report

    def test_warns_on_unexpectedly_long_silence(self):
        """Test a silence gap far longer than a [BREAK] pause warns with a timestamp."""
        audio = make_segment([("tone", 30000), ("silence", 6000), ("tone", 30000)])
        text = " ".join(["word"] * 150)  # pace in band; only silence should warn

        report = precheck_report(audio, text)

        assert "WARNING" in report
        assert "0:30" in report  # the silence starts 30s in

    def test_no_warning_for_normal_break_silence(self):
        """Test an ordinary ~2s [BREAK] gap does not trigger the silence warning."""
        audio = make_segment([("tone", 30000), ("silence", 2000), ("tone", 30000)])
        text = " ".join(["word"] * 150)

        report = precheck_report(audio, text)

        assert "WARNING" not in report


class TestCLI:
    """Test the command-line interface."""

    @patch("verify_audio.verify_audio")
    def test_cli_with_text_file(self, mock_verify, tmp_path, capsys):
        """Test CLI reads source text from a file."""
        mock_verify.return_value = "No issues found."
        audio_path = make_wav(tmp_path / "audio.wav")
        text_path = tmp_path / "source.txt"
        text_path.write_text("Hello world.")

        main([audio_path, str(text_path)])
        mock_verify.assert_called_once()
        args = mock_verify.call_args
        assert args[0][1] == "Hello world."
        captured = capsys.readouterr()
        assert "No issues found." in captured.out

    @patch("verify_audio.verify_audio")
    def test_cli_with_model_flag(self, mock_verify, tmp_path):
        """Test CLI passes --model flag to verify_audio."""
        mock_verify.return_value = "All good."
        audio_path = make_wav(tmp_path / "audio.wav")
        text_path = tmp_path / "source.txt"
        text_path.write_text("Some text.")

        main([audio_path, str(text_path), "--model", "gemini-2.5-pro"])
        call_args = mock_verify.call_args
        assert call_args.kwargs.get("model") == "gemini-2.5-pro" or call_args[0][2] == "gemini-2.5-pro"

    @patch("verify_audio.verify_audio")
    def test_cli_default_model_is_maintained_alias(self, mock_verify, tmp_path):
        """Test CLI defaults to the maintained gemini-flash-latest alias."""
        mock_verify.return_value = "All good."
        audio_path = make_wav(tmp_path / "audio.wav")
        text_path = tmp_path / "source.txt"
        text_path.write_text("Some text.")

        main([audio_path, str(text_path)])
        call_args = mock_verify.call_args
        assert call_args.kwargs.get("model") == "gemini-flash-latest"

    @patch("verify_audio.verify_audio")
    def test_cli_unavailable_model_exits_with_clear_error(self, mock_verify, tmp_path, capsys):
        """Test CLI reports a clear error (not a traceback) when the model 404s."""
        mock_verify.side_effect = Exception(
            "404 NOT_FOUND: models/gemini-nonexistent is not found"
        )
        audio_path = make_wav(tmp_path / "audio.wav")
        text_path = tmp_path / "source.txt"
        text_path.write_text("Some text.")

        with pytest.raises(SystemExit) as exc_info:
            main([audio_path, str(text_path), "--model", "gemini-nonexistent"])

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "gemini-nonexistent" in captured.err
        assert "not available" in captured.err

    @patch("verify_audio.verify_audio")
    def test_cli_reraises_unrelated_errors(self, mock_verify, tmp_path):
        """Test CLI doesn't swallow errors that aren't about a missing model."""
        mock_verify.side_effect = Exception("connection reset by peer")
        audio_path = make_wav(tmp_path / "audio.wav")
        text_path = tmp_path / "source.txt"
        text_path.write_text("Some text.")

        with pytest.raises(Exception, match="connection reset"):
            main([audio_path, str(text_path)])

    def test_cli_missing_audio_file_exits(self, tmp_path):
        """Test CLI exits with error for missing audio file."""
        text_path = tmp_path / "source.txt"
        text_path.write_text("Some text.")

        with pytest.raises(SystemExit):
            main(["/nonexistent/audio.wav", str(text_path)])

    def test_cli_missing_text_file_exits(self, tmp_path):
        """Test CLI exits with error for missing text file."""
        audio_path = make_wav(tmp_path / "audio.wav")

        with pytest.raises(SystemExit):
            main([audio_path, "/nonexistent/text.txt"])
