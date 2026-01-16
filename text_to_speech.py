#!/usr/bin/env python3
"""
Text-to-Speech script using MLX Audio library.
Converts text files (including Markdown) to audio using chunking for long texts.
Optimized for Apple Silicon (M1/M2/M3/M4 Macs).
"""

import argparse
import sys
import os
import re
import tempfile
from pathlib import Path
from typing import List
import numpy as np
import soundfile as sf
from pydub import AudioSegment
from mlx_audio.tts.utils import load_model


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
    
    # Remove markdown images completely (including alt text)
    text = re.sub(r'!\[([^\]]*)\]\([^\)]+\)', '', text)
    
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


def list_available_voices():
    """List all available voices organized by language."""
    voices = {
        'American English (lang: a)': {
            'Female': ['af_alloy', 'af_aoede', 'af_bella', 'af_heart', 'af_jessica', 
                      'af_kore', 'af_nicole', 'af_nova', 'af_river', 'af_sarah', 'af_sky'],
            'Male': ['am_adam', 'am_echo', 'am_eric', 'am_fenrir', 'am_liam', 
                    'am_michael', 'am_onyx', 'am_puck', 'am_santa']
        },
        'British English (lang: b)': {
            'Female': ['bf_alice', 'bf_emma', 'bf_isabella', 'bf_lily'],
            'Male': ['bm_daniel', 'bm_fable', 'bm_george', 'bm_lewis']
        },
        'Spanish (lang: e)': {
            'Female': ['ef_dora'],
            'Male': ['em_alex', 'em_santa']
        },
        'French (lang: f)': {
            'Female': ['ff_siwis'],
            'Male': []
        },
        'Hindi (lang: h)': {
            'Female': ['hf_alpha', 'hf_beta'],
            'Male': ['hm_omega', 'hm_psi']
        },
        'Italian (lang: i)': {
            'Female': ['if_sara'],
            'Male': ['im_nicola']
        },
        'Japanese (lang: j)': {
            'Female': ['jf_alpha', 'jf_gongitsune', 'jf_nezumi', 'jf_tebukuro'],
            'Male': ['jm_kumo']
        },
        'Portuguese (lang: p)': {
            'Female': ['pf_dora'],
            'Male': ['pm_alex', 'pm_santa']
        },
        'Chinese (lang: z)': {
            'Female': ['zf_xiaobei', 'zf_xiaoni', 'zf_xiaoxiao', 'zf_xiaoyi'],
            'Male': ['zm_yunjian', 'zm_yunxi', 'zm_yunxia', 'zm_yunyang']
        }
    }
    
    print("Available Voices by Language:\n")
    for language, genders in voices.items():
        print(f"🗣️  {language}")
        for gender, voice_list in genders.items():
            if voice_list:
                print(f"   {gender}: {', '.join(voice_list)}")
        print()
    
    print("Usage: python text_to_speech.py document.txt --voice VOICE_NAME --lang LANG_CODE")
    print("Example: python text_to_speech.py document.txt --voice af_bella --lang a")


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
        description="Convert text files to speech using MLX Audio (Apple Silicon optimized)",
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
        nargs="?",
        help="Input text file (supports .txt, .md, and other text formats). Use '-' or omit to read from stdin."
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
        "--keep-temp",
        action="store_true",
        help="Keep temporary audio chunk files for debugging"
    )
    
    parser.add_argument(
        "--list-voices",
        action="store_true",
        help="List all available voices and exit"
    )
    
    parser.add_argument(
        "--markdown", "-m",
        action="store_true",
        help="Treat input as markdown and clean formatting (auto-detected for .md/.markdown files)"
    )
    
    args = parser.parse_args()
    
    # Handle --list-voices option
    if args.list_voices:
        list_available_voices()
        sys.exit(0)

    # Determine if reading from stdin
    reading_from_stdin = not args.input_file or args.input_file == '-'
    
    # Validate input
    if not reading_from_stdin and not os.path.exists(args.input_file):
        print(f"Error: Input file '{args.input_file}' not found.", file=sys.stderr)
        sys.exit(1)
    
    # Check if stdin has data when reading from stdin
    if reading_from_stdin and sys.stdin.isatty():
        print("Error: No input provided. Specify a file or pipe text to stdin.", file=sys.stderr)
        print("Examples:")
        print("  python text_to_speech.py document.txt")
        print("  echo 'Hello world' | python text_to_speech.py")
        print("  head -n 10 document.txt | python text_to_speech.py")
        sys.exit(1)
    
    # Determine output path
    if args.output:
        output_path = args.output
    elif reading_from_stdin:
        output_path = "output.wav"
    else:
        input_path = Path(args.input_file)
        output_path = str(input_path.with_suffix('.wav'))
    
    try:
        # Read and clean the text
        if reading_from_stdin:
            print("Reading from stdin...")
            text = sys.stdin.read()
            is_markdown = args.markdown
        else:
            print("Reading input file...")
            text = read_text_file(args.input_file)
            is_markdown = args.markdown or args.input_file.lower().endswith(('.md', '.markdown'))
        
        # Clean markdown if specified or auto-detected
        if is_markdown:
            print("Cleaning markdown formatting...")
            text = clean_markdown_text(text)
        
        if not text.strip():
            print("Error: Input file is empty or contains no readable text.", file=sys.stderr)
            sys.exit(1)
        
        print(f"Text length: {len(text)} characters")

        # Initialize MLX Audio model (Apple Silicon optimized)
        print("Initializing MLX Audio TTS model...")
        model = load_model("mlx-community/Kokoro-82M-bf16")
        print("Model loaded on MLX (Apple Silicon)")

        # Create temporary directory for audio chunks
        temp_dir = tempfile.mkdtemp(prefix="kokoro_tts_")
        temp_files = []

        try:
            # Generate audio chunks
            print("Generating audio chunks...")
            chunk_count = 0

            for result in model.generate(
                text=text,
                voice=args.voice,
                speed=args.speed,
                lang_code=args.lang
            ):
                if result.audio is not None:
                    # Save chunk to temporary file
                    chunk_path = os.path.join(temp_dir, f"chunk_{chunk_count:04d}.wav")
                    sf.write(chunk_path, np.array(result.audio), 24000)
                    temp_files.append(chunk_path)
                    chunk_count += 1
                    print(f"Generated chunk {chunk_count}")
            
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