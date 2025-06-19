#!/usr/bin/env python3
"""
Text-to-Speech script using Kokoro TTS library.
Converts text files (including Markdown) to audio using chunking for long texts.
"""

import os
# Set MPS fallback BEFORE importing torch-related modules
os.environ.setdefault('PYTORCH_ENABLE_MPS_FALLBACK', '1')

import argparse
import sys
import re
import tempfile
import warnings
from pathlib import Path
from typing import List
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
        nargs="?",
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
    
    parser.add_argument(
        "--list-voices",
        action="store_true",
        help="List all available voices and exit"
    )
    
    args = parser.parse_args()
    
    # Handle --list-voices option
    if args.list_voices:
        list_available_voices()
        sys.exit(0)
    
    # Validate input file is provided
    if not args.input_file:
        print("Error: Input file is required (unless using --list-voices)", file=sys.stderr)
        parser.print_help()
        sys.exit(1)
    
    # Note: MPS fallback environment variable is set at the top of the script
    
    # Validate input file exists
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
        
        # Determine the best device to use
        if args.device == "auto":
            import torch
            if torch.cuda.is_available():
                device = 'cuda'
                print("Auto-selected CUDA for GPU acceleration")
            elif torch.backends.mps.is_available() and os.environ.get('PYTORCH_ENABLE_MPS_FALLBACK') == '1':
                device = 'mps'
                print("Auto-selected MPS for Apple Silicon acceleration")
            else:
                device = 'cpu'
                print("Auto-selected CPU (no GPU acceleration available)")
        else:
            device = args.device
            print(f"Using explicitly specified device: {device}")
        
        # Suppress warnings from the Kokoro library
        warnings.filterwarnings("ignore", category=UserWarning, module="torch.nn.modules.rnn")
        warnings.filterwarnings("ignore", category=FutureWarning, module="torch.nn.utils.weight_norm")
        
        pipeline = KPipeline(lang_code=args.lang, device=device, repo_id='hexgrad/Kokoro-82M')
        
        # Debug: Check what device is actually being used
        if pipeline.model:
            actual_device = next(pipeline.model.parameters()).device
            print(f"Model loaded on device: {actual_device}")
            
            # Check MPS availability and settings
            import torch
            print(f"MPS available: {torch.backends.mps.is_available()}")
            print(f"MPS fallback enabled: {os.environ.get('PYTORCH_ENABLE_MPS_FALLBACK', 'Not set')}")
            if hasattr(torch.backends.mps, 'is_built'):
                print(f"MPS built: {torch.backends.mps.is_built()}")
        else:
            print("No model loaded (quiet pipeline)")
        
        # Create temporary directory for audio chunks
        temp_dir = tempfile.mkdtemp(prefix="kokoro_tts_")
        temp_files = []
        
        try:
            # First pass: count total chunks without generating audio
            print("Analyzing text and counting chunks...")
            quiet_pipeline = KPipeline(lang_code=args.lang, model=False, repo_id='hexgrad/Kokoro-82M')
            total_chunks = sum(1 for _ in quiet_pipeline(text, voice=None, speed=args.speed))
            
            print(f"Generating {total_chunks} audio chunks...")
            chunk_count = 0
            
            # Second pass: generate audio using pipeline's built-in chunking
            for result in pipeline(text, voice=args.voice, speed=args.speed):
                if result.audio is not None:
                    # Save chunk to temporary file
                    chunk_path = os.path.join(temp_dir, f"chunk_{chunk_count:04d}.wav")
                    sf.write(chunk_path, result.audio.numpy(), 24000)
                    temp_files.append(chunk_path)
                    chunk_count += 1
                    progress = (chunk_count / total_chunks) * 100
                    print(f"Generated chunk {chunk_count}/{total_chunks} ({progress:.1f}%): {len(result.graphemes)} chars")
            
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