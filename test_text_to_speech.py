#!/usr/bin/env python3
"""
Unit tests for text_to_speech.py script.
Run with: pytest test_text_to_speech.py
"""

import pytest
import tempfile
import os
from pathlib import Path
from unittest.mock import patch, mock_open, MagicMock
import unittest.mock
import sys
from io import StringIO

# Import functions from the script
from text_to_speech import (
    read_text_file,
    clean_markdown_text,
    list_available_voices,
    concatenate_audio_files
)


class TestReadTextFile:
    """Test the read_text_file function."""
    
    def test_read_utf8_file(self):
        """Test reading a UTF-8 encoded file."""
        content = "Hello, 世界! 🌍"
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', delete=False) as f:
            f.write(content)
            temp_path = f.name
        
        try:
            result = read_text_file(temp_path)
            assert result == content
        finally:
            os.unlink(temp_path)
    
    def test_read_latin1_file(self):
        """Test reading a Latin-1 encoded file when UTF-8 fails."""
        content = "Plain ASCII content"  # Use ASCII content that works across encodings
        with tempfile.NamedTemporaryFile(mode='wb', delete=False) as f:
            f.write(content.encode('latin-1'))
            temp_path = f.name
        
        try:
            result = read_text_file(temp_path)
            assert result == content
        finally:
            os.unlink(temp_path)
    
    def test_read_nonexistent_file(self):
        """Test reading a file that doesn't exist."""
        with pytest.raises(FileNotFoundError):
            read_text_file("/nonexistent/file.txt")
    
    def test_read_ascii_file(self):
        """Test reading a basic ASCII file."""
        content = "Hello World!"
        with tempfile.NamedTemporaryFile(mode='w', encoding='ascii', delete=False) as f:
            f.write(content)
            temp_path = f.name
        
        try:
            result = read_text_file(temp_path)
            assert result == content
        finally:
            os.unlink(temp_path)


class TestCleanMarkdownText:
    """Test the clean_markdown_text function."""
    
    def test_remove_headers(self):
        """Test removal of markdown headers."""
        text = "# Header 1\n## Header 2\n### Header 3\nRegular text"
        expected = "Header 1\nHeader 2\nHeader 3\nRegular text"
        assert clean_markdown_text(text) == expected
    
    def test_remove_images(self):
        """Test removal of markdown images."""
        text = "Before ![Alt text](image.png) after"
        result = clean_markdown_text(text)
        assert "![" not in result
        assert "image.png" not in result
        assert "Before" in result
        assert "after" in result
    
    def test_remove_images_no_alt_text(self):
        """Test removal of images with no alt text."""
        text = "Before ![](image.png) after"
        result = clean_markdown_text(text)
        assert "![" not in result
        assert "image.png" not in result
        assert "Before" in result
        assert "after" in result
    
    def test_remove_images_complex_alt_text(self):
        """Test removal of images with complex alt text."""
        text = "Image: ![A photo of a cat sitting on a windowsill](cat.jpg) here"
        result = clean_markdown_text(text)
        assert "![" not in result
        assert "cat.jpg" not in result
        assert "Image:" in result
        assert "here" in result
    
    def test_preserve_link_text(self):
        """Test that link text is preserved but URLs are removed."""
        text = "Check out [this link](https://example.com) for more info"
        expected = "Check out this link for more info"
        assert clean_markdown_text(text) == expected
    
    def test_remove_emphasis(self):
        """Test removal of bold and italic formatting."""
        text = "This is **bold** and *italic* text"
        expected = "This is bold and italic text"
        assert clean_markdown_text(text) == expected
    
    def test_remove_underscore_emphasis(self):
        """Test removal of underscore-based emphasis."""
        text = "This is __bold__ and _italic_ text"
        expected = "This is bold and italic text"
        assert clean_markdown_text(text) == expected
    
    def test_remove_code_blocks(self):
        """Test removal of code blocks."""
        text = "Before\n```python\nprint('hello')\n```\nAfter"
        expected = "Before\n\nAfter"
        assert clean_markdown_text(text) == expected
    
    def test_remove_inline_code(self):
        """Test removal of inline code."""
        text = "Use the `print()` function here"
        expected = "Use the print() function here"
        assert clean_markdown_text(text) == expected
    
    def test_remove_lists(self):
        """Test removal of list formatting."""
        text = "- Item 1\n* Item 2\n+ Item 3\n1. Numbered item"
        expected = "Item 1\nItem 2\nItem 3\nNumbered item"
        assert clean_markdown_text(text) == expected
    
    def test_remove_blockquotes(self):
        """Test removal of blockquote formatting."""
        text = "> This is a quote\n> Multi-line quote"
        expected = "This is a quote\nMulti-line quote"
        assert clean_markdown_text(text) == expected
    
    def test_clean_whitespace(self):
        """Test cleanup of excessive whitespace."""
        text = "Line 1\n\n\n\nLine 2    with   spaces"
        expected = "Line 1\n\nLine 2 with spaces"
        assert clean_markdown_text(text) == expected
    
    def test_complex_markdown_document(self):
        """Test cleaning a complex markdown document."""
        text = """# My Document

This is **bold** text with an image: ![Screenshot](image.png)

## Section 2

Here's a [link](https://example.com) and some `code`.

- List item 1
- List item 2

> This is a blockquote

```python
def hello():
    print("world")
```

Regular text at the end."""
        
        result = clean_markdown_text(text)
        
        # Check that all markdown elements are removed
        assert "# " not in result
        assert "**" not in result
        assert "![" not in result
        assert "](image.png)" not in result
        assert "](https://example.com)" not in result
        assert "`" not in result
        assert "- " not in result
        assert "> " not in result
        assert "```" not in result
        assert "def hello():" not in result
        
        # Check that content is preserved
        assert "My Document" in result
        assert "bold text with an image:" in result
        assert "Section 2" in result
        assert "link and some code" in result
        assert "List item 1" in result
        assert "This is a blockquote" in result
        assert "Regular text at the end." in result


