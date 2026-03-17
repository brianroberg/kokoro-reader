#!/usr/bin/env python3
"""
Tests for publishing audio files to Audiobookshelf.
Run with: pytest tests/test_publish_audio.py
"""

import pytest
from unittest.mock import patch, MagicMock

from publish_audio import publish_audio, _find_library, _upload, main


class TestPublishAudio:
    """Test the publish_audio function."""

    def test_raises_on_missing_audio_file(self):
        """Test that a missing audio file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            publish_audio("/nonexistent/audio.mp3", title="Test", mode="podcast")

    def test_raises_on_invalid_mode(self, tmp_path):
        """Test that an invalid mode raises ValueError."""
        audio = tmp_path / "test.mp3"
        audio.write_bytes(b"fake audio")
        with pytest.raises(ValueError, match="mode must be"):
            publish_audio(str(audio), title="Test", mode="audiobook")

    def test_raises_when_url_missing(self, tmp_path):
        """Test that missing server URL raises ValueError."""
        audio = tmp_path / "test.mp3"
        audio.write_bytes(b"fake audio")
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(ValueError, match="AUDIOBOOKSHELF_URL"):
                publish_audio(
                    str(audio), title="Test", mode="podcast",
                    abs_url=None, abs_api_key=None,
                )

    def test_raises_when_token_missing(self, tmp_path):
        """Test that missing API token raises ValueError."""
        audio = tmp_path / "test.mp3"
        audio.write_bytes(b"fake audio")
        with patch.dict("os.environ", {"AUDIOBOOKSHELF_URL": "http://example.com"}, clear=True):
            with pytest.raises(ValueError, match="AUDIOBOOKSHELF_API_KEY"):
                publish_audio(
                    str(audio), title="Test", mode="podcast",
                    abs_url=None, abs_api_key=None,
                )

    @patch("publish_audio.requests.post")
    @patch("publish_audio.requests.get")
    def test_podcast_mode_finds_library_and_uploads(self, mock_get, mock_post, tmp_path):
        """Test that podcast mode resolves the podcast library and uploads."""
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"libraries": SAMPLE_LIBRARIES},
        )
        mock_post.return_value = MagicMock(status_code=200)

        audio = tmp_path / "Article Title.mp3"
        audio.write_bytes(b"fake audio")

        publish_audio(
            str(audio), title="The New Atlantis", mode="podcast",
            abs_url="http://example.com", abs_api_key="tok123",
        )

        # Should have called GET /api/libraries
        mock_get.assert_called_once()
        assert "/api/libraries" in mock_get.call_args[0][0]

        # Should have uploaded to the podcast library
        mock_post.assert_called_once()
        upload_data = mock_post.call_args.kwargs["data"]
        assert upload_data["library"] == "lib_podcast"
        assert upload_data["title"] == "The New Atlantis"

    @patch("publish_audio.requests.post")
    @patch("publish_audio.requests.get")
    def test_book_mode_includes_author(self, mock_get, mock_post, tmp_path):
        """Test that book mode passes author to the upload."""
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"libraries": SAMPLE_LIBRARIES},
        )
        mock_post.return_value = MagicMock(status_code=200)

        audio = tmp_path / "Chapter 1.mp3"
        audio.write_bytes(b"fake audio")

        publish_audio(
            str(audio), title="My Book", mode="book",
            author="Jane Doe",
            abs_url="http://example.com", abs_api_key="tok123",
        )

        upload_data = mock_post.call_args.kwargs["data"]
        assert upload_data["library"] == "lib_book"
        assert upload_data["title"] == "My Book"
        assert upload_data["author"] == "Jane Doe"

    @patch("publish_audio.requests.post")
    @patch("publish_audio.requests.get")
    def test_book_mode_requires_author(self, mock_get, mock_post, tmp_path):
        """Test that book mode raises ValueError when author is missing."""
        audio = tmp_path / "Chapter 1.mp3"
        audio.write_bytes(b"fake audio")

        with pytest.raises(ValueError, match="author"):
            publish_audio(
                str(audio), title="My Book", mode="book",
                abs_url="http://example.com", abs_api_key="tok123",
            )


SAMPLE_LIBRARIES = [
    {
        "id": "lib_podcast",
        "name": "Podcasts",
        "mediaType": "podcast",
        "folders": [{"id": "fol_pod1", "path": "/podcasts"}],
    },
    {
        "id": "lib_book",
        "name": "Audiobooks",
        "mediaType": "book",
        "folders": [{"id": "fol_book1", "path": "/audiobooks"}],
    },
]


class TestFindLibrary:
    """Test the _find_library helper."""

    def test_finds_podcast_library_by_media_type(self):
        """Test finding the first podcast library when no name given."""
        lib_id, folder_id = _find_library(
            "http://example.com", {"Authorization": "Bearer tok"},
            media_type="podcast", libraries=SAMPLE_LIBRARIES,
        )
        assert lib_id == "lib_podcast"
        assert folder_id == "fol_pod1"

    def test_finds_book_library_by_media_type(self):
        """Test finding the first book library when no name given."""
        lib_id, folder_id = _find_library(
            "http://example.com", {"Authorization": "Bearer tok"},
            media_type="book", libraries=SAMPLE_LIBRARIES,
        )
        assert lib_id == "lib_book"
        assert folder_id == "fol_book1"

    def test_finds_library_by_name_case_insensitive(self):
        """Test finding a library by name, ignoring case."""
        lib_id, folder_id = _find_library(
            "http://example.com", {"Authorization": "Bearer tok"},
            media_type="podcast", library_name="podcasts",
            libraries=SAMPLE_LIBRARIES,
        )
        assert lib_id == "lib_podcast"

    def test_raises_when_named_library_not_found(self):
        """Test ValueError when named library doesn't exist."""
        with pytest.raises(ValueError, match="No podcast library found named 'Nonexistent'"):
            _find_library(
                "http://example.com", {"Authorization": "Bearer tok"},
                media_type="podcast", library_name="Nonexistent",
                libraries=SAMPLE_LIBRARIES,
            )

    def test_raises_when_no_library_of_type(self):
        """Test ValueError when no library of the requested type exists."""
        books_only = [lib for lib in SAMPLE_LIBRARIES if lib["mediaType"] == "book"]
        with pytest.raises(ValueError, match="No podcast library found"):
            _find_library(
                "http://example.com", {"Authorization": "Bearer tok"},
                media_type="podcast", libraries=books_only,
            )


