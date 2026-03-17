#!/usr/bin/env python3
"""
Unit tests for verify_audio.py script.
Run with: pytest test_verify_audio.py
"""

import pytest
from unittest.mock import patch, MagicMock
import tempfile
import os

from verify_audio import verify_audio, main


class TestVerifyAudio:
    """Test the verify_audio function."""

    @patch("verify_audio.genai")
    def test_returns_model_response_text(self, mock_genai):
        """Test that verify_audio returns the model's response as a string."""
        mock_client = MagicMock()
        mock_genai.Client.return_value = mock_client
        mock_response = MagicMock()
        mock_response.text = "No issues found."
        mock_client.models.generate_content.return_value = mock_response

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(b"fake audio data")
            audio_path = f.name

        try:
            result = verify_audio(audio_path, "Some source text.")
            assert result == "No issues found."
        finally:
            os.unlink(audio_path)

    @patch("verify_audio.genai")
    def test_sends_source_text_in_prompt(self, mock_genai):
        """Test that the source text is included in the prompt sent to Gemini."""
        mock_client = MagicMock()
        mock_genai.Client.return_value = mock_client
        mock_response = MagicMock()
        mock_response.text = "All good."
        mock_client.models.generate_content.return_value = mock_response

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(b"fake audio data")
            audio_path = f.name

        try:
            verify_audio(audio_path, "The quick brown fox.")
            call_args = mock_client.models.generate_content.call_args
            # The source text should appear somewhere in the contents sent
            contents = str(call_args)
            assert "The quick brown fox." in contents
        finally:
            os.unlink(audio_path)

    @patch("verify_audio.genai")
    def test_uploads_audio_file(self, mock_genai):
        """Test that the audio file is uploaded via the client."""
        mock_client = MagicMock()
        mock_genai.Client.return_value = mock_client
        mock_response = MagicMock()
        mock_response.text = "All good."
        mock_client.models.generate_content.return_value = mock_response

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(b"fake audio data")
            audio_path = f.name

        try:
            verify_audio(audio_path, "Some text.")
            mock_client.files.upload.assert_called_once()
            upload_args = mock_client.files.upload.call_args
            assert audio_path in str(upload_args)
        finally:
            os.unlink(audio_path)

    @patch("verify_audio.genai")
    def test_uses_specified_model(self, mock_genai):
        """Test that the specified model is passed to generate_content."""
        mock_client = MagicMock()
        mock_genai.Client.return_value = mock_client
        mock_response = MagicMock()
        mock_response.text = "All good."
        mock_client.models.generate_content.return_value = mock_response

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(b"fake audio data")
            audio_path = f.name

        try:
            verify_audio(audio_path, "Some text.", model="gemini-2.5-flash")
            call_args = mock_client.models.generate_content.call_args
            assert call_args.kwargs.get("model") == "gemini-2.5-flash" or "gemini-2.5-flash" in str(call_args)
        finally:
            os.unlink(audio_path)

    def test_raises_on_missing_audio_file(self):
        """Test that a missing audio file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            verify_audio("/nonexistent/audio.wav", "Some text.")


class TestCLI:
    """Test the command-line interface."""

    @patch("verify_audio.verify_audio")
    def test_cli_with_text_file(self, mock_verify, capsys):
        """Test CLI reads source text from a file."""
        mock_verify.return_value = "No issues found."

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as af:
            af.write(b"fake audio")
            audio_path = af.name

        with tempfile.NamedTemporaryFile(suffix=".txt", mode="w", delete=False) as tf:
            tf.write("Hello world.")
            text_path = tf.name

        try:
            main([audio_path, text_path])
            mock_verify.assert_called_once()
            args = mock_verify.call_args
            assert args[0][1] == "Hello world."
            captured = capsys.readouterr()
            assert "No issues found." in captured.out
        finally:
            os.unlink(audio_path)
            os.unlink(text_path)

    @patch("verify_audio.verify_audio")
    def test_cli_with_model_flag(self, mock_verify, capsys):
        """Test CLI passes --model flag to verify_audio."""
        mock_verify.return_value = "All good."

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as af:
            af.write(b"fake audio")
            audio_path = af.name

        with tempfile.NamedTemporaryFile(suffix=".txt", mode="w", delete=False) as tf:
            tf.write("Some text.")
            text_path = tf.name

        try:
            main([audio_path, text_path, "--model", "gemini-2.5-pro"])
            call_args = mock_verify.call_args
            assert call_args.kwargs.get("model") == "gemini-2.5-pro" or call_args[0][2] == "gemini-2.5-pro"
        finally:
            os.unlink(audio_path)
            os.unlink(text_path)

    def test_cli_missing_audio_file_exits(self, capsys):
        """Test CLI exits with error for missing audio file."""
        with tempfile.NamedTemporaryFile(suffix=".txt", mode="w", delete=False) as tf:
            tf.write("Some text.")
            text_path = tf.name

        try:
            with pytest.raises(SystemExit):
                main(["/nonexistent/audio.wav", text_path])
        finally:
            os.unlink(text_path)

    def test_cli_missing_text_file_exits(self):
        """Test CLI exits with error for missing text file."""
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as af:
            af.write(b"fake audio")
            audio_path = af.name

        try:
            with pytest.raises(SystemExit):
                main([audio_path, "/nonexistent/text.txt"])
        finally:
            os.unlink(audio_path)