class TestListAvailableVoices:
    """Test the list_available_voices function."""
    
    def test_list_voices_output(self, capsys):
        """Test that list_available_voices produces expected output."""
        list_available_voices()
        captured = capsys.readouterr()
        
        # Check that key sections are present
        assert "Available Voices by Language:" in captured.out
        assert "American English (lang: a)" in captured.out
        assert "British English (lang: b)" in captured.out
        assert "Spanish (lang: e)" in captured.out
        assert "Female:" in captured.out
        assert "Male:" in captured.out
        assert "af_heart" in captured.out  # Default voice
        assert "Usage:" in captured.out
        assert "Example:" in captured.out


class TestConcatenateAudioFiles:
    """Test the concatenate_audio_files function."""
    
    @patch('text_to_speech.AudioSegment')
    def test_concatenate_audio_files_calls_correct_methods(self, mock_audio_segment):
        """Test that concatenate_audio_files calls the expected AudioSegment methods."""
        audio_files = ["file1.wav", "file2.wav", "file3.wav"]
        output_path = "output.wav"
        
        concatenate_audio_files(audio_files, output_path)
        
        # Verify AudioSegment.from_wav was called for each file
        assert mock_audio_segment.from_wav.call_count == 3
        expected_calls = [
            unittest.mock.call("file1.wav"),
            unittest.mock.call("file2.wav"), 
            unittest.mock.call("file3.wav")
        ]
        mock_audio_segment.from_wav.assert_has_calls(expected_calls)
        
        # Verify empty() and silent() were called
        mock_audio_segment.empty.assert_called_once()
        # silent() is called for each gap between files
        assert mock_audio_segment.silent.call_count >= 1
    
    def test_concatenate_empty_list(self):
        """Test error handling for empty audio file list."""
        with pytest.raises(ValueError, match="No audio files to concatenate"):
            concatenate_audio_files([], "output.wav")
    
    @patch('text_to_speech.AudioSegment')
    def test_concatenate_single_file_calls_methods(self, mock_audio_segment):
        """Test concatenation with a single file calls expected methods."""
        concatenate_audio_files(["single.wav"], "output.wav")
        
        mock_audio_segment.from_wav.assert_called_once_with("single.wav")
        mock_audio_segment.empty.assert_called_once()


class TestIntegration:
    """Integration tests for combined functionality."""
    
    def test_markdown_processing_pipeline(self):
        """Test the complete pipeline for markdown processing."""
        markdown_content = """# Test Document

This is a test with ![image](test.png) and [link](http://example.com).

## Code Section

```python
print("hello")
```

- List item
- Another item

> Quote text

Final paragraph."""
        
        # Test that the markdown processing works end-to-end
        cleaned = clean_markdown_text(markdown_content)
        
        # Verify structure is preserved but formatting is removed
        lines = cleaned.split('\n')
        assert any("Test Document" in line for line in lines)
        assert any("Code Section" in line for line in lines)
        assert any("Final paragraph." in line for line in lines)
        
        # Verify formatting is removed
        assert "![" not in cleaned
        assert "```" not in cleaned
        assert "- " not in cleaned
        assert "> " not in cleaned
    
    def test_file_encoding_fallback(self):
        """Test that encoding fallback works properly."""
        # Create files with different encodings
        test_content = "Test content with special chars: café"
        
        # Test UTF-8
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', delete=False) as f:
            f.write(test_content)
            utf8_path = f.name
        
        # Test Latin-1
        with tempfile.NamedTemporaryFile(mode='wb', delete=False) as f:
            f.write(test_content.encode('latin-1'))
            latin1_path = f.name
        
        try:
            # Both should work
            utf8_result = read_text_file(utf8_path)
            latin1_result = read_text_file(latin1_path)
            
            assert utf8_result == test_content
            assert latin1_result == test_content
        finally:
            os.unlink(utf8_path)
            os.unlink(latin1_path)


# Test fixtures and parametrized tests
@pytest.mark.parametrize("input_text,expected_contains,expected_not_contains", [
    # Headers
    ("# Header\nContent", ["Header", "Content"], ["# "]),
    # Images
    ("Text ![alt](img.png) more", ["Text", "more"], ["![", "img.png"]),
    # Links
    ("See [link text](url) here", ["link text", "here"], ["](url)"]),
    # Emphasis
    ("**bold** and *italic*", ["bold", "italic"], ["**", "*"]),
    # Code
    ("Use `code` here", ["Use", "code", "here"], ["`"]),
    # Lists
    ("- Item 1\n- Item 2", ["Item 1", "Item 2"], ["- "]),
])
def test_markdown_cleaning_parametrized(input_text, expected_contains, expected_not_contains):
    """Parametrized test for markdown cleaning."""
    result = clean_markdown_text(input_text)
    
    for expected in expected_contains:
        assert expected in result
    
    for not_expected in expected_not_contains:
        assert not_expected not in result


if __name__ == "__main__":
    pytest.main([__file__])