class TestUpload:
    """Test the _upload helper."""

    @patch("publish_audio.requests.post")
    def test_sends_correct_multipart_data(self, mock_post, tmp_path):
        """Test that upload sends libraryId, folderId, title, and file."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        audio = tmp_path / "episode.mp3"
        audio.write_bytes(b"fake audio")

        _upload(
            "http://example.com", {"Authorization": "Bearer tok"},
            str(audio), "lib1", "fol1", "The New Atlantis",
        )

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert call_kwargs.kwargs["data"]["title"] == "The New Atlantis"
        assert call_kwargs.kwargs["data"]["library"] == "lib1"
        assert call_kwargs.kwargs["data"]["folder"] == "fol1"

    @patch("publish_audio.requests.post")
    def test_sends_author_when_provided(self, mock_post, tmp_path):
        """Test that author is included in upload data when given."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        audio = tmp_path / "chapter.mp3"
        audio.write_bytes(b"fake audio")

        _upload(
            "http://example.com", {"Authorization": "Bearer tok"},
            str(audio), "lib1", "fol1", "My Book", author="Jane Doe",
        )

        call_kwargs = mock_post.call_args
        assert call_kwargs.kwargs["data"]["author"] == "Jane Doe"

    @patch("publish_audio.requests.post")
    def test_raises_on_upload_failure(self, mock_post, tmp_path):
        """Test RuntimeError on non-200 response."""
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.text = "Forbidden"
        mock_post.return_value = mock_response

        audio = tmp_path / "episode.mp3"
        audio.write_bytes(b"fake audio")

        with pytest.raises(RuntimeError, match="Upload failed: 403"):
            _upload(
                "http://example.com", {"Authorization": "Bearer tok"},
                str(audio), "lib1", "fol1", "Test",
            )


class TestCLI:
    """Test the command-line interface."""

    @patch("publish_audio.publish_audio")
    def test_podcast_subcommand(self, mock_publish, tmp_path):
        """Test CLI podcast subcommand passes correct args."""
        audio = tmp_path / "episode.mp3"
        audio.write_bytes(b"fake audio")

        main(["podcast", str(audio), "--podcast", "The New Atlantis"])

        mock_publish.assert_called_once()
        kwargs = mock_publish.call_args.kwargs
        assert kwargs["mode"] == "podcast"
        assert kwargs["title"] == "The New Atlantis"

    @patch("publish_audio.publish_audio")
    def test_book_subcommand(self, mock_publish, tmp_path):
        """Test CLI book subcommand passes correct args including author."""
        audio = tmp_path / "chapter.mp3"
        audio.write_bytes(b"fake audio")

        main([
            "book", str(audio),
            "--title", "My Book",
            "--author", "Jane Doe",
        ])

        mock_publish.assert_called_once()
        kwargs = mock_publish.call_args.kwargs
        assert kwargs["mode"] == "book"
        assert kwargs["title"] == "My Book"
        assert kwargs["author"] == "Jane Doe"

    def test_cli_missing_file_exits(self):
        """Test CLI exits with error for missing audio file."""
        with pytest.raises(SystemExit):
            main(["podcast", "/nonexistent/audio.mp3", "--podcast", "Test"])

    def test_cli_book_without_author_exits(self, tmp_path):
        """Test CLI book subcommand requires --author."""
        audio = tmp_path / "chapter.mp3"
        audio.write_bytes(b"fake audio")
        with pytest.raises(SystemExit):
            main(["book", str(audio), "--title", "Test"])

    @patch("publish_audio.publish_audio")
    def test_cli_library_flag(self, mock_publish, tmp_path):
        """Test CLI passes --library flag to publish_audio."""
        audio = tmp_path / "episode.mp3"
        audio.write_bytes(b"fake audio")

        main([
            "podcast", str(audio),
            "--podcast", "The New Atlantis",
            "--library", "My Podcasts",
        ])

        kwargs = mock_publish.call_args.kwargs
        assert kwargs["library_name"] == "My Podcasts"
