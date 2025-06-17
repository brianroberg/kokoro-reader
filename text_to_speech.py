#!/usr/bin/env python3
"""
Text-to-Speech script using Kokoro TTS library.
Converts text files (including Markdown) to audio using chunking for long texts.
"""

import argparse
import os
import sys
import re
import tempfile
from pathlib import Path
from typing import List, Optional
import torch
import soundfile as sf
from pydub import AudioSegment
from kokoro import KPipeline


def read_text_file(file_path: str) -> str:
    """Read text from file, handling various encodings."""
    encodings = ['utf-8', 'utf-16', 'latin-1', 'ascii']
    
    for encoding in encodings:
        try:
            with open(file_path, 'r', encoding=encoding) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    
    raise ValueError(f"Could not decode file {file_path} with any supported encoding")


def clean_markdown_text(text: str) -> str:
    """Clean markdown formatting from text for better TTS."""
    # Remove markdown headers
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    
    # Remove markdown links but keep the text
    text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
    
    # Remove markdown emphasis (bold/italic)
    text = re.sub(r'\*{1,2}([^*]+)\*{1,2}', r'\1', text)
    text = re.sub(r'_{1,2}([^_]+)_{1,2}', r'\1', text)
    
    # Remove code blocks and inline code
    text = re.sub(r'```[^`]*```', '', text, flags=re.DOTALL)
    text = re.sub(r'`([^`]+)`', r'\1', text)
    
    # Remove markdown lists
    text = re.sub(r'^\s*[-*+]\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*\d+\.\s+', '', text, flags=re.MULTILINE)
    
    # Remove blockquotes
    text = re.sub(r'^\s*>\s+', '', text, flags=re.MULTILINE)
    
    # Clean up excessive whitespace
    text = re.sub(r'\n\s*\n', '\n\n', text)
    text = re.sub(r' +', ' ', text)
    
    return text.strip()


def concatenate_audio_files(audio_files: List[str], output_path: str) -> None:
    """Concatenate multiple audio files into one."""
    if not audio_files:
        raise ValueError("No audio files to concatenate")
    
    combined = AudioSegment.empty()
    
    for audio_file in audio_files:
        audio = AudioSegment.from_wav(audio_file)
        combined += audio
        # Add small pause between chunks
        combined += AudioSegment.silent(duration=300)  # 300ms pause
    
    combined.export(output_path, format="wav")


def main():
    parser = argparse.ArgumentParser(
        description="Convert text files to speech using Kokoro TTS",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python text_to_speech.py document.txt
  python text_to_speech.py README.md --voice af_bella --output my_audio.wav
  python text_to_speech.py --help
        """
    )
    
    parser.add_argument(
        "input_file",
        help="Input text file (supports .txt, .md, and other text formats)"
    )
    
    parser.add_argument(
        "--output", "-o",
        help="Output audio file path (default: input_filename.wav)"
    )
    
    parser.add_argument(
        "--voice", "-v",
        default="af_heart",
        help="Voice to use for TTS (default: af_heart)"
    )
    
    parser.add_argument(
        "--speed", "-s",
        type=float,
        default=1.0,
        help="Speech speed multiplier (default: 1.0)"
    )
    
    parser.add_argument(
        "--lang", "-l",
        default="a",
        help="Language code (a=American English, b=British English, etc.)"
    )
    
    parser.add_argument(
        "--device",
        choices=["auto", "cpu", "cuda", "mps"],
        default="auto",
        help="Device to use for inference (default: auto)"
    )
    
    parser.add_argument(
        "--keep-temp",
        action="store_true",
        help="Keep temporary audio chunk files for debugging"
    )
    
    args = parser.parse_args()
    
    # Set environment variable for Apple Silicon MPS support
    if args.device in ["auto", "mps"]:
        os.environ['PYTORCH_ENABLE_MPS_FALLBACK'] = '1'
    
    # Validate input file
    if not os.path.exists(args.input_file):
        print(f"Error: Input file '{args.input_file}' not found.", file=sys.stderr)
        sys.exit(1)
    
    # Determine output path
    if args.output:
        output_path = args.output
    else:
        input_path = Path(args.input_file)
        output_path = str(input_path.with_suffix('.wav'))
    
    try:
        # Read and clean the text
        print("Reading input file...")
        text = read_text_file(args.input_file)
        
        # Clean markdown if the file appears to be markdown
        if args.input_file.lower().endswith(('.md', '.markdown')):
            print("Cleaning markdown formatting...")
            text = clean_markdown_text(text)
        
        if not text.strip():
            print("Error: Input file is empty or contains no readable text.", file=sys.stderr)
            sys.exit(1)
        
        print(f"Text length: {len(text)} characters")
        
        # Initialize Kokoro pipeline
        print("Initializing Kokoro TTS pipeline...")
        device = None if args.device == "auto" else args.device
        pipeline = KPipeline(lang_code=args.lang, device=device)
        
        # Create temporary directory for audio chunks
        temp_dir = tempfile.mkdtemp(prefix="kokoro_tts_")
        temp_files = []
        
        try:
            print("Generating audio chunks...")
            chunk_count = 0
            
            # Generate audio using pipeline's built-in chunking
            for result in pipeline(text, voice=args.voice, speed=args.speed):
                if result.audio is not None:
                    # Save chunk to temporary file
                    chunk_path = os.path.join(temp_dir, f"chunk_{chunk_count:04d}.wav")
                    sf.write(chunk_path, result.audio.numpy(), 24000)
                    temp_files.append(chunk_path)
                    chunk_count += 1
                    print(f"Generated chunk {chunk_count}: {len(result.graphemes)} chars")
            
            if not temp_files:
                print("Error: No audio was generated.", file=sys.stderr)
                sys.exit(1)
            
            print(f"Generated {len(temp_files)} audio chunks")
            
            # Concatenate all chunks
            print("Concatenating audio chunks...")
            concatenate_audio_files(temp_files, output_path)
            
            print(f"Audio saved to: {output_path}")
            
        finally:
            # Clean up temporary files unless requested to keep them
            if not args.keep_temp:
                for temp_file in temp_files:
                    try:
                        os.unlink(temp_file)
                    except OSError:
                        pass
                try:
                    os.rmdir(temp_dir)
                except OSError:
                    pass
            else:
                print(f"Temporary files kept in: {temp_dir}")
    
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